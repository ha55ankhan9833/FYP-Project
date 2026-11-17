"""
Grading reliability, Ollama failure handling (mocked HTTP), teacher review
flow, and isolation of the developer-only synthetic harness.
No database writes, no C++ execution, no real Ollama calls here.
"""

import ast
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import requests

from tests.fakes import BACKEND, FakeConnection

import app as app_module
import evaluation_review_service as review
import grading_pipeline as gp
import ollama_grading_service as ogs
import secure_execution_gate as gate
from dev_tools import grading_preview_harness as harness
from equal_weight_test_scoring import calculate_equal_weight_score
from grading_rubric_allocation_adapter import combine_grading_results
from rubric_ai_grader import (RubricGradingUnavailable, build_llm_evidence,
                              grade_rubric_with_ollama)

PREVIEW = json.loads((Path(BACKEND) / "fixtures/new_assessment_csc101/"
                      "oracle_verification.json").read_text())["assessment_5_preview"]
RUBRIC = [{"criterion_name": "Logic & Algorithm", "max_marks": Decimal("3")},
          {"criterion_name": "Code Quality", "max_marks": Decimal("2")}]
CONTEXT = {"problem": "p", "input_format": "i", "output_format": "o",
           "constraints": "1 <= N <= 1000000"}


class SyntheticScenarioTests(unittest.TestCase):
    def test_models_give_declared_counts_on_assessment5_inputs(self):
        for name, (fixture, model, expected) in harness.SCENARIOS.items():
            passed = sum(model(int(c["input_data"])) == c["expected_output"]
                         for c in PREVIEW)
            self.assertEqual(passed, expected, name)
            self.assertTrue((harness.SUBMISSIONS / fixture).is_file())

    def test_scenario_test_scores(self):
        # 10/10, 8/10, 6/10, 0/10 on the 10-case set
        expected = {"all_pass": "5.00", "most_pass": "4.00",
                    "half_range": "3.00", "none_pass": "0.00"}
        for name, score in expected.items():
            passes = harness.SCENARIOS[name][2]
            self.assertEqual(calculate_equal_weight_score(passes, 10, "5.00")
                             ["test_score"], score)

    def test_harness_proposed_set_comes_from_migration_plan(self):
        plan = json.loads(harness.MIGRATION_PLAN.read_text())
        self.assertEqual(len(plan["proposed_10"]), 10)
        self.assertEqual([p["input_data"] for p in plan["proposed_10"]],
                         [c["input_data"] for c in PREVIEW])
        self.assertIn("IN MEMORY ONLY", harness.PROPOSED_LABEL)

    def test_totals_bounded_for_every_rubric_award(self):
        config = {"total_marks": "10", "validation_percentage": "50"}
        for passes in (10, 8, 6, 0):
            for logic in ("0", "1.5", "3"):
                for quality in ("0", "1", "2"):
                    r = combine_grading_results(
                        config, RUBRIC,
                        {"criteria": [{"criterion_name": "Logic & Algorithm",
                                       "rating": logic},
                                      {"criterion_name": "Code Quality",
                                       "rating": quality}]},
                        passes, 10)
                    self.assertTrue(Decimal("0") <= Decimal(r["rubric_score"])
                                    <= Decimal("5"))
                    self.assertTrue(Decimal("0") <= Decimal(r["suggested_score"])
                                    <= Decimal("10"))

    def test_fixtures_are_marked_synthetic(self):
        for path in harness.SUBMISSIONS.glob("*.cpp"):
            self.assertIn("SYNTHETIC STUDENT SUBMISSION",
                          path.read_text(encoding="utf-8").splitlines()[0])

    def test_developer_header_never_reaches_the_model(self):
        for path in harness.SUBMISSIONS.glob("*.cpp"):
            code, removed = harness.strip_developer_header(
                path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(removed, 2)
            self.assertTrue(code.lstrip().startswith("#include"))
            for leak in ("SYNTHETIC", "Intended behaviour", "never compiled"):
                self.assertNotIn(leak, code)
        with self.assertRaises(RuntimeError):
            harness.strip_developer_header("int main(){}\n")


class ReadOnlyGuardTests(unittest.TestCase):
    def conn(self):
        return harness.ReadOnlyConnection(
            FakeConnection(lambda sql, p: ([(1,)], -1)))

    def test_select_allowed(self):
        cur = self.conn().cursor()
        cur.execute("SELECT 1")
        cur.execute("  ;WITH x AS (SELECT 1 AS a) SELECT a FROM x")

    def test_writes_and_commit_blocked(self):
        c = self.conn()
        for sql in ("INSERT INTO dbo.Evaluation VALUES (1)",
                    "update dbo.GradingJob set x=1", "DELETE FROM dbo.TestCase",
                    "MERGE dbo.X", "EXEC sp_who", "CREATE TABLE t (a int)",
                    "DROP TABLE dbo.TestCase", "SET NOEXEC ON"):
            with self.subTest(sql=sql), self.assertRaises(harness.ReadOnlyViolation):
                c.cursor().execute(sql)
        with self.assertRaises(harness.ReadOnlyViolation):
            c.commit()
        with self.assertRaises(harness.ReadOnlyViolation):
            c.autocommit = True


class HarnessIsolationTests(unittest.TestCase):
    def test_no_application_module_imports_dev_tools(self):
        for path in Path(BACKEND).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom)
                         else [])
                self.assertFalse(any(n.startswith("dev_tools") for n in names),
                                 path.name)

    def test_no_route_exposes_synthetic_grading(self):
        for rule in app_module.app.url_map.iter_rules():
            for word in ("synthetic", "harness", "evidence", "dev"):
                self.assertNotIn(word, rule.rule.lower())

    def test_synthetic_sandbox_is_never_registered(self):
        gate.register_secure_sandbox(None)
        self.assertIsNone(gate.get_secure_sandbox())
        harness.SyntheticEvidenceSandbox(harness.model_all_pass)
        self.assertIsNone(gate.get_secure_sandbox())

    def test_production_worker_still_refuses_without_sandbox(self):
        claim = MagicMock()
        deps = gp.GradingDependencies(
            claim=claim, mark_exhausted=lambda: [],
            get_sandbox=gate.get_secure_sandbox)
        result = gp.run_worker_once("w1", deps)
        self.assertEqual(result["outcome"], "SECURE_EXECUTION_DISABLED")
        claim.assert_not_called()

    def test_llm_is_told_evidence_is_synthetic(self):
        ev = build_llm_evidence({"tests_run": 15, "tests_passed": 12,
                                 "cases": [], "synthetic": True})
        self.assertEqual(ev["evidence_type"], "synthetic_development_fixture")
        real = build_llm_evidence({"tests_run": 15, "tests_passed": 12, "cases": []})
        self.assertEqual(real["evidence_type"], "sandbox_execution_results")


