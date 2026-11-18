"""
LLM analysis mode (no execution) + structured evaluation report + the small
status hooks. Fakes only: no database, no Ollama, no compiler.
"""

import json
import unittest
import uuid
from decimal import Decimal

import evaluation_report as er
import llm_analysis_grading as lag
from evaluation_review_service import grading_status
from grading_job_completion_service import ALLOWED_AUTO_STATUSES
from grading_pipeline import GradingDependencies
from rubric_ai_grader import RubricGradingUnavailable
from student_result_builder import build_student_result, verified_test_marks

RUBRIC = [{"criterion_name": "Logic & Correctness", "max_marks": Decimal("3.00"),
           "description": "sum with long long"},
          {"criterion_name": "Code Quality", "max_marks": Decimal("2.00"),
           "description": "readable"}]
HIDDEN = [{"test_number": n, "input_data": f"3\n{n}7 {n}8 {n}9\n",
           "expected_output": f"{n}424242\n"} for n in range(1, 11)]
SOURCE = "#include <iostream>\nint main(){int n;std::cin>>n;int s=0;}\n"


def ai_result(logic="1.50", quality="2.00", text="Uses int; may overflow."):
    return {"criteria": [
        {"criterion_name": "Logic & Correctness", "rating": logic, "explanation": text,
         "cited_failed_tests": []},
        {"criterion_name": "Code Quality", "rating": quality, "explanation": "Clear.",
         "cited_failed_tests": []}],
        "suggested_score": str(Decimal(logic) + Decimal(quality)),
        "overall_feedback": "Accumulator overflow risk.",
        "suspected_bugs": ["int accumulator overflows for large sums"],
        "issues": [], "improvement_suggestions": ["use long long"],
        "reliability_flags": [], "manual_review_required": False}


class Recorder:
    def __init__(self, sandbox=None, grader=None, validation=50):
        self.calls, self.grade_args = [], None
        self.sandbox, self.grader, self.validation = sandbox, grader, validation

    def deps(self):
        def grade(context, rubric, alloc, source, evidence):
            self.grade_args = (context, rubric, alloc, source, evidence)
            if isinstance(self.grader, Exception):
                raise self.grader
            return self.grader or ai_result()

        return GradingDependencies(
            claim=lambda *a: {"job_id": 7, "claim_token": str(uuid.uuid4())},
            mark_exhausted=lambda: [],
            prepare=lambda j, w, t: {"job_id": j, "submission_id": 9, "source_code": SOURCE},
            renew_lease=lambda *a: True,
            load_assessment_marks=lambda sid: {"assessment_id": 6},
            load_config=lambda aid: {"assessment_id": 6, "total_marks": Decimal("10.00"),
                                     "validation_percentage": Decimal(self.validation)},
            load_context=lambda aid: {"problem": "Sum N integers", "input_format": "N then N ints",
                                      "output_format": "sum", "constraints": "1 <= N <= 100",
                                      "code_constraints": []},
            load_rubric=lambda aid: RUBRIC,
            load_test_cases=lambda aid: HIDDEN,
            get_sandbox=lambda: self.sandbox,
            grade_rubric=grade,
            complete=lambda *a, **k: self.calls.append(("complete", a, k)) or {
                "job_id": a[0], "submission_id": 9, "job_status": "COMPLETED",
                "suggested_score": str(a[3]), "criteria_saved": 2,
                "auto_evaluation_status": k["auto_evaluation_status"],
                "tests_passed": a[5], "tests_run": a[6]},
            release=lambda *a: self.calls.append(("release", a)) or {"job_status": "PENDING"},
        )

    def run(self):
        return lag.analyse_claimed_job({"job_id": 7, "claim_token": str(uuid.uuid4())},
                                       "llm-analysis-test", self.deps())


