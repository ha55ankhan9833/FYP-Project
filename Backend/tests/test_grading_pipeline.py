import unittest
from decimal import Decimal
from uuid import uuid4

from tests import fakes  # noqa: F401

import grading_pipeline as gp
from grading_job_completion_service import GradingJobOwnershipError
from rubric_ai_grader import RubricGradingUnavailable
from secure_execution_gate import SandboxInfrastructureError

TOKEN = str(uuid4())
CLAIM = {"job_id": 11, "submission_id": 21, "claim_token": TOKEN}
TESTS = [{"test_number": n, "test_name": f"t{n}", "category": "c",
          "input_data": f"{n}\n", "expected_output": f"{n * 2}\n"}
         for n in range(1, 11)]
RUBRIC = [{"criterion_name": "Logic", "max_marks": Decimal("3")},
          {"criterion_name": "Style", "max_marks": Decimal("2")}]


class FakeSandbox:
    def __init__(self, compile_status="COMPILED", passing=8,
                 compile_error=None, run_error=None):
        self.compile_status = compile_status
        self.passing = passing
        self.compile_error = compile_error
        self.run_error = run_error
        self.cleaned = []
        self.received_cases = None

    def compile(self, source):
        if self.compile_error:
            raise self.compile_error
        return {"status": self.compile_status,
                "diagnostics": "main.cpp:1: error: expected ';'",
                "artifact": "ART" if self.compile_status == "COMPILED" else None}

    def run_tests(self, artifact, cases):
        self.received_cases = cases
        if self.run_error:
            raise self.run_error
        out = []
        for c in cases:
            n = c["test_number"]
            ok = n <= self.passing
            out.append({"test_number": n, "status": "completed",
                        "stdout": f"{n * 2}\n" if ok else "wrong\n"})
        return out

    def cleanup(self, artifact):
        self.cleaned.append(artifact)


def make_deps(validation="50", sandbox=None, rubric=RUBRIC, ai=None,
              ai_error=None, renew_ok=True, complete_error=None):
    calls = {"complete": [], "compile_fail": [], "release": [],
             "grade_rubric": 0, "claim": 0}

    def complete(*args, **kwargs):
        if complete_error:
            raise complete_error
        calls["complete"].append((args, kwargs))
        return {"job_id": args[0], "job_status": "COMPLETED"}

    def grade_rubric(context, rubric_, alloc, source, evidence):
        calls["grade_rubric"] += 1
        calls["evidence"] = evidence
        if ai_error:
            raise ai_error
        return ai or {
            "criteria": [
                {"criterion_name": "Logic", "rating": "3.00", "explanation": "ok"},
                {"criterion_name": "Style", "rating": "1.00", "explanation": "meh"}],
            "suggested_score": "4.00", "overall_feedback": "Good",
            "improvement_suggestions": [], "reliability_flags": [],
            "manual_review_required": False}

    def claim(worker_id, lease, attempts):
        calls["claim"] += 1
        return dict(CLAIM)

    deps = gp.GradingDependencies(
        claim=claim,
        mark_exhausted=lambda: [],
        prepare=lambda j, w, t: {"job_id": j, "submission_id": 21,
                                 "source_code": "int main(){}",
                                 "file_name": "a.cpp", "student_id": "S1",
                                 "assessment_section_id": 3},
        renew_lease=lambda *a: "2026-11-17T10:00:00" if renew_ok else None,
        load_assessment_marks=lambda sid: {"assessment_id": 4,
                                           "total_marks": Decimal("10")},
        load_config=lambda aid: {"total_marks": Decimal("10"),
                                 "validation_percentage": Decimal(validation)},
        load_context=lambda aid: {"problem": "p", "input_format": "i",
                                  "output_format": "o", "constraints": "c"},
        load_rubric=lambda aid: rubric,
        load_test_cases=lambda aid: TESTS,
        get_sandbox=lambda: sandbox,
        grade_rubric=grade_rubric,
        complete=complete,
        complete_compilation_failure=lambda *a: (
            calls["compile_fail"].append(a) or
            {"job_status": "COMPLETED", "suggested_score": 0}),
        release=lambda *a: (calls["release"].append(a) or
                            {"job_status": "PENDING"}),
    )
    return deps, calls


