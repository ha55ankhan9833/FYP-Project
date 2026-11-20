"""
Proposed CSC-101 payloads: static checks AND acceptance by the real
Flask route validation (database replaced by an in-memory fake).
No real database, HTTP server, compiler or C++ execution.
"""

import hashlib
import json
import unittest
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch

from tests.fakes import BACKEND, FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module
import assessment_rubric_service as rubric_service
import assessment_section_service as section_service
import reference_solution_service
import submission_compilation_orchestrator
import test_case_generation_orchestrator as orch
from assessment_rubric_service import normalize_rubric_input
from equal_weight_test_scoring import calculate_equal_weight_score
from grading_allocation import split_allocation
from grading_rubric_allocation_adapter import combine_grading_results
from test_case_approval_validator import validate_assessment_approval
from test_case_generation_service import load_trusted_reference_hashes
from test_case_validator import validate_generated_test_cases

DIR = Path(BACKEND) / "fixtures" / "new_assessment_csc101"
CONSTRAINT = "1 <= N <= 1000000"


def load(name):
    return json.loads((DIR / name).read_text(encoding="utf-8"))


def body(name):
    return load(name)["body"]


flask_app = app_module.app
flask_app.config["TESTING"] = True


def headers(identity="BIIT235", role="teacher"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity,
                                  additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


FINAL_DRAFT = {
    "draft_id": "d-1", "finalized": True,
    "approval": {"value": True, "code": True},
    "constraints": [
        {"id": "a", "category": "value", "text": "1 <= N <= 10^6",
         "review_status": "REJECTED"},
        {"id": "b", "category": "value", "text": CONSTRAINT,
         "review_status": "ACCEPTED"},
        {"id": "c", "category": "code", "text": "",          # empty AI item
         "review_status": "REJECTED"}]}


class StaticPayloadTests(unittest.TestCase):
    def test_request_files_have_only_method_path_body_notes(self):
        for name in ("01_draft_create.json", "03_assessment_create.json",
                     "04_rubric.json", "05_reference_solution.json",
                     "06_reference_approve.json", "07_generate_test_cases.json",
                     "08_section_assignment.json"):
            item = load(name)
            self.assertLessEqual(set(item), {"method", "path", "body", "notes"})
            self.assertFalse(any(k.startswith("_") for k in item["body"]))

    def test_assessment_values(self):
        a = body("03_assessment_create.json")
        self.assertNotIn("value_constraints", a)
        self.assertNotIn("code_constraints", a)
        self.assertEqual(a["course_code"], "CSC-101")
        self.assertIsNone(datetime.fromisoformat(a["deadline_at"]).tzinfo)
        self.assertEqual(split_allocation(a["total_marks"],
                                          a["validation_percentage"]),
                         (Decimal("5.00"), Decimal("5.00")))

    def test_rubric_valid_for_allocation(self):
        out = normalize_rubric_input(body("04_rubric.json")["criteria"],
                                     Decimal("5.00"))
        self.assertEqual(sum(c["max_marks"] for c in out), Decimal("5"))

    def test_reference_hash_matches_fixture_but_is_not_host_approved(self):
        source = body("05_reference_solution.json")["source_code"]
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        fixture = (Path(BACKEND) / "fixtures/reference_solutions/"
                   "prime_check_reference.cpp").read_bytes()
        self.assertEqual(digest, hashlib.sha256(fixture).hexdigest())
        self.assertEqual(body("06_reference_approve.json")["source_hash"], digest)
        # Owner has NOT approved host execution of this source yet.
        self.assertNotIn(digest, load_trusted_reference_hashes())


class RouteAcceptanceTests(unittest.TestCase):
    """Bodies are sent to the real route handlers; only SQL is faked."""

    def setUp(self):
        self.client = flask_app.test_client()

    def test_assessment_create_body_accepted_and_constraints_from_draft(self):
        payload = dict(body("03_assessment_create.json"), draft_id="d-1")

        def responder(sql, params):
            if "FROM dbo.ConstraintReviewDraft WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [(json.dumps(FINAL_DRAFT), True, None)], -1
            if sql.startswith("INSERT INTO Assessment"):
                return [(99,)], 1
            if sql.startswith("UPDATE dbo.ConstraintReviewDraft"):
                return [], 1
            raise AssertionError(sql)

        conn = FakeConnection(responder)
        with patch.object(app_module, "get_connection", return_value=conn):
            r = self.client.post("/api/assessments", json=payload,
                                 headers=headers())
        self.assertEqual(r.status_code, 201, r.get_json())
        insert = next(p for s, p in conn.executed
                      if s.startswith("INSERT INTO Assessment"))
        self.assertEqual(insert[0], "BIIT235")          # teacher from JWT
        self.assertEqual(insert[11], CONSTRAINT)         # value_constraints
        self.assertEqual(insert[12], "")                 # code_constraints
        self.assertEqual(insert[14], Decimal("50"))
        self.assertEqual(conn.commits, 1)

    def test_assessment_create_rejected_for_other_teachers_draft(self):
        payload = dict(body("03_assessment_create.json"), draft_id="d-1")
        conn = FakeConnection(lambda sql, p: ([], -1))   # draft not found
        with patch.object(app_module, "get_connection", return_value=conn):
            r = self.client.post("/api/assessments", json=payload,
                                 headers=headers("BIIT001"))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(conn.statements("INSERT"))

    def test_rubric_body_accepted(self):
        def responder(sql, params):
            if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
                return [(1,)], -1
            if sql.startswith("SELECT total_marks, validation_percentage"):
                return [("10.00", "50.00")], -1
            if sql.startswith("SELECT COUNT(*) FROM dbo.Submission"):
                return [(0,)], -1
            return [], 1
        conn = FakeConnection(responder)
        real = rubric_service.replace_assessment_rubric
        with patch.object(rubric_service, "replace_assessment_rubric",
                          side_effect=lambda a, t, c: real(
                              a, t, c, connection_factory=lambda: conn)):
            r = self.client.put("/api/assessments/99/rubric",
                                json=body("04_rubric.json"), headers=headers())
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(len(conn.statements(
            "INSERT INTO dbo.AssessmentRubricCriterion")), 2)

    def test_reference_bodies_accepted(self):
        def responder(sql, params):
            if "FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [(99,)], -1
            if "FROM dbo.ReferenceSolution WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [], -1
            if sql.startswith("INSERT INTO dbo.ReferenceSolution"):
                return [(7,)], 1
            raise AssertionError(sql)
        conn = FakeConnection(responder)
        with patch.object(reference_solution_service, "get_connection",
                          return_value=conn):
            r = self.client.post("/api/assessments/99/reference-solution",
                                 json=body("05_reference_solution.json"),
                                 headers=headers())
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["reference_solution"]["source_hash"],
                         body("06_reference_approve.json")["source_hash"])
        self.assertEqual(r.get_json()["reference_solution"]["approval_status"],
                         "PENDING")

    def test_generation_body_accepted_by_route(self):
        with patch.object(orch, "generate_and_save_test_cases",
                          return_value={"success": True}) as m:
            r = self.client.post("/api/assessments/99/generate-test-cases",
                                 json=body("07_generate_test_cases.json"),
                                 headers=headers())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m.call_args.kwargs["additional_inputs"],
                         body("07_generate_test_cases.json")["additional_inputs"])

    def test_generation_refused_for_unapproved_host_reference(self):
        source = body("05_reference_solution.json")["source_code"]
        with self.assertRaises(orch.HostExecutionNotApproved):
            orch._generate_on_host(source, CONSTRAINT, ["3"])

    def test_section_body_accepted(self):
        def responder(sql, params):
            if "FROM dbo.ConstraintReviewDraft" in sql:
                return [(json.dumps(FINAL_DRAFT), True)], -1
            if sql.startswith("SELECT value_constraints, code_constraints"):
                return [(CONSTRAINT, "")], -1
            if "FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [(99, "CSC-101", datetime(2026, 12, 15, 23, 59), False,
                         "10.00", "50.00", "Fixture", "Assignment", "Course")], -1
            # Publishing guard: a ready assessment (10 tests, complete rubric).
            if "FROM dbo.TestCase WITH (HOLDLOCK)" in sql:
                return [(i+1,str(i+1),str(i+1),"0.50") for i in range(10)], -1
            if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
                return [(1,)], -1
            if "FROM dbo.AssessmentRubricCriterion WITH (HOLDLOCK)" in sql:
                return [(1, "Logic", "3.00", None), (2, "Quality", "2.00", None)], -1
            if "FROM dbo.Section_Export WITH (HOLDLOCK) WHERE section_id = ?" in sql:
                return [(params[0],)], -1
            if "FROM dbo.SectionCourseTeacher" in sql:
                return [(1 if params == ("SEC000001", "BIIT235", "CSC-101") else 0,)], -1
            if "FROM dbo.AssessmentSection WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [], -1
            if sql.startswith("SELECT section_id FROM dbo.AssessmentSection"):
                return [("SEC000001",)], -1
            if sql.startswith("INSERT INTO dbo.AssessmentSection"):
                return [(1,)], 1
            raise AssertionError(sql)
        conn = FakeConnection(responder)
        real = section_service.assign_sections
        with patch.object(section_service, "assign_sections",
                          side_effect=lambda a, t, s: real(
                              a, t, s, connection_factory=lambda: conn)):
            r = self.client.post("/api/assessments/99/sections",
                                 json=body("08_section_assignment.json"),
                                 headers=headers())
        self.assertEqual(r.status_code, 201, r.get_json())


