"""
Python grading verification layer: deterministic re-verification,
structured report, synthetic-evidence isolation, reliability policy and
student-facing redaction. No DB, no C++ execution, no real LLM.
"""

import json
import unittest
from decimal import Decimal
from uuid import uuid4

from tests import fakes  # noqa: F401

import grading_pipeline as gp
import secure_execution_gate as gate
from code_constraint_checker import INCONCLUSIVE, SATISFIED, VIOLATED, \
    check_code_constraints
from dev_tools import grading_preview_harness as harness
from evaluation_review_service import student_safe_feedback, hidden_values
from grading_verification import (SECTION_MARKER, VerificationFailure,
                                  verify_and_report, verify_tests)
from ollama_grading_service import parse_criterion_only_response
from output_comparison import (EXACT, TRIMMED_EXACT, WHITESPACE_NORMALIZED,
                               outputs_match)
from rubric_ai_grader import RubricGradingUnavailable, grade_rubric_with_ollama

RUBRIC = [{"criterion_name": "Logic & Algorithm", "max_marks": Decimal("3")},
          {"criterion_name": "Code Quality", "max_marks": Decimal("2")}]
CONTEXT = {"problem": "p", "input_format": "i", "output_format": "o",
           "constraints": "1 <= N <= 1000000", "code_constraints": []}
HIDDEN_INPUTS = [str(700000 + n) for n in range(1, 11)]
HIDDEN_OUTPUTS = [f"ANSWER-{n:04d}\n" for n in range(1, 11)]
TESTS = [{"test_number": n, "input_data": HIDDEN_INPUTS[n - 1],
          "expected_output": HIDDEN_OUTPUTS[n - 1]} for n in range(1, 11)]


class Sandbox:
    """Non-synthetic stand-in: production would get this from the gate."""

    def __init__(self, passing):
        self.passing = passing

    def compile(self, src):
        return {"status": "COMPILED", "artifact": "A"}

    def run_tests(self, artifact, cases):
        return [{"test_number": c["test_number"], "status": "completed",
                 "stdout": (HIDDEN_OUTPUTS[c["test_number"] - 1]
                            if c["test_number"] <= self.passing else "WRONG\n")}
                for c in cases]

    def cleanup(self, artifact):
        pass


class SyntheticSandbox(Sandbox):
    synthetic = True


def ai_grader(logic, quality, logic_text="Uses the loop condition correctly.",
              quality_text="Readable variable names.", cited=None):
    def grader(**kwargs):
        return parse_criterion_only_response({
            "criteria": [{"criterion_name": "Logic & Algorithm", "mark": logic,
                          "explanation": logic_text,
                          "cited_failed_tests": cited or []},
                         {"criterion_name": "Code Quality", "mark": quality,
                          "explanation": quality_text}],
            "overall_feedback": "ok", "issues": [], "improvement_suggestions": []},
            kwargs["rubric"])
    return grader


def run(passing=10, grader=None, validation="50", sandbox=None,
        code_constraints=(), source="int main(){ for(;;){} }", allow=False):
    calls = {"complete": [], "release": [], "ai": 0}

    def grade_rubric(context, rubric, alloc, src, evidence):
        calls["ai"] += 1
        calls["evidence"] = evidence
        return grade_rubric_with_ollama(context, rubric, alloc, src, evidence,
                                        grader=grader or ai_grader(3, 2))

    deps = gp.GradingDependencies(
        prepare=lambda *a: {"job_id": 1, "submission_id": 1, "source_code": source},
        renew_lease=lambda *a: "ok",
        load_assessment_marks=lambda s: {"assessment_id": 5},
        load_config=lambda a: {"total_marks": Decimal("10"),
                               "validation_percentage": Decimal(validation)},
        load_context=lambda a: {**CONTEXT, "code_constraints": list(code_constraints)},
        load_rubric=lambda a: RUBRIC if validation != "100" else [],
        load_test_cases=lambda a: TESTS,
        get_sandbox=lambda: sandbox if sandbox is not None else Sandbox(passing),
        grade_rubric=grade_rubric,
        complete=lambda *a, **k: calls["complete"].append((a, k)) or
        {"job_status": "COMPLETED"},
        complete_compilation_failure=lambda *a: calls["complete"].append((a, {})) or
        {"job_status": "COMPLETED"},
        release=lambda *a: calls["release"].append(a) or {"job_status": "PENDING"},
        allow_synthetic_evidence=allow)
    result = gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                     "claim_token": str(uuid4())}, "w", deps)
    return result, calls


