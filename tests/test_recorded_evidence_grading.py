"""
dev_tools/recorded_evidence_grading.py - integration through the REAL
grading pipeline using the REAL recorded trusted-fixture reports.
No database, no compiler, no execution, no real LLM (a parsed fake grader
is used). subprocess and the compiler are patched to fail if touched.
"""

import copy
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import BACKEND

import cpp_compilation_service
import grading_pipeline as gp
import secure_execution_gate as gate
from dev_tools import recorded_evidence_grading as reg
from dev_tools import trusted_execution_harness as teh
from evaluation_review_service import TEACHER_ONLY_MARKER
from ollama_grading_service import parse_criterion_only_response
from rubric_ai_grader import grade_rubric_with_ollama

PLAN = json.loads((Path(BACKEND) / "fixtures/new_assessment_csc101/"
                   "migration_to_10_tests.json").read_text())
TESTS = [{"test_number": p["test_number"], "input_data": p["input_data"],
          "expected_output": p["expected_output"]} for p in PLAN["proposed_10"]]
RUBRIC = [{"criterion_name": "Logic & Algorithm", "max_marks": Decimal("3")},
          {"criterion_name": "Code Quality", "max_marks": Decimal("2")}]
CONTEXT = {"problem": "Prime check", "input_format": "N", "output_format": "YES/NO",
           "constraints": "1 <= N <= 1000000", "code_constraints": []}
ALLOC = Decimal("5.00")

HAVE_REPORTS = all((reg.REPORTS / r["report"]).exists()
                   for r in reg.REVIEWED_RECORDS.values())


def load(key):
    report, digest = reg.read_record(key)
    return report, digest


def fake_grader(logic, quality, cited=None, text="Trial division loop."):
    def grader(**kwargs):
        return parse_criterion_only_response({
            "criteria": [{"criterion_name": "Logic & Algorithm", "mark": logic,
                          "explanation": text, "cited_failed_tests": cited or []},
                         {"criterion_name": "Code Quality", "mark": quality,
                          "explanation": "Readable."}],
            "overall_feedback": "ok", "issues": [],
            "improvement_suggestions": []}, kwargs["rubric"])
    return grader


def fake_build(calls, grader):
    """Same signature as grading_preview_harness.build_dependencies."""
    def build(model, source, record, test_loader=None, sandbox=None,
              evidence_overrides=None, file_name=None, student_id=None):
        def grade_rubric(context, rubric, alloc, src, evidence):
            record["llm_calls"] += 1
            calls["evidence"] = evidence
            calls["source"] = src
            return grade_rubric_with_ollama(context, rubric, alloc, src,
                                            {**evidence, **evidence_overrides},
                                            grader=grader)

        def capture(name):
            def inner(*a, **k):
                record["captured_writes"].append({"step": name, "args": a,
                                                  "kwargs": k})
                return {"job_status": "simulated"}
            return inner

        return gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": -1,
                                "source_code": source, "file_name": file_name,
                                "student_id": student_id},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("50")},
            load_context=lambda a: CONTEXT,
            load_rubric=lambda a: RUBRIC,
            load_test_cases=lambda a: TESTS,
            get_sandbox=lambda: sandbox,
            grade_rubric=grade_rubric,
            complete=capture("complete"),
            complete_compilation_failure=capture("complete_compilation_failure"),
            release=capture("release"),
            allow_synthetic_evidence=True)
    return build


def no_execution():
    """Fail loudly if anything tries to compile or run."""
    boom = AssertionError("compilation/execution attempted")
    return [patch.object(cpp_compilation_service, "compile_cpp_source",
                         side_effect=boom),
            patch("subprocess.run", side_effect=boom),
            patch("subprocess.Popen", side_effect=boom)]


def grade(key, grader, report=None):
    if report is None:
        report, digest = load(key)
    else:
        digest = reg.REVIEWED_RECORDS[key]["report_sha256"]
    calls = {}
    source = teh.fixture_path(key).read_text(encoding="utf-8")
    patches = no_execution()
    for p in patches:
        p.start()
    try:
        result = reg.grade_recorded(key, report, source, {"fixture": key},
                                    build=fake_build(calls, grader))
    finally:
        for p in patches:
            p.stop()
    return result, calls


