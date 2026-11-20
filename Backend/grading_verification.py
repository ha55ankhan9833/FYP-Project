"""
Python grading verification layer (verification_v1).

Runs AFTER the pipeline has scored a submission and BEFORE anything is
saved. It independently re-derives every deterministic number and builds
a structured, evidence-grounded report for the teacher.

Policy
- A deterministic mismatch (test counts, re-compared outputs, equal-weight
  test score, rubric total, combined total, criterion set) is an internal
  error -> VerificationFailure -> the job is released for retry. It is
  never saved and never turned into a zero.
- Questionable but structurally valid AI output is NOT rejected: its marks
  are preserved and the report sets review_status = NEEDS_REVIEW.
- No mark is ever adjusted here.
- The report contains NO hidden test inputs, expected outputs or student
  outputs - only test numbers and counts - so it is safe to log and to
  show the teacher; students never see it (see evaluation_review_service).

This layer verifies arithmetic and consistency. It does not and cannot
verify that the AI's judgement of code quality is correct.
"""

from decimal import Decimal, ROUND_HALF_UP

from equal_weight_test_scoring import calculate_equal_weight_score
from output_comparison import DEFAULT_POLICY, outputs_match
from test_case_policy import REQUIRED_TEST_CASE_COUNT

VERSION = "verification_v1"
SECTION_MARKER = "=== Python verification report (teacher only) ==="
CENT = Decimal("0.01")


class VerificationFailure(RuntimeError):
    """A deterministic value did not re-verify; nothing may be saved."""


def _d(value):
    return Decimal(str(value))


def _require(condition, message):
    if not condition:
        raise VerificationFailure(message)


def verify_tests(evidence, test_alloc, score):
    checks = []
    test_alloc = _d(test_alloc)

    if test_alloc == 0:
        _require(evidence["tests_run"] == 0 and evidence["tests_passed"] == 0,
                 "Tests reported although no test marks are allocated")
        _require(_d(score["test_score"]) == 0, "Non-zero test score with zero "
                 "test allocation")
        return {"run": 0, "passed": 0, "failed": 0, "failed_test_numbers": [],
                "test_marks_allocated": "0.00", "test_score": "0.00",
                "marks_per_test": None}, ["no_test_allocation"]

    cases = evidence.get("cases", [])
    run, passed = evidence.get("tests_run"), evidence.get("tests_passed")

    _require(run == REQUIRED_TEST_CASE_COUNT,
             f"Expected exactly {REQUIRED_TEST_CASE_COUNT} test results, got {run}")
    _require(len(cases) == run, "Case list length differs from tests_run")
    numbers = [c.get("test_number") for c in cases]
    _require(len(set(numbers)) == run, "Duplicate test numbers in evidence")
    checks.append("exactly_10_unique_results")

    recount = sum(c.get("passed") is True for c in cases)
    _require(recount == passed, f"tests_passed={passed} but {recount} cases "
             f"are marked passed")
    checks.append("pass_count_consistent")

    for case in cases:
        if "expected_output" in case and "student_output" in case:
            again = (case.get("execution_status") == "completed"
                     and outputs_match(case["student_output"],
                                       case["expected_output"], DEFAULT_POLICY))
            _require(again == case["passed"],
                     f"Test {case['test_number']} pass flag disagrees with "
                     f"{DEFAULT_POLICY} comparison")
    checks.append(f"outputs_recompared_{DEFAULT_POLICY.lower()}")

    expected_score = calculate_equal_weight_score(passed, run, test_alloc)["test_score"]
    _require(_d(score["test_score"]) == _d(expected_score),
             f"Test score {score['test_score']} != equal-weight "
             f"{expected_score}")
    _require(_d(score["test_marks_allocated"]) == test_alloc,
             "Test allocation differs from configuration")
    checks.append("equal_weight_test_score")

    return {
        "run": run, "passed": passed, "failed": run - passed,
        "failed_test_numbers": sorted(c["test_number"] for c in cases
                                      if c.get("passed") is False),
        "test_marks_allocated": str(test_alloc.quantize(CENT)),
        "marks_per_test": str(test_alloc / run),          # exact, display only
        "test_score": str(_d(expected_score).quantize(CENT)),
        "formula": f"ROUND_HALF_UP({test_alloc} x {passed} / {run}, 2)",
    }, checks