class ApprovalWorkflowTests(unittest.TestCase):
    def test_rejected_empty_ai_suggestion_does_not_block_generation(self):
        def responder(sql, params):
            if "FROM dbo.ConstraintReviewDraft" in sql:
                return [(json.dumps(FINAL_DRAFT), True)], -1
            return [(CONSTRAINT, "")], -1
        cur = FakeConnection(responder).cursor()
        r = validate_assessment_approval(cur, 99)
        self.assertEqual(r["approved_value_constraints"], CONSTRAINT)
        self.assertEqual(r["approved_code_constraints"], "")

    def test_accepted_empty_constraint_still_rejected(self):
        draft = json.loads(json.dumps(FINAL_DRAFT))
        draft["constraints"][2]["review_status"] = "ACCEPTED"
        def responder(sql, params):
            if "FROM dbo.ConstraintReviewDraft" in sql:
                return [(json.dumps(draft), True)], -1
            return [(CONSTRAINT, "")], -1
        with self.assertRaises(ValueError):
            validate_assessment_approval(FakeConnection(responder).cursor(), 99)


class PlannedTestCaseTests(unittest.TestCase):
    def test_all_10_within_constraints_unique_and_balanced(self):
        cases = load("planned_test_inputs.json")["cases"]
        self.assertEqual(len(cases), 10)
        validate_generated_test_cases(CONSTRAINT, cases)
        outputs = [c["expected_output_python_oracle"] for c in cases]
        self.assertEqual(outputs.count("YES\n"), 4)
        self.assertEqual(outputs.count("NO\n"), 6)

    def test_rounding_policy_for_10_equal_tests(self):
        # Test Score = ROUND_HALF_UP(5 x passed / 10, 2), rounded once.
        for passed in range(11):
            exact = Decimal(5) * passed / Decimal(10)
            expected = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            got = Decimal(calculate_equal_weight_score(passed, 10, "5.00")
                          ["test_score"])
            self.assertEqual(got, expected)
        self.assertEqual(calculate_equal_weight_score(10, 10, "5")["test_score"],
                         "5.00")
        self.assertEqual(calculate_equal_weight_score(1, 10, "5")["test_score"],
                         "0.50")
        self.assertEqual(calculate_equal_weight_score(8, 10, "5")["test_score"],
                         "4.00")

    def test_full_marks_never_exceed_total(self):
        config = {"total_marks": "10", "validation_percentage": "50"}
        rubric = [{"criterion_name": "Logic & Algorithm", "max_marks": "3"},
                  {"criterion_name": "Code Quality", "max_marks": "2"}]
        ai = {"criteria": [{"criterion_name": "Logic & Algorithm", "rating": "3"},
                           {"criterion_name": "Code Quality", "rating": "2"}]}
        self.assertEqual(Decimal(combine_grading_results(
            config, rubric, ai, 10, 10)["suggested_score"]), Decimal("10.00"))