class OutcomeTests(unittest.TestCase):
    def test_10_8_5_0_outcomes_are_deterministic(self):
        for passing, test_score in ((10, "5.00"), (8, "4.00"), (5, "2.50"),
                                    (0, "0.00")):
            with self.subTest(passing=passing):
                result, calls = run(passing, grader=ai_grader(2, 1,
                                    "The loop is wrong for some inputs.",
                                    cited=[10] if passing < 10 else []))
                report = result["verification_report"]
                self.assertEqual(report["tests"]["passed"], passing)
                self.assertEqual(report["tests"]["failed"], 10 - passing)
                self.assertEqual(report["tests"]["test_score"], test_score)
                self.assertEqual(Decimal(report["tests"]["marks_per_test"]),
                                 Decimal("0.5"))           # exact 5 / 10
                self.assertEqual(report["rubric"]["python_total"], "3.00")
                expected = Decimal(test_score) + Decimal("3")
                self.assertEqual(Decimal(report["suggested_total"]), expected)
                self.assertEqual(calls["complete"][0][0][3], expected)
                self.assertTrue(report["teacher_approval_required"])
                self.assertFalse(report["marks_adjusted_by_verification"])
                self.assertIn("equal_weight_test_score",
                              report["deterministic_checks_passed"])

    def test_report_and_feedback_contain_no_hidden_test_data(self):
        result, calls = run(7, grader=ai_grader(2, 1, "wrong output", cited=[8]))
        text = json.dumps(result["verification_report"]) + calls["complete"][0][0][4]
        for secret in HIDDEN_INPUTS + [o.strip() for o in HIDDEN_OUTPUTS]:
            self.assertNotIn(secret, text)
        self.assertIn(SECTION_MARKER, calls["complete"][0][0][4])


class DeterministicVerificationTests(unittest.TestCase):
    def evidence(self, passed=8, run_count=10):
        cases = [{"test_number": n, "passed": n <= passed,
                  "execution_status": "completed",
                  "expected_output": "X\n", "student_output":
                      "X\n" if n <= passed else "Y\n"} for n in range(1, run_count + 1)]
        return {"tests_run": run_count, "tests_passed": passed, "cases": cases}

    def score(self, test_score="4.00"):
        return {"test_score": test_score, "test_marks_allocated": "5.00"}

    def test_invalid_evidence_counts_rejected(self):
        bad = [
            ("9 results", self.evidence(8, 9), self.score()),
            ("11 results", self.evidence(8, 11), self.score()),
            ("count mismatch", {**self.evidence(8), "tests_passed": 9}, self.score()),
            ("flag vs comparison",
             {**self.evidence(8), "cases": [dict(c, passed=True)
                                            for c in self.evidence(8)["cases"]],
              "tests_passed": 10}, {"test_score": "5.00",
                                    "test_marks_allocated": "5.00"}),
            ("duplicate numbers", {**self.evidence(8), "cases":
                                   self.evidence(8)["cases"][:9] +
                                   [self.evidence(8)["cases"][0]]}, self.score()),
            ("tampered test score", self.evidence(8), self.score("4.50")),
        ]
        for label, evidence, score in bad:
            with self.subTest(label), self.assertRaises(VerificationFailure):
                verify_tests(evidence, "5.00", score)

    def test_valid_evidence(self):
        tests, checks = verify_tests(self.evidence(8), "5.00", self.score())
        self.assertEqual(tests["formula"], "ROUND_HALF_UP(5.00 x 8 / 10, 2)")
        self.assertIn("pass_count_consistent", checks)

    def test_rubric_and_total_tampering_rejected(self):
        ev = self.evidence(10)
        good_ai = {"criteria": [{"criterion_name": "Logic & Algorithm", "rating": "3.00"},
                                {"criterion_name": "Code Quality", "rating": "2.00"}],
                   "suggested_score": "5.00", "reliability_flags": []}
        score = {"test_score": "5.00", "test_marks_allocated": "5.00",
                 "rubric_score": "5.00", "suggested_score": "10.00"}
        cfg = {"total_marks": "10"}
        verify_and_report(cfg, score, ev, good_ai, RUBRIC, None, "5", "5")
        for label, ai, sc in [
            ("rubric total not python sum", {**good_ai, "suggested_score": "4.00"}, score),
            ("missing criterion", {**good_ai, "criteria": good_ai["criteria"][:1]}, score),
            ("over max", {**good_ai, "criteria": [
                {"criterion_name": "Logic & Algorithm", "rating": "3.50"},
                {"criterion_name": "Code Quality", "rating": "1.50"}]}, score),
            ("suggested tampered", good_ai, {**score, "suggested_score": "9.00"}),
        ]:
            with self.subTest(label), self.assertRaises(VerificationFailure):
                verify_and_report(cfg, sc, ev, ai, RUBRIC, None, "5", "5")

    def test_verification_failure_releases_without_zero(self):
        import grading_verification as gv
        original = gv.calculate_equal_weight_score
        gv.calculate_equal_weight_score = lambda p, r, a: {"test_score": "1.23"}
        try:
            result, calls = run(10)
        finally:
            gv.calculate_equal_weight_score = original
        self.assertEqual(result["error_code"], "VERIFICATION_FAILED")
        self.assertFalse(calls["complete"])
        self.assertEqual(len(calls["release"]), 1)