def ollama_reply(content, status=200):
    response = MagicMock()
    response.status_code = status
    response.raise_for_status = MagicMock()
    response.json.return_value = {"message": {"content": content}}
    return response


def criterion_only(logic=2, quality=2, **extra):
    """Model reply in the NEW criterion-only contract (no total)."""
    body = {"criteria": [{"criterion_name": "Logic & Algorithm", "mark": logic,
                          "explanation": "traces d * d < n"},
                         {"criterion_name": "Code Quality", "mark": quality,
                          "explanation": "readable"}],
            "overall_feedback": "ok",
            "issues": ["d * d < n misses squares"],
            "improvement_suggestions": ["use d * d <= n"]}
    body.update(extra)
    return body


def legacy(logic=2, quality=2, total=None):
    """Model reply in the LEGACY contract (model reports a total)."""
    return json.dumps({
        "suggested_score": logic + quality if total is None else total,
        "overall_feedback": "ok",
        "criteria": [{"criterion_name": "Logic & Algorithm", "rating": logic,
                      "explanation": "x"},
                     {"criterion_name": "Code Quality", "rating": quality,
                      "explanation": "y"}],
        "deductions": [], "improvement_suggestions": []})


LEGACY_RUBRIC = [{"criterion_name": "Logic & Algorithm", "max_marks": "3"},
                 {"criterion_name": "Code Quality", "max_marks": "2"}]