class HostPathIsolationTests(unittest.TestCase):
    def test_unsandboxed_student_compilation_is_disabled(self):
        with self.assertRaises(RuntimeError):
            submission_compilation_orchestrator.process_submission_compilation(
                1, "int main(){}")

    def test_host_execution_modules_only_imported_by_test_generation(self):
        import ast
        allowed = {
            "cpp_runner": {"test_case_pipeline"},
            "cpp_compiler": {"test_case_generation_orchestrator"},
            "cpp_compilation_service": {"submission_compilation_orchestrator",
                                        "test_cpp_compilation_service_mock"},
            "test_case_pipeline": {"test_case_generation_orchestrator"},
            "test_case_generation_orchestrator": {"test_case_routes"},
            "submission_compilation_orchestrator": set(),
        }
        importers = {name: set() for name in allowed}
        for path in Path(BACKEND).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for n in names:
                    if n in importers and path.stem != n:
                        importers[n].add(path.stem)
        for module, expected in allowed.items():
            self.assertEqual(importers[module], expected, module)

    def test_grading_pipeline_has_no_host_execution(self):
        text = (Path(BACKEND) / "grading_pipeline.py").read_text(encoding="utf-8")
        for forbidden in ("cpp_runner", "cpp_compiler", "compile_cpp_source",
                          "subprocess", "run_trusted_cpp"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