class AnalysisModeTests(unittest.TestCase):

    def test_saves_unverified_evaluation_with_rubric_only_marks(self):
        rec = Recorder()
        result = rec.run()
        self.assertEqual(result["outcome"], "ANALYSED")
        (name, args, kwargs), = rec.calls
        self.assertEqual(name, "complete")
        _, _, _, score, feedback, passed, run = args
        self.assertEqual((score, passed, run), (Decimal("3.50"), 0, 0))
        self.assertEqual(kwargs["auto_evaluation_status"], "AI_ANALYSIS_UNVERIFIED")
        self.assertEqual([c["rating"] for c in kwargs["criteria"]], ["1.50", "2.00"])
        report = er.parse_report(feedback)
        self.assertEqual(report["evaluation_mode"], er.LLM_ANALYSIS)
        self.assertEqual(report["test_execution"]["status"], er.TEST_NOT_RUN)
        self.assertEqual(report["test_execution"]["tests_configured"], 10)
        self.assertIsNone(report["test_execution"]["tests_passed"])
        self.assertIsNone(report["suggested_marks"]["test_marks"])
        self.assertEqual(report["suggested_marks"]["suggested_total"], "3.50")
        self.assertIn("NOT tested", report["logic_analysis"]["label"])
        self.assertTrue(any("NO_EXECUTION_EVIDENCE" in f for f in report["reliability_flags"]))
        self.assertIn("NOT compiled or run", feedback)

    def test_model_never_receives_hidden_tests(self):
        rec = Recorder()
        rec.run()
        context, rubric, alloc, source, evidence = rec.grade_args
        self.assertEqual((evidence["tests_run"], evidence["cases"]), (0, []))
        blob = json.dumps([context, evidence, source], default=str)
        for t in HIDDEN:
            self.assertNotIn(t["input_data"].strip(), blob)
            self.assertNotIn(t["expected_output"].strip(), blob)
        self.assertEqual(alloc, Decimal("5.00"))

    def test_report_and_feedback_contain_no_hidden_values(self):
        rec = Recorder()
        rec.run()
        feedback = rec.calls[0][1][4]
        for t in HIDDEN:
            self.assertNotIn(t["expected_output"].strip(), feedback)

    def test_refuses_when_a_real_sandbox_exists(self):
        rec = Recorder(sandbox=object())
        result = rec.run()
        self.assertEqual(result["error_code"], "EXECUTION_AVAILABLE")
        self.assertIsNone(rec.grade_args)
        self.assertEqual([c[0] for c in rec.calls], ["release"])

    def test_model_failure_releases_without_evaluation(self):
        rec = Recorder(grader=RubricGradingUnavailable("ollama down"))
        result = rec.run()
        self.assertEqual(result["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertEqual([c[0] for c in rec.calls], ["release"])

    def test_invalid_ai_mark_is_rejected(self):
        rec = Recorder(grader=ai_result(logic="3.50"))
        result = rec.run()
        self.assertEqual(result["error_code"], "VERIFICATION_FAILED")
        self.assertEqual([c[0] for c in rec.calls], ["release"])

    def test_no_rubric_allocation_is_left_for_manual_grading(self):
        rec = Recorder(validation=100)
        self.assertEqual(rec.run()["error_code"], "NO_RUBRIC_FOR_ANALYSIS")

    def test_invented_test_citations_are_dropped_from_the_report(self):
        invented = ai_result()
        invented["criteria"][0]["cited_failed_tests"] = [1]
        rec = Recorder(grader=invented)
        rec.run()
        report = er.parse_report(rec.calls[0][1][4])
        self.assertEqual([r["cited_failed_tests"] for r in report["rubric"]], [[], []])
        self.assertEqual(report["test_execution"]["failed_test_numbers"], [])

    def test_model_is_told_no_tests_ran(self):
        from rubric_ai_grader import build_llm_evidence
        ev = build_llm_evidence({"tests_run": 0, "tests_passed": 0, "cases": []})
        self.assertIn("NO TESTS WERE COMPILED OR RUN", ev["grading_instruction"])
        ran = build_llm_evidence({"tests_run": 1, "tests_passed": 1, "cases": [
            {"test_number": 1, "input_data": "1", "execution_status": "completed",
             "passed": True, "expected_output": "1", "student_output": "1"}]})
        self.assertNotIn("NO TESTS WERE", ran["grading_instruction"])

    def test_ai_text_about_test_outcomes_is_flagged(self):
        rec = Recorder(grader=ai_result(text="The code passes all the tests."))
        rec.run()
        report = er.parse_report(rec.calls[0][1][4])
        self.assertTrue(any("UNVERIFIED_TEST_CLAIM" in f for f in report["reliability_flags"]))

    def test_worker_once_refuses_with_sandbox_and_handles_no_job(self):
        rec = Recorder(sandbox=object())
        self.assertEqual(lag.run_analysis_worker_once("w", rec.deps())["outcome"],
                         "EXECUTION_AVAILABLE")
        rec = Recorder()
        self.assertEqual(lag.run_analysis_worker_once("w", rec.deps(),
                                                      claim_fn=lambda *a: None)["outcome"],
                         "NO_JOB")


class ReportTests(unittest.TestCase):

    def base(self, **over):
        values = dict(mode=er.LLM_ANALYSIS, evidence_basis="none",
                      assessment={"assessment_id": 6, "total_marks": "10"},
                      constraints_text="1 <= N <= 100", constraint_report={"checks": []},
                      rubric=RUBRIC, ai_result=ai_result(), test_alloc="5",
                      rubric_alloc="5", tests_configured=10, rubric_marks="3.50",
                      suggested_total="3.50")
        values.update(over)
        return er.build_report(**values)

    def test_llm_mode_cannot_carry_test_results(self):
        with self.assertRaises(ValueError):
            self.base(tests_run=10, tests_passed=10)
        with self.assertRaises(ValueError):
            self.base(test_marks="5.00")

    def test_verified_mode_requires_executed_tests(self):
        with self.assertRaises(ValueError):
            self.base(mode=er.VERIFIED_EXECUTION)
        report = self.base(mode=er.VERIFIED_EXECUTION, tests_run=10, tests_passed=7,
                           failed_test_numbers=[6, 3, 8], test_marks="3.50",
                           suggested_total="7.00")
        self.assertEqual(report["test_execution"]["status"], er.TEST_VERIFIED)
        self.assertEqual(report["test_execution"]["failed_test_numbers"], [3, 6, 8])
        self.assertEqual(report["suggested_marks"]["test_marks_basis"],
                         "verified test execution")

    def test_section_round_trip_and_teacher_overlay(self):
        report = self.base()
        text = "free text\n" + er.report_section(report)
        self.assertEqual(er.parse_report(text), report)
        self.assertIsNone(er.parse_report("no report here"))
        final = er.with_teacher_status(report, "FINALIZED", "7.00", [{"criterion_name": "x"}])
        self.assertEqual(final["teacher_approval"]["status"], "APPROVED_FINAL")
        pending = er.with_teacher_status(report, None, None, [])
        self.assertEqual(pending["teacher_approval"]["status"], "PENDING_REVIEW")
        self.assertIsNone(pending["teacher_approval"]["final_score"])

    def test_legacy_evaluations_get_a_derived_report(self):
        derived = er.derive_report(auto_status="COMPLETED", tests_passed=10, tests_run=10,
                                   suggested_score="10.00", total_marks="10.00",
                                   test_marks="5.00", ai_criteria=[],
                                   feedback="Version: v | evidence: trusted_fixture_recorded |")
        self.assertEqual((derived["evaluation_mode"], derived["evidence_basis"]),
                         (er.VERIFIED_EXECUTION, "trusted_fixture_recorded"))
        manual = er.derive_report(auto_status="MANUALLY_GRADED", tests_passed=0, tests_run=0,
                                  suggested_score=None, total_marks="10", test_marks=None,
                                  ai_criteria=[], feedback="")
        self.assertEqual(manual["evaluation_mode"], er.MANUAL)
        self.assertEqual(manual["test_execution"]["status"], er.TEST_NOT_RUN)


class StatusHookTests(unittest.TestCase):

    def test_completion_accepts_the_new_status(self):
        self.assertIn("AI_ANALYSIS_UNVERIFIED", ALLOWED_AUTO_STATUSES)

    def test_teacher_sees_it_as_needing_review(self):
        self.assertEqual(grading_status("COMPLETED", True, None, "AI_ANALYSIS_UNVERIFIED"),
                         "AUTO_GRADED_NEEDS_REVIEW")

    def test_unverified_evaluations_never_have_test_marks(self):
        self.assertEqual(verified_test_marks("AI_ANALYSIS_UNVERIFIED", 10, 10, "10", "50"),
                         (None, Decimal("5.00")))

    def test_student_result_says_not_executed(self):
        common = dict(final_score="3.50", total_marks="10.00", validation_percentage="50",
                      auto_status="AI_ANALYSIS_UNVERIFIED", tests_passed=0, tests_run=0,
                      instructor_feedback=None, stored_feedback="teacher only",
                      rubric_rows=RUBRIC,
                      ai_criteria=[{"criterion_name": "Logic & Correctness", "rating": "1.50"},
                                   {"criterion_name": "Code Quality", "rating": "2.00"}])
        result = build_student_result(**common, approved_criteria=[])
        self.assertEqual(result["outcome"], "CODE_REVIEWED_NOT_EXECUTED")
        self.assertIsNone(result["tests_passed"])
        self.assertIsNone(result["tests_run"])
        self.assertIsNone(result["test_marks"])
        self.assertEqual(result["rubric_source"], "FINAL_TOTAL_ONLY")   # AI marks not shown
        self.assertIn("not run against the hidden tests", result["summary"][0])
        approved = build_student_result(**common, approved_criteria=[
            {"criterion_name": "Logic & Correctness", "approved_marks": "1.50",
             "teacher_comment": None},
            {"criterion_name": "Code Quality", "approved_marks": "2.00",
             "teacher_comment": None}])
        self.assertEqual(approved["rubric_source"], "TEACHER_APPROVED")
        self.assertEqual(approved["rubric_marks"], "3.50")


class FixturePolicyTests(unittest.TestCase):

    def test_a6_fixture_is_bound_to_assessment_6_and_not_approved(self):
        from dev_tools import recorded_evidence_grading as reg
        from dev_tools import trusted_execution_harness as teh
        self.assertEqual(teh.assessment_for("array_sum_int_accumulator"), 6)
        self.assertEqual({teh.assessment_for(k) for k in
                          ("prime_correct", "prime_buggy_square", "prime_compile_error")}, {5})
        self.assertNotIn("array_sum_int_accumulator", teh.load_approvals())
        with self.assertRaises(teh.NotApproved):
            teh.require_approval("array_sum_int_accumulator")
        self.assertNotIn("array_sum_int_accumulator", reg.REVIEWED_RECORDS)
        with self.assertRaises(reg.RecordIntegrityError):
            reg.verify_record("array_sum_int_accumulator", {}, "x", [], "y", "5")

    def test_fixture_grader_rejects_cross_assessment_submissions(self):
        from dev_tools import fixture_submission_grader as fsg
        from dev_tools import trusted_execution_harness as teh
        source = teh.fixture_path("prime_correct").read_text(encoding="utf-8")
        sub = {"assessment_id": 6, "job_id": 1, "job_status": "PENDING", "attempt_count": 0,
               "evaluation_exists": 0, "enrolled": 1, "teacher_linked": 1,
               "source_code": source}
        with self.assertRaises(fsg.SubmissionNotEligible) as ctx:
            fsg.check_eligibility(sub)
        self.assertIn("not 5", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
