"""
rubric_ai_grader on the criterion-only contract. Fake graders feed model-
shaped JSON through the REAL parser, so validation is exercised too.
"""

import unittest
from decimal import Decimal

from tests import fakes  # noqa: F401

from ollama_grading_service import (GradingServiceError,
                                    parse_criterion_only_response)
from rubric_ai_grader import (
    RubricGradingUnavailable, build_llm_evidence, grade_rubric_with_ollama)

CONTEXT = {"problem": "p", "input_format": "i", "output_format": "o",
           "constraints": "c"}
RUBRIC = [{"criterion_name": "Logic", "max_marks": Decimal("3"),
           "description": "correct algorithm"},
          {"criterion_name": "Style", "max_marks": Decimal("2")}]
EVIDENCE = {"tests_run": 2, "tests_passed": 0, "cases": [
    {"test_number": 1, "execution_status": "completed", "passed": False,
     "expected_output": "2\n", "student_output": "x" * 2000},
    {"test_number": 2, "execution_status": "timeout", "passed": False,
     "expected_output": "4\n", "student_output": None}]}


def model_json(marks, **extra):
    body = {"criteria": [{"criterion_name": n, "mark": m, "explanation": "e"}
                         for n, m in marks],
            "overall_feedback": "fb", "issues": ["bug"],
            "improvement_suggestions": ["s"]}
    body.update(extra)
    return body


def fake_grader(marks, capture=None, **extra):
    def grader(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return parse_criterion_only_response(model_json(marks, **extra),
                                             kwargs["rubric"])
    return grader


class RubricAiGraderTests(unittest.TestCase):
    def test_python_computes_total_and_model_gets_no_maximum_total(self):
        seen = {}
        r = grade_rubric_with_ollama(
            CONTEXT, RUBRIC, Decimal("5"), "code", EVIDENCE,
            grader=fake_grader([("Logic", 1.5), ("Style", 1)], seen))
        self.assertNotIn("max_marks", seen)          # no total to aim for
        self.assertEqual([c["criterion_name"] for c in seen["rubric"]],
                         ["Logic", "Style"])
        self.assertEqual(r["suggested_score"], "2.50")
        self.assertEqual(r["rubric_total_source"],
                         "python_sum_of_validated_criteria")
        self.assertEqual([c["rating"] for c in r["criteria"]], ["1.50", "1.00"])
        self.assertEqual(r["issues"], ["bug"])

    def test_stray_model_total_is_ignored_not_used(self):
        r = grade_rubric_with_ollama(
            CONTEXT, RUBRIC, Decimal("5"), "code", EVIDENCE,
            grader=fake_grader([("Logic", 2), ("Style", 2)],
                               suggested_score=3))       # the old 2+2=3 error
        self.assertEqual(r["suggested_score"], "4.00")
        self.assertEqual(r["ignored_model_fields"], ["suggested_score"])

    def test_model_failure_is_infrastructure_error(self):
        def broken(**kwargs):
            raise GradingServiceError("Offline LLM unavailable")
        with self.assertRaises(RubricGradingUnavailable):
            grade_rubric_with_ollama(CONTEXT, RUBRIC, Decimal("5"), "c",
                                     EVIDENCE, grader=broken)

    def test_tampered_parser_output_rejected(self):
        def bad(**kwargs):
            return {"criteria": [{"criterion_name": "Logic", "mark": Decimal("9"),
                                  "explanation": "x"},
                                 {"criterion_name": "Style", "mark": Decimal("1"),
                                  "explanation": "y"}]}
        with self.assertRaises(RubricGradingUnavailable):
            grade_rubric_with_ollama(CONTEXT, RUBRIC, Decimal("5"), "c",
                                     EVIDENCE, grader=bad)

    def test_high_score_with_zero_passes_is_flagged(self):
        r = grade_rubric_with_ollama(
            CONTEXT, RUBRIC, Decimal("5"), "c", EVIDENCE,
            grader=fake_grader([("Logic", 3), ("Style", 2)]))
        self.assertTrue(r["manual_review_required"])
        self.assertTrue(r["reliability_flags"])

    def test_full_rubric_marks_with_some_failed_tests_needs_review(self):
        # The live most_pass result: 8/10 tests passed, AI gave 3 + 2.
        r = grade_rubric_with_ollama(
            CONTEXT, RUBRIC, Decimal("5"), "c",
            {"tests_run": 10, "tests_passed": 8, "cases": []},
            grader=fake_grader([("Logic", 3), ("Style", 2)]))
        self.assertTrue(r["manual_review_required"])
        self.assertIn("Full rubric marks despite failed tests",
                      r["reliability_flags"][0])
        self.assertEqual(r["suggested_score"], "5.00")      # marks unchanged

    def test_no_flag_for_consistent_results(self):
        for passed, logic, style in ((10, 3, 2), (8, 2, 2), (0, 0, 0)):
            with self.subTest(passed=passed):
                r = grade_rubric_with_ollama(
                    CONTEXT, RUBRIC, Decimal("5"), "c",
                    {"tests_run": 10, "tests_passed": passed, "cases": []},
                    grader=fake_grader([("Logic", logic), ("Style", style)]))
                self.assertFalse(r["manual_review_required"])

    def test_zero_allocation_refused(self):
        with self.assertRaises(ValueError):
            grade_rubric_with_ollama(CONTEXT, [], 0, "c", EVIDENCE,
                                     grader=fake_grader([]))

    def test_rubric_not_matching_allocation_is_config_error(self):
        with self.assertRaises(ValueError):
            grade_rubric_with_ollama(CONTEXT, RUBRIC, Decimal("6"), "c",
                                     EVIDENCE, grader=fake_grader([]))

    def test_evidence_is_clipped_and_not_fabricated(self):
        ev = build_llm_evidence(EVIDENCE)
        self.assertLess(len(ev["cases"][0]["student_output"]), 600)
        self.assertIsNone(ev["cases"][1]["student_output"])
        self.assertIn("NOT yours to award", ev["grading_instruction"])
        empty = build_llm_evidence({"tests_run": 0, "tests_passed": 0,
                                    "cases": []})
        self.assertEqual(empty["evidence_type"], "no_test_execution")


if __name__ == "__main__":
    unittest.main()