@unittest.skipUnless(HAVE_REPORTS, "recorded trusted-fixture reports not present")
class RecordVerificationTests(unittest.TestCase):

    def verify(self, key, report=None, digest=None, fixture_sha=None,
               tests=TESTS):
        r, d = load(key)
        return reg.verify_record(
            key, report if report is not None else r, digest or d, tests,
            fixture_sha or teh.fixture_sha256(key), ALLOC)

    def test_real_reports_verify(self):
        correct = self.verify("prime_correct")
        self.assertEqual((correct["tests_passed"], correct["failed_test_numbers"],
                          correct["recorded_test_score"]), (10, [], "5.00"))
        buggy = self.verify("prime_buggy_square")
        self.assertEqual((buggy["tests_passed"], buggy["failed_test_numbers"],
                          buggy["recorded_test_score"]), (8, [6, 9], "4.00"))
        failed = self.verify("prime_compile_error")
        self.assertEqual(failed["compile_classification"],
                         "STUDENT_CODE_COMPILATION_ERROR")
        for summary in (correct, buggy, failed):
            self.assertEqual(summary["provenance"], "trusted_fixture_recorded")

    def test_report_file_hash_pinned(self):
        with self.assertRaisesRegex(reg.RecordIntegrityError, "report file hash"):
            self.verify("prime_correct", digest="0" * 64)

    def test_changed_fixture_file_refused(self):
        with self.assertRaisesRegex(reg.RecordIntegrityError, "fixture file changed"):
            self.verify("prime_correct", fixture_sha="1" * 64)

    def test_tampered_pass_flag_refused(self):
        report, _ = load("prime_buggy_square")
        bad = copy.deepcopy(report)
        bad["tests"][5]["passed"] = True          # N=4 really printed YES
        with self.assertRaisesRegex(reg.RecordIntegrityError, "pass/fail"):
            self.verify("prime_buggy_square", report=bad)

    def test_tampered_stdout_refused(self):
        report, _ = load("prime_correct")
        bad = copy.deepcopy(report)
        bad["tests"][0]["actual"] = "YES"
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify("prime_correct", report=bad)

    def test_changed_database_tests_refused(self):
        changed = copy.deepcopy(TESTS)
        changed[2]["input_data"] = "3"
        with self.assertRaisesRegex(reg.RecordIntegrityError, "input differs"):
            self.verify("prime_correct", tests=changed)

    def test_nine_tests_refused(self):
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify("prime_correct", tests=TESTS[:9])

    def test_report_label_must_be_trusted_fixture(self):
        report, _ = load("prime_correct")
        bad = {**report, "label": "student submission"}
        with self.assertRaisesRegex(reg.RecordIntegrityError, "labelled"):
            self.verify("prime_correct", report=bad)


class CompileClassificationTests(unittest.TestCase):

    def test_located_error_is_student_code(self):
        self.assertEqual(reg.classify_compile_record({
            "status": "COMPILATION_FAILED",
            "diagnostics": "student_solution.cpp:6:19: error: 'x' was not declared"}),
            "STUDENT_CODE_COMPILATION_ERROR")

    def test_infrastructure_statuses(self):
        for record in ({"status": "COMPILER_UNAVAILABLE", "diagnostics": ""},
                       {"status": "COMPILATION_TIMEOUT"},
                       {"status": "COMPILATION_FAILED",
                        "diagnostics": "g++: fatal error: cannot execute 'cc1plus'"},
                       {"status": "COMPILATION_FAILED", "diagnostics": ""},
                       {}, None):
            self.assertEqual(reg.classify_compile_record(record),
                             "INFRASTRUCTURE_FAILURE", record)