class CriterionOnlyContractTests(unittest.TestCase):
    """grade_rubric_with_ollama -> real grade_rubric_criteria, mocked HTTP."""

    def grade(self, side_effect=None, body=None, raw=None):
        reply = None if side_effect else ollama_reply(
            raw if raw is not None else json.dumps(body))
        with patch.object(ogs.requests, "post", side_effect=side_effect,
                          return_value=reply) as post:
            try:
                return grade_rubric_with_ollama(
                    CONTEXT, RUBRIC, Decimal("5"), "int main(){}",
                    {"tests_run": 10, "tests_passed": 8, "cases": []}), post
            except Exception as exc:
                return exc, post

    def test_prompt_and_payload_never_ask_for_a_total(self):
        r, post = self.grade(body=criterion_only(2, 1.5))
        sent = post.call_args.kwargs["json"]
        system = sent["messages"][0]["content"]
        payload = json.loads(sent["messages"][1]["content"])
        self.assertEqual(sent["model"], "qwen2.5-coder:3b")
        self.assertIn("Do NOT calculate or output any total", system)
        self.assertNotIn("maximum_marks", payload)
        self.assertNotIn("suggested_score", system)
        self.assertEqual(r["suggested_score"], "3.50")        # Python: 2 + 1.5
        self.assertEqual(r["contract"], "criterion_only_v1.1")

    def test_valid_edge_marks(self):
        for logic, quality, total in ((0, 0, "0.00"), (3, 2, "5.00"),
                                      (2.75, 0.25, "3.00"), (1, 2.0, "3.00")):
            with self.subTest(logic=logic, quality=quality):
                r, _ = self.grade(body=criterion_only(logic, quality))
                self.assertEqual(r["suggested_score"], total)

    def test_model_total_is_ignored(self):
        r, _ = self.grade(body=criterion_only(2, 2, suggested_score=3,
                                              total=99))
        self.assertEqual(r["suggested_score"], "4.00")
        self.assertEqual(r["ignored_model_fields"], ["suggested_score", "total"])

    def test_order_independent(self):
        body = criterion_only(1, 2)
        body["criteria"].reverse()
        r, _ = self.grade(body=body)
        self.assertEqual([c["criterion_name"] for c in r["criteria"]],
                         ["Logic & Algorithm", "Code Quality"])

    def test_every_invalid_response_is_rejected(self):
        two = criterion_only()["criteria"]
        cases = {
            "invalid json": dict(raw="not json {"),
            "not an object": dict(raw="[1, 2]"),
            "over maximum": dict(body=criterion_only(3.01, 1)),
            "negative": dict(body=criterion_only(-0.5, 1)),
            "string mark": dict(body=criterion_only("2", 1)),
            "boolean mark": dict(body=criterion_only(True, 1)),
            "null mark": dict(body=criterion_only(None, 1)),
            "three decimals": dict(body=criterion_only(1.255, 1)),
            "missing criterion": dict(body={**criterion_only(), "criteria": two[:1]}),
            "duplicate criterion": dict(body={**criterion_only(),
                                              "criteria": [two[0], two[0]]}),
            "unknown criterion": dict(body={**criterion_only(), "criteria": [
                two[0], {**two[1], "criterion_name": "Style"}]}),
            "missing mark": dict(body={**criterion_only(), "criteria": [
                two[0], {k: v for k, v in two[1].items() if k != "mark"}]}),
            "old rating key": dict(body={**criterion_only(), "criteria": [
                two[0], {"criterion_name": "Code Quality", "rating": 1,
                         "explanation": "y"}]}),
            "empty explanation": dict(body={**criterion_only(), "criteria": [
                two[0], {**two[1], "explanation": "  "}]}),
            "missing explanation": dict(body={**criterion_only(), "criteria": [
                two[0], {k: v for k, v in two[1].items() if k != "explanation"}]}),
            "issues not text": dict(body=criterion_only(issues=[{"a": 1}])),
            "missing issues": dict(body={k: v for k, v in criterion_only().items()
                                         if k != "issues"}),
            "feedback not text": dict(body=criterion_only(overall_feedback=5)),
            "timeout": dict(side_effect=requests.Timeout("read timed out")),
            "unavailable": dict(side_effect=requests.ConnectionError("refused")),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                result, _ = self.grade(**kwargs)
                self.assertIsInstance(result, RubricGradingUnavailable, label)


class LegacyContractTests(unittest.TestCase):
    """grade_cpp_submission keeps the old contract and its strict total check."""

    def legacy(self, reply):
        with patch.object(ogs.requests, "post", return_value=ollama_reply(reply)):
            return ogs.grade_cpp_submission("p", "i", "o", "c", LEGACY_RUBRIC,
                                            "int main(){}", "5")

    def test_consistent_legacy_total_accepted(self):
        self.assertEqual(self.legacy(legacy(2, 2))["suggested_score"], 4.0)

    def test_inconsistent_legacy_total_still_rejected(self):
        for total in (3, 5, 4.01):
            with self.subTest(total=total), self.assertRaisesRegex(
                    ogs.GradingServiceError, "does not equal"):
                self.legacy(legacy(2, 2, total=total))

    def test_production_rubric_grader_uses_new_contract(self):
        import inspect
        import rubric_ai_grader
        self.assertIs(inspect.signature(grade_rubric_with_ollama)
                      .parameters["grader"].default, ogs.grade_rubric_criteria)
        self.assertNotIn("grade_cpp_submission", inspect.getsource(rubric_ai_grader))

    def test_pipeline_never_zeroes_on_ai_failure(self):
        released, completed = [], []
        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": 1,
                                "source_code": "int main(){}"},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("50")},
            load_context=lambda a: CONTEXT,
            load_rubric=lambda a: RUBRIC,
            load_test_cases=lambda a: [{"test_number": c["test_number"],
                                        "input_data": c["input_data"],
                                        "expected_output": c["expected_output"]}
                                       for c in PREVIEW],
            get_sandbox=lambda: harness.SyntheticEvidenceSandbox(
                harness.model_square_bug),
            grade_rubric=lambda *a: (_ for _ in ()).throw(
                RubricGradingUnavailable("Suggested score does not equal "
                                         "rubric marks total")),
            complete=lambda *a, **k: completed.append(a),
            complete_compilation_failure=lambda *a: completed.append(a),
            release=lambda *a: released.append(a) or {"job_status": "PENDING"},
            allow_synthetic_evidence=True)     # synthetic sandbox, dev-style
        r = gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                    "claim_token": str(uuid4())}, "w", deps)
        self.assertEqual(r["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertFalse(completed)
        self.assertEqual(len(released), 1)


class TeacherReviewFlowTests(unittest.TestCase):
    """Suggested result -> teacher review -> finalize -> student view."""

    def setUp(self):
        self.store = {"result_status": None, "final_score": None,
                      "instructor_feedback": None}

    def conn(self):
        store = self.store

        def responder(sql, params):
            owner_ok = len(params) > 1 and params[1] == "BIIT235"
            if sql.startswith("SELECT s.submission_id, s.student_id, s.file_name"):
                return ([(7, "S1", "a.cpp", "int main(){}", None, None, 5,
                          "Prime Number Check", "10.00", "50.00", None, False,
                          "0.00", "COMPLETED", 1, None)] if owner_ok else []), -1
            if sql.startswith("SELECT suggested_score, auto_evaluation_status"):
                return [("8.00", "COMPLETED", "feedback", store["final_score"],
                         store["result_status"], store["instructor_feedback"],
                         12, 15)], -1
            if "FROM dbo.EvaluationCriterion" in sql:
                return [("Logic & Algorithm", "2.00",
                         "Fails for N = 994009 because d * d < n"),
                        ("Code Quality", "2.00", "y")], -1
            if sql.startswith("SELECT input_data"):
                return [("4", "NO\n"), ("994009", "NO\n"),
                        ("999983", "YES\n")], -1
            if sql.startswith("SELECT a.total_marks, j.job_status"):
                return ([("10.00", "COMPLETED", 5, "50.00")] if owner_ok else []), -1
            if "FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [(store["result_status"], "COMPLETED", 8, 10)], -1
            if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
                return [(1,)], -1
            if "OBJECT_ID(N'dbo.EvaluationCriterionApproval'" in sql:
                return [(0,)], -1
            if "FROM dbo.AssessmentRubricCriterion" in sql:
                return [(1, "Logic & Algorithm", "3.00", None),
                        (2, "Code Quality", "2.00", None)], -1
            if sql.startswith("UPDATE dbo.Evaluation"):
                store.update(final_score=params[0], instructor_feedback=params[1],
                             result_status="FINALIZED")
                return [], 1
            if sql.startswith("SELECT s.submission_id, s.file_name, s.uploaded_at"):
                feedback = ("=== Automated test results ===\nTests passed: 8 / 10\n"
                            "=== AI rubric assessment (provisional) ===\n"
                            "- Logic: 2.00 - wrong for 994009 and 4; 999983 ok")
                return [(7, "a.cpp", None, None, "COMPLETED", 1,
                         store["result_status"], store["final_score"],
                         store["instructor_feedback"], feedback, 8, 10,
                         "10.00", "COMPLETED", "50.00", 5)], -1
            raise AssertionError(sql)
        return FakeConnection(responder)

    def factory(self):
        return lambda: self.conn()

    def test_full_review_flow(self):
        teacher_view = review.get_teacher_submission_evaluation(
            7, "BIIT235", connection_factory=self.factory())
        self.assertEqual(teacher_view["evaluation"]["suggested_score"], "8.00")
        self.assertEqual(teacher_view["grading_status"],
                         "AUTO_GRADED_AWAITING_REVIEW")

        before = review.get_student_submission(5, "S1",
                                               connection_factory=self.factory())
        self.assertIsNone(before["result"])
        self.assertNotIn("8.00", json.dumps(before))       # suggested hidden

        with self.assertRaises(review.NotFoundError):
            review.finalize_evaluation(7, "BIIT001", 8,
                                       connection_factory=self.factory())
        for bad in (11, -1, "8.005", None, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review.finalize_evaluation(7, "BIIT235", bad,
                                           connection_factory=self.factory())
        self.assertIsNone(self.store["result_status"])

        done = review.finalize_evaluation(7, "BIIT235", Decimal("7.50"), "Good",
                                          connection_factory=self.factory())
        self.assertEqual(done["final_score"], "7.50")

        after = review.get_student_submission(5, "S1",
                                              connection_factory=self.factory())
        self.assertEqual(after["status"], "RESULT_PUBLISHED")
        self.assertEqual(after["result"]["final_score"], "7.50")
        self.assertEqual(after["result"]["instructor_feedback"], "Good")
        self.assertNotIn("suggested_score", json.dumps(after))
        # The student result is constructed from permitted fields: AI text
        # (which quoted hidden inputs) is not forwarded at all, so nothing
        # needs redacting and no placeholder text appears.
        for hidden in ("994009", "999983", "[hidden test input]", "8.00"):
            self.assertNotIn(hidden, json.dumps(after))
        result = after["result"]
        self.assertNotIn("automated_feedback", result)
        self.assertEqual((result["tests_passed"], result["tests_run"],
                          result["test_marks"]), (8, 10, "4.00"))
        # 4.00 tests + AI 2.00 + 2.00 = 8.00 != teacher 7.50: the AI
        # criteria are withheld; the approved rubric total is 3.50.
        self.assertEqual((result["rubric_source"], result["criteria"],
                          result["rubric_marks"]),
                         ("FINAL_TOTAL_ONLY", [], "3.50"))
        self.assertEqual(result["summary"][0], "8 of 10 hidden tests passed.")

        with self.assertRaises(review.ConflictError):
            review.finalize_evaluation(7, "BIIT235", 9,
                                       connection_factory=self.factory())

    def test_other_teacher_cannot_review(self):
        with self.assertRaises(review.NotFoundError):
            review.get_teacher_submission_evaluation(
                7, "BIIT001", connection_factory=self.factory())


if __name__ == "__main__":
    unittest.main()