class ComparisonPolicyTests(unittest.TestCase):
    def test_default_is_the_historical_trimmed_exact_rule(self):
        self.assertTrue(outputs_match("YES\n", "YES"))
        self.assertTrue(outputs_match("  YES  \n", "YES\n"))
        self.assertFalse(outputs_match("Yes\n", "YES\n"))           # case matters
        self.assertFalse(outputs_match("1  2\n", "1 2\n"))           # inner spaces
        self.assertFalse(outputs_match(None, "YES"))

    def test_other_policies_explicit(self):
        self.assertFalse(outputs_match("YES\n", "YES", EXACT))
        self.assertTrue(outputs_match("1  2\n", "1 2", WHITESPACE_NORMALIZED))
        with self.assertRaises(ValueError):
            outputs_match("a", "a", "CASE_INSENSITIVE")


class ReliabilityPolicyTests(unittest.TestCase):
    def test_invalid_ai_marks_reject_result_and_release(self):
        def bad_grader(**kwargs):
            return parse_criterion_only_response({
                "criteria": [{"criterion_name": "Logic & Algorithm", "mark": 4,
                              "explanation": "x"},
                             {"criterion_name": "Code Quality", "mark": 1,
                              "explanation": "y"}],
                "overall_feedback": "", "issues": [], "improvement_suggestions": []},
                kwargs["rubric"])
        result, calls = run(10, grader=bad_grader)
        self.assertEqual(result["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertFalse(calls["complete"])

    def test_missing_rubric_criterion_rejected(self):
        def partial(**kwargs):
            return parse_criterion_only_response({
                "criteria": [{"criterion_name": "Logic & Algorithm", "mark": 2,
                              "explanation": "x"}],
                "overall_feedback": "", "issues": [], "improvement_suggestions": []},
                kwargs["rubric"])
        result, calls = run(10, grader=partial)
        self.assertEqual(result["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertFalse(calls["complete"])

    def test_contradictory_feedback_preserves_marks_and_needs_review(self):
        result, calls = run(8, grader=ai_grader(
            3, 2, "The loop condition d * d < n is incorrect for squares.",
            cited=[9]))
        args, kwargs = calls["complete"][0]
        self.assertEqual(kwargs["auto_evaluation_status"], "NEEDS_REVIEW")
        self.assertEqual([c["rating"] for c in kwargs["criteria"]], ["3.00", "2.00"])
        self.assertEqual(args[3], Decimal("9.00"))
        codes = " ".join(w["message"] for w in result["verification_report"]["warnings"])
        self.assertIn("DEFECT_ADMITTED_WITH_FULL_MARKS", codes)

    def test_invented_failed_test_reference_flagged(self):
        result, calls = run(8, grader=ai_grader(2, 2, "wrong for a case", cited=[1]))
        self.assertEqual(calls["complete"][0][1]["auto_evaluation_status"],
                         "NEEDS_REVIEW")
        self.assertIn("UNSUPPORTED_FAILURE_CLAIM", json.dumps(
            result["verification_report"]["warnings"]))

    def test_clean_result_completes(self):
        result, calls = run(10, grader=ai_grader(3, 2))
        self.assertEqual(result["verification_report"]["review_status"], "COMPLETED")
        self.assertEqual(calls["complete"][0][1]["auto_evaluation_status"],
                         "COMPLETED")

    def test_infrastructure_failures_never_zero(self):
        def down(**kwargs):
            from ollama_grading_service import GradingServiceError
            raise GradingServiceError("Offline LLM unavailable")
        for label, kwargs, code in (
                ("ollama down", {"grader": down}, "AI_GRADING_UNAVAILABLE"),
                ("no sandbox", {"sandbox": False}, "SECURE_EXECUTION_UNAVAILABLE")):
            with self.subTest(label):
                if kwargs.get("sandbox") is False:
                    deps_result, calls = self._run_without_sandbox()
                else:
                    deps_result, calls = run(10, **kwargs)
                self.assertEqual(deps_result["error_code"], code)
                self.assertFalse(calls["complete"])

    def _run_without_sandbox(self):
        calls = {"complete": [], "release": []}
        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": 1, "source_code": "x"},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("50")},
            load_context=lambda a: CONTEXT, load_rubric=lambda a: RUBRIC,
            load_test_cases=lambda a: TESTS, get_sandbox=lambda: None,
            complete=lambda *a, **k: calls["complete"].append(a),
            release=lambda *a: calls["release"].append(a) or {"job_status": "PENDING"})
        return gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                       "claim_token": str(uuid4())}, "w", deps), calls


class SyntheticIsolationTests(unittest.TestCase):
    def test_synthetic_evidence_rejected_in_production_dependencies(self):
        result, calls = run(10, sandbox=SyntheticSandbox(10))
        self.assertEqual(result["error_code"], "SYNTHETIC_EVIDENCE_REJECTED")
        self.assertFalse(calls["complete"])
        self.assertEqual(calls["ai"], 0)

    def test_dev_harness_opt_in_is_labelled(self):
        result, calls = run(10, sandbox=SyntheticSandbox(10), allow=True)
        self.assertEqual(result["verification_report"]["provenance"],
                         "synthetic_development")
        self.assertTrue(calls["evidence"]["synthetic"])

    def test_real_sandbox_labelled_as_sandbox_execution(self):
        result, _ = run(10)
        self.assertEqual(result["verification_report"]["provenance"],
                         "sandbox_execution")

    def test_synthetic_source_cannot_be_registered_with_gate(self):
        with self.assertRaises(ValueError):
            gate.register_secure_sandbox(SyntheticSandbox(10))
        with self.assertRaises(ValueError):
            gate.register_secure_sandbox(
                harness.SyntheticEvidenceSandbox(harness.model_all_pass))
        self.assertIsNone(gate.get_secure_sandbox())

    def test_production_defaults_never_allow_synthetic(self):
        self.assertFalse(gp.GradingDependencies.__dataclass_fields__[
            "allow_synthetic_evidence"].default)


class ConstraintVerificationTests(unittest.TestCase):
    HELPER = """
#include <vector>
bool isPrime(long long n) { for (long long d = 2; d * d <= n; ++d) if (n % d == 0) return false; return n >= 2; }
int main() { std::vector<int> v; return isPrime(7) ? 0 : 1; }
"""
    UNCALLED = "bool isPrime(int n) { return n > 1; }\nint main() { return 0; }"

    def statuses(self, src, constraints):
        return [c["status"] for c in check_code_constraints(src, constraints)["checks"]]

    def test_helper_and_container_forms(self):
        self.assertEqual(self.statuses(self.HELPER, [
            "helper_required:isPrime", "helper_required:check",
            "container_required:vector", "container_prohibited:vector",
            "container_required:map"]),
            [SATISFIED, VIOLATED, SATISFIED, VIOLATED, VIOLATED])
        self.assertEqual(self.statuses(self.UNCALLED, ["helper_required:isPrime"]),
                         [INCONCLUSIVE])
        commented = "// bool isPrime(int n) { return 1; }\nint main(){ return 0; }"
        self.assertEqual(self.statuses(commented, ["helper_required:isPrime"]),
                         [VIOLATED])

    def test_violation_and_inconclusive_need_review_without_deduction(self):
        for constraints in (["recursion_required"], ["dynamic_programming_required"]):
            with self.subTest(constraints=constraints):
                result, calls = run(10, validation="100",
                                    code_constraints=constraints)
                self.assertEqual(calls["complete"][0][1]["auto_evaluation_status"],
                                 "NEEDS_REVIEW")
                self.assertEqual(calls["complete"][0][0][3], Decimal("10.00"))
                self.assertEqual(result["verification_report"]["review_status"],
                                 "NEEDS_REVIEW")


class StudentFeedbackRedactionTests(unittest.TestCase):
    def test_teacher_section_removed_and_hidden_data_redacted(self):
        feedback = ("=== Automated test results ===\nTests passed: 8 / 10\n"
                    "=== AI rubric assessment (provisional) ===\n"
                    "- Logic: fails for 700009, expected ANSWER-0009\n"
                    + SECTION_MARKER + "\nTests: 8 passed (failed: [9, 10])")
        hidden = hidden_values([(i, o) for i, o in zip(HIDDEN_INPUTS, HIDDEN_OUTPUTS)]
                               + [("4", "NO\n")])
        out = student_safe_feedback(feedback, hidden)
        self.assertNotIn(SECTION_MARKER, out)
        self.assertNotIn("700009", out)
        self.assertNotIn("ANSWER-0009", out)
        self.assertIn("Tests passed: 8 / 10", out)
        self.assertNotIn("NO", hidden)       # short public outputs not redacted


if __name__ == "__main__":
    unittest.main()