def verify_rubric(ai_result, rubric, rubric_alloc, score):
    rubric_alloc = _d(rubric_alloc)
    if rubric_alloc == 0:
        _require(ai_result is None, "AI result present without rubric allocation")
        _require(_d(score["rubric_score"]) == 0, "Rubric score without allocation")
        return None, ["no_rubric_allocation"]

    _require(ai_result is not None, "Rubric allocated but no AI result")
    maxima = {r["criterion_name"]: _d(r["max_marks"]) for r in rubric}
    names = [c["criterion_name"] for c in ai_result["criteria"]]
    _require(sorted(names) == sorted(maxima) and len(set(names)) == len(names),
             "AI criteria do not exactly match the teacher rubric")

    total = Decimal("0")
    rows = []
    for c in ai_result["criteria"]:
        mark = _d(c["rating"])
        _require(mark == mark.quantize(CENT) and 0 <= mark <= maxima[c["criterion_name"]],
                 f"Invalid mark for {c['criterion_name']}")
        total += mark
        rows.append({"criterion_name": c["criterion_name"], "mark": str(mark),
                     "max_marks": str(maxima[c["criterion_name"]]),
                     "cited_failed_tests": list(c.get("cited_failed_tests") or [])})

    _require(total == _d(ai_result["suggested_score"]) == _d(score["rubric_score"]),
             "Rubric total is not the Python sum of criterion marks")
    _require(total <= rubric_alloc, "Rubric total exceeds allocation")

    return {"criteria": rows, "python_total": str(total.quantize(CENT)),
            "allocated": str(rubric_alloc.quantize(CENT)),
            "total_source": "python_sum_of_validated_criteria"}, \
        ["rubric_criteria_exact", "rubric_python_total"]


def verify_and_report(config, score, evidence, ai_result, rubric,
                      constraint_report, test_alloc, rubric_alloc):
    """Returns the verification report; raises VerificationFailure."""

    tests, checks = verify_tests(evidence, test_alloc, score)
    rubric_part, rubric_checks = verify_rubric(ai_result, rubric,
                                               rubric_alloc, score)
    checks += rubric_checks

    total_marks = _d(config["total_marks"])
    combined = (_d(tests["test_score"]) +
                (_d(rubric_part["python_total"]) if rubric_part else 0)
                ).quantize(CENT, rounding=ROUND_HALF_UP)
    _require(combined == _d(score["suggested_score"]),
             f"Suggested total {score['suggested_score']} != {combined}")
    _require(0 <= combined <= total_marks, "Suggested total outside 0..max")
    checks.append("suggested_total_within_maximum")

    warnings = [{"source": "ai_consistency", "message": f}
                for f in (ai_result or {}).get("reliability_flags", [])]
    constraint_checks = [{"constraint": c["constraint"], "status": c["status"],
                          "evidence": c["evidence"]}
                         for c in (constraint_report or {}).get("checks", [])]
    if (constraint_report or {}).get("requires_review") and not any(
            "CONSTRAINT_NOT_VERIFIED" in w["message"] for w in warnings):
        warnings.append({"source": "code_constraints",
                         "message": "Code constraints not all SATISFIED"})

    return {
        "version": VERSION,
        "provenance": evidence.get("provenance", "sandbox_execution"),
        "comparison_policy": DEFAULT_POLICY,
        "tests": tests,
        "rubric": rubric_part,
        "code_constraints": constraint_checks,
        "warnings": warnings,
        "suggested_total": str(combined),
        "total_marks": str(total_marks.quantize(CENT)),
        "review_status": "NEEDS_REVIEW" if warnings else "COMPLETED",
        "teacher_approval_required": True,
        "marks_adjusted_by_verification": False,
        "deterministic_checks_passed": checks,
    }


def report_to_text(report):
    """Teacher-only feedback section; contains no hidden test data."""
    t, r = report["tests"], report["rubric"]
    lines = ["", SECTION_MARKER,
             f"Version: {report['version']} | evidence: {report['provenance']} "
             f"| comparison: {report['comparison_policy']}",
             f"Tests: {t['passed']} passed, {t['failed']} failed of {t['run']}"
             + (f" (failed: {t['failed_test_numbers']})" if t["failed"] else ""),
             f"Test marks: {t['test_score']} / {t['test_marks_allocated']}"
             + (f" = {t['formula']}" if t.get("formula") else "")]
    if r:
        lines.append("Rubric (Python total of AI criterion marks): "
                     + ", ".join(f"{c['criterion_name']} {c['mark']}/{c['max_marks']}"
                                 for c in r["criteria"])
                     + f" = {r['python_total']} / {r['allocated']}")
    for c in report["code_constraints"]:
        lines.append(f"Constraint {c['constraint']}: {c['status']}")
    for w in report["warnings"]:
        lines.append(f"Warning ({w['source']}): {w['message']}")
    lines.append(f"Suggested total: {report['suggested_total']} / "
                 f"{report['total_marks']} | status: {report['review_status']} "
                 f"| teacher approval required")
    lines.append("Deterministic checks passed: "
                 + ", ".join(report["deterministic_checks_passed"]))
    return "\n".join(lines)