@unittest.skipUnless(HAVE_REPORTS, "recorded trusted-fixture reports not present")
class RecordedPipelineTests(unittest.TestCase):

    def test_correct_fixture_graded_from_recorded_evidence(self):
        result, calls = grade("prime_correct", fake_grader(3, 2))
        self.assertEqual(result["outcome"], "GRADED")
        self.assertEqual(result["score"]["test_score"], "5.00")
        self.assertEqual(result["score"]["suggested_score"], "10.00")
        self.assertEqual(calls["evidence"]["provenance"], "trusted_fixture_recorded")
        self.assertFalse(calls["evidence"]["synthetic"])
        self.assertTrue(calls["evidence"]["development_only"])
        self.assertEqual(result["verification_report"]["provenance"],
                         "trusted_fixture_recorded")
        self.assertEqual(result["replay"], {"compile_calls": 1, "run_calls": 1,
                                            "real_compilation": False,
                                            "real_execution": False})
        self.assertEqual(result["llm_calls"], 1)
        self.assertTrue(all(not w["written_to_database"]
                            for w in result["captured_writes"]))
        self.assertIn("NOT a student", result["label"].replace("not a", "NOT a"))

    def test_ai_sees_recorded_execution_evidence_type(self):
        seen = {}

        def grader(**kwargs):
            seen.update(kwargs)
            return fake_grader(2, 2, cited=[6, 9])(**kwargs)

        result, _ = grade("prime_buggy_square", grader)
        evidence = seen["test_evidence"]
        self.assertEqual(evidence["evidence_type"],
                         "trusted_fixture_recorded_execution")
        self.assertEqual(evidence["failed_test_numbers"], [6, 9])
        self.assertEqual(result["score"]["test_score"], "4.00")
        self.assertEqual(result["suggested_evaluation_preview"]["tests_passed"], 8)

    def test_buggy_python_test_marks_ignore_ai(self):
        result, _ = grade("prime_buggy_square", fake_grader(3, 2))
        self.assertEqual(result["score"]["test_score"], "4.00")
        self.assertEqual(result["verification_report"]["tests"]["failed_test_numbers"],
                         [6, 9])

    def test_compile_error_follows_policy_without_ai(self):
        def must_not_run(**kwargs):
            raise AssertionError("Ollama called for a compilation failure")

        result, _ = grade("prime_compile_error", must_not_run)
        self.assertEqual(result["outcome"], "COMPILATION_FAILED")
        self.assertEqual(result["llm_calls"], 0)
        self.assertEqual(result["replay"]["run_calls"], 0)
        preview = result["suggested_evaluation_preview"]
        self.assertEqual((preview["suggested_score"], preview["tests_run"],
                          preview["auto_evaluation_status"]),
                         ("0.00", 0, "COMPILATION_FAILED"))
        self.assertIn("student_solution.cpp:6:19: error:", preview["diagnostics"])
        self.assertNotIn("Users", preview["diagnostics"])

    def test_infrastructure_compile_record_released_not_zeroed(self):
        report, _ = load("prime_compile_error")
        infra = {**report, "compile": {"status": "COMPILER_UNAVAILABLE",
                                       "diagnostics": "", "seconds": 0}}
        result, _ = grade("prime_compile_error", fake_grader(3, 2), report=infra)
        self.assertEqual(result["outcome"], "INFRASTRUCTURE_FAILURE")
        self.assertEqual(result["error_code"], "SANDBOX_COMPILE_FAILURE")
        self.assertEqual(result["llm_calls"], 0)
        self.assertNotIn("suggested_evaluation_preview", result)
        self.assertEqual([w["step"] for w in result["captured_writes"]], ["release"])

    def test_student_view_hides_hidden_inputs_and_teacher_section(self):
        text = ("Logic fails for N = 994009 and N = 4, also 999983 is fine.")
        result, _ = grade("prime_buggy_square", fake_grader(2, 2, cited=[6, 9],
                                                            text=text))
        preview = result["suggested_evaluation_preview"]
        self.assertIn("994009", preview["teacher_feedback"])
        student = reg.student_facing_view(preview, TESTS)
        self.assertNotIn(TEACHER_ONLY_MARKER, student)
        self.assertEqual(reg.leaked_hidden_inputs(student, TESTS), [])
        self.assertNotIn("994009", student)


class IsolationTests(unittest.TestCase):

    def test_gate_refuses_recorded_sandbox(self):
        with self.assertRaises(ValueError):
            gate.register_secure_sandbox(reg.RecordedEvidenceSandbox({}))
        self.assertIsNone(gate.get_secure_sandbox())

    def _run_with(self, sandbox, allow):
        released = []
        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": 1, "source_code": "x"},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("50")},
            load_context=lambda a: CONTEXT, load_rubric=lambda a: RUBRIC,
            load_test_cases=lambda a: TESTS, get_sandbox=lambda: sandbox,
            grade_rubric=lambda *a: self.fail("AI must not run"),
            complete=lambda *a, **k: self.fail("must not complete"),
            complete_compilation_failure=lambda *a: self.fail("must not complete"),
            release=lambda *a: released.append(a) or {"job_status": "PENDING"},
            allow_synthetic_evidence=allow)
        return gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                       "claim_token": str(uuid4())}, "w", deps)

    def test_production_configuration_refuses_recorded_evidence(self):
        result = self._run_with(reg.RecordedEvidenceSandbox({}), allow=False)
        self.assertEqual(result["error_code"], "SYNTHETIC_EVIDENCE_REJECTED")

    def test_unknown_provenance_refused(self):
        class Odd:
            evidence_provenance = "student_upload_replay"
        result = self._run_with(Odd(), allow=True)
        self.assertEqual(result["error_code"], "UNKNOWN_EVIDENCE_PROVENANCE")

    def test_application_never_imports_recorded_harness(self):
        for path in Path(BACKEND).glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("recorded_evidence_grading", text, path.name)

    def test_reviewed_records_match_approved_fixture_hashes(self):
        approved = {
            "prime_correct": "f258e59ba3daefb8a4142beb2b15094356b16c9fc95ed61b0e2a34946e66b824",
            "prime_buggy_square": "d52fba8be7c1f2df6a5faca49c85848e6e949ee40fea7f287cd6e92576cd3bdd",
            "prime_compile_error": "89b1c9ebfe88c7d8d61a9a5301289fcd0e871a21d40f9768f127ef9a7231b1e1"}
        for key, digest in approved.items():
            self.assertEqual(reg.REVIEWED_RECORDS[key]["fixture_sha256"], digest)
            self.assertEqual(hashlib.sha256(teh.fixture_path(key).read_bytes())
                             .hexdigest(), digest)

    def test_approval_file_holds_only_owner_approved_entries(self):
        # Gate V V1 (2026-11-18) approved exactly one A6 fixture; the A5
        # fixtures' one-time approvals were removed after their runs.
        self.assertEqual(teh.load_approvals(), {
            "array_sum_int_accumulator":
                "ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4"})


if __name__ == "__main__":
    unittest.main()