class PipelineTests(unittest.TestCase):
    def test_spec_example_graded_8_of_10(self):
        sandbox = FakeSandbox(passing=8)
        deps, calls = make_deps(sandbox=sandbox)
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)

        self.assertEqual(r["outcome"], "GRADED")
        args, kwargs = calls["complete"][0]
        self.assertEqual(args[3], Decimal("8.00"))          # suggested
        self.assertEqual((args[5], args[6]), (8, 10))       # passed/run
        self.assertEqual(len(kwargs["criteria"]), 2)
        self.assertEqual(kwargs["auto_evaluation_status"], "COMPLETED")
        self.assertIn("Tests passed: 8 / 10", args[4])
        self.assertIn("provisional", args[4])
        self.assertEqual(sandbox.cleaned, ["ART"])
        self.assertFalse(calls["release"])

    def test_expected_outputs_never_sent_to_sandbox(self):
        sandbox = FakeSandbox()
        deps, _ = make_deps(sandbox=sandbox)
        gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertTrue(all(set(c) == {"test_number", "input_data"}
                            for c in sandbox.received_cases))

    def test_compilation_failure_zero_without_llm(self):
        deps, calls = make_deps(
            sandbox=FakeSandbox(compile_status="COMPILATION_FAILED"))
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["outcome"], "COMPILATION_FAILED")
        self.assertEqual(calls["grade_rubric"], 0)
        self.assertEqual(len(calls["compile_fail"]), 1)
        self.assertFalse(calls["complete"])

    def test_sandbox_unavailable_releases_without_evaluation(self):
        deps, calls = make_deps(sandbox=None)
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "SECURE_EXECUTION_UNAVAILABLE")
        self.assertFalse(r["evaluation_written"])
        self.assertFalse(calls["complete"] or calls["compile_fail"])
        self.assertEqual(len(calls["release"]), 1)

    def test_compiler_infrastructure_failure_is_not_student_failure(self):
        deps, calls = make_deps(sandbox=FakeSandbox(
            compile_error=SandboxInfrastructureError("daemon down")))
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "SANDBOX_COMPILE_FAILURE")
        self.assertFalse(calls["complete"] or calls["compile_fail"])

    def test_run_infrastructure_failure_cleans_up(self):
        sandbox = FakeSandbox(run_error=SandboxInfrastructureError("x"))
        deps, calls = make_deps(sandbox=sandbox)
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "SANDBOX_RUN_FAILURE")
        self.assertEqual(sandbox.cleaned, ["ART"])
        self.assertFalse(calls["complete"])

    def test_ollama_unavailable_releases(self):
        deps, calls = make_deps(sandbox=FakeSandbox(),
                                ai_error=RubricGradingUnavailable("timeout"))
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertFalse(calls["complete"])

    def test_invalid_ai_score_rejected_not_saved(self):
        bad_ai = {"criteria": [
            {"criterion_name": "Logic", "rating": "9", "explanation": ""},
            {"criterion_name": "Style", "rating": "1", "explanation": ""}],
            "suggested_score": "10"}
        deps, calls = make_deps(sandbox=FakeSandbox(), ai=bad_ai)
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "SCORE_VALIDATION_FAILED")
        self.assertFalse(calls["complete"])

    def test_zero_rubric_allocation_skips_llm(self):
        deps, calls = make_deps(validation="100", sandbox=FakeSandbox(passing=7),
                                rubric=[])
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["outcome"], "GRADED")
        self.assertEqual(calls["grade_rubric"], 0)
        args, kwargs = calls["complete"][0]
        self.assertEqual(args[3], Decimal("7.00"))
        self.assertIsNone(kwargs["criteria"])

    def test_missing_rubric_is_configuration_failure(self):
        deps, calls = make_deps(sandbox=FakeSandbox(), rubric=[])
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "RUBRIC_NOT_CONFIGURED")
        self.assertFalse(calls["complete"])

    def test_lost_lease_writes_nothing(self):
        deps, calls = make_deps(sandbox=FakeSandbox(), renew_ok=False)
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["outcome"], "OWNERSHIP_LOST")
        self.assertFalse(calls["complete"] or calls["release"])

    def test_stale_worker_rejected_at_completion(self):
        deps, calls = make_deps(sandbox=FakeSandbox(),
                                complete_error=GradingJobOwnershipError())
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["outcome"], "OWNERSHIP_LOST")
        self.assertFalse(calls["release"])

    def test_reliability_flag_marks_needs_review(self):
        ai = {"criteria": [
            {"criterion_name": "Logic", "rating": "3", "explanation": ""},
            {"criterion_name": "Style", "rating": "2", "explanation": ""}],
            "suggested_score": "5", "overall_feedback": "",
            "improvement_suggestions": [],
            "reliability_flags": ["High LLM score despite zero passed tests."],
            "manual_review_required": True}
        deps, calls = make_deps(sandbox=FakeSandbox(passing=0), ai=ai)
        gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(calls["complete"][0][1]["auto_evaluation_status"],
                         "NEEDS_REVIEW")

    def test_unexpected_error_never_zeroes(self):
        deps, calls = make_deps(sandbox=FakeSandbox())
        deps.load_context = lambda aid: 1 / 0
        r = gp.process_claimed_job(dict(CLAIM), "w1", deps)
        self.assertEqual(r["error_code"], "UNEXPECTED_WORKER_ERROR")
        self.assertFalse(calls["complete"] or calls["compile_fail"])


class WorkerLoopTests(unittest.TestCase):
    def test_does_not_claim_without_sandbox(self):
        deps, calls = make_deps(sandbox=None)
        r = gp.run_worker_once("w1", deps)
        self.assertEqual(r["outcome"], "SECURE_EXECUTION_DISABLED")
        self.assertEqual(calls["claim"], 0)

    def test_claims_and_grades_with_sandbox(self):
        deps, calls = make_deps(sandbox=FakeSandbox())
        r = gp.run_worker_once("w1", deps)
        self.assertTrue(r["claimed"])
        self.assertEqual(r["outcome"], "GRADED")


if __name__ == "__main__":
    unittest.main()
