"""
Ollama rubric grading for the production grading pipeline.

Responsibilities split:
- Python (grading_pipeline / grading_rubric_allocation_adapter) owns
  test pass/fail counts and the equal-weight test score.
- Ollama only proposes a mark + explanation per teacher rubric criterion
  (criterion-only contract, ollama_grading_service.grade_rubric_criteria).
  It is never asked for a total: small local models proved unable to add
  their own ratings reliably (live tests, 2026-11-17).
- Python validates every criterion, sums the marks in Decimal and the
  adapter combines that with the deterministic test score. Any invalid
  criterion rejects the whole AI result (no partial acceptance, no
  silent clamping or rounding).

Model accuracy is NOT established: results are suggestions that the
teacher must review and finalize.
"""

from decimal import Decimal

from grading_consistency_checks import analyse_ai_result
from grading_reliability_guard import check_grading_reliability
from ollama_grading_service import GradingServiceError, grade_rubric_criteria


MAX_EVIDENCE_CASES = 10
MAX_EVIDENCE_TEXT = 500


class RubricGradingUnavailable(RuntimeError):
    """Ollama unreachable, timed out, or returned unusable output."""


def _clip(text):
    if text is None:
        return None
    text = str(text)
    if len(text) <= MAX_EVIDENCE_TEXT:
        return text
    return text[:MAX_EVIDENCE_TEXT] + "...[truncated]"


def build_llm_evidence(execution_evidence):
    """
    Compact, verified-only evidence for the model.
    Never invents outputs; when no tests were run, says so.
    """

    tests_run = execution_evidence.get("tests_run", 0)
    tests_passed = execution_evidence.get("tests_passed", 0)

    # Failing tests first so they are never cut off by the case limit.
    ordered = sorted(execution_evidence.get("cases", []),
                     key=lambda c: (c.get("passed") is not False,
                                    c.get("test_number", 0)))
    cases = [
        {
            "test_number": case["test_number"],
            "input": _clip(case.get("input_data")),
            "execution_status": case["execution_status"],
            "passed": case["passed"],
            "expected_output": _clip(case["expected_output"]),
            "student_output": _clip(case["student_output"])
        }
        for case in ordered[:MAX_EVIDENCE_CASES]
    ]
    failed_numbers = sorted(c["test_number"]
                            for c in execution_evidence.get("cases", [])
                            if c.get("passed") is False)
    constraint_checks = [
        {"constraint": c["constraint"], "status": c["status"],
         "evidence": c["evidence"]}
        for c in (execution_evidence.get("code_constraint_checks") or {})
        .get("checks", [])
    ]

    if execution_evidence.get("provenance") == "trusted_fixture_recorded":
        evidence_type = "trusted_fixture_recorded_execution"
    elif execution_evidence.get("synthetic") is True:
        evidence_type = "synthetic_development_fixture"
    elif tests_run:
        evidence_type = "sandbox_execution_results"
    else:
        evidence_type = "no_test_execution"

    instruction = (
        "Test-case marks are calculated separately by deterministic "
        "code and are NOT yours to award. Mark ONLY the rubric "
        "criteria listed. Use these results as factual evidence of "
        "behaviour; do not re-score the tests and do not total marks."
    )
    if evidence_type == "no_test_execution":
        # LLM analysis mode: there is no behaviour evidence at all.
        instruction += (
            " NO TESTS WERE COMPILED OR RUN for this submission. Judge the "
            "code only by reading it. Do NOT cite any test number, do NOT "
            "claim that any test passed or failed, and leave "
            "cited_failed_tests and observed_failures empty. Report possible "
            "defects as suspected_bugs. Judge code quality (readability, "
            "naming, structure) separately from correctness."
        )

    return {
        "evidence_type": evidence_type,
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "failed_test_numbers": failed_numbers,
        "cases_shown": len(cases),
        "cases": cases,
        "code_constraint_checks": constraint_checks,
        "grading_instruction": instruction
    }


def grade_rubric_with_ollama(
    context,
    rubric,
    rubric_marks_allocated,
    source_code,
    execution_evidence,
    grader=grade_rubric_criteria
):
    """
    Criterion-only contract: the model marks each criterion; PYTHON sums.

    Returns:
      {
        "criteria": [{"criterion_name", "rating" (str), "explanation"}],
        "suggested_score": str,   # rubric total = Python sum of validated marks
        "rubric_total_source": "python_sum_of_validated_criteria",
        "contract": "criterion_only_v1",
        "overall_feedback": str, "issues": [str],
        "improvement_suggestions": [str],
        "ignored_model_fields": [str],
        "reliability_flags": [str], "manual_review_required": bool
      }
    Raises RubricGradingUnavailable for infrastructure/model failures.
    Raises ValueError for configuration errors (bad rubric).
    """

    allocation = Decimal(str(rubric_marks_allocated))

    if allocation <= 0:
        raise ValueError("Rubric grading requested with zero allocation")

    llm_rubric = [
        {
            "criterion_name": item["criterion_name"],
            "max_marks": str(item["max_marks"]),
            "description": item.get("description") or ""
        }
        for item in rubric
    ]

    rubric_maximum = sum((Decimal(str(item["max_marks"])) for item in rubric),
                         Decimal("0"))
    if rubric_maximum != allocation:
        raise ValueError("Rubric criteria do not sum to the rubric allocation")

    try:
        result = grader(
            problem=context["problem"],
            input_format=context["input_format"],
            output_format=context["output_format"],
            constraints=context["constraints"],
            rubric=llm_rubric,
            source_code=source_code,
            test_evidence=build_llm_evidence(execution_evidence)
        )
    except GradingServiceError as error:
        raise RubricGradingUnavailable(str(error)) from error

    # Defence in depth: re-validate the parser's output and sum in Decimal.
    max_by_name = {
        item["criterion_name"]: Decimal(str(item["max_marks"]))
        for item in rubric
    }
    returned = result.get("criteria")
    if not isinstance(returned, list) or \
            sorted(c.get("criterion_name") for c in returned) != sorted(max_by_name):
        raise RubricGradingUnavailable("AI criteria do not match the rubric")

    criteria = []
    total = Decimal("0")

    for item in returned:
        name = item["criterion_name"]
        mark = item.get("mark")
        if not isinstance(mark, Decimal) or not mark.is_finite() \
                or mark != mark.quantize(Decimal("0.01")) \
                or not Decimal("0") <= mark <= max_by_name[name]:
            raise RubricGradingUnavailable("AI mark failed re-validation")

        total += mark
        criteria.append({
            "criterion_name": name,
            "rating": str(mark),
            "explanation": item["explanation"],
            "cited_failed_tests": list(item.get("cited_failed_tests", []))
        })

    if total > allocation:      # impossible if every mark <= its maximum
        raise RubricGradingUnavailable("Rubric total exceeds allocation")

    flags = []
    tests_run = execution_evidence.get("tests_run", 0)

    if tests_run:
        guard = check_grading_reliability(
            {"suggested_score": float(total)},
            {
                "tests_run": tests_run,
                "tests_passed": execution_evidence.get("tests_passed", 0)
            },
            float(allocation)
        )
        flags = list(guard["reliability_flags"])

    consistency = analyse_ai_result(
        result, rubric, execution_evidence,
        execution_evidence.get("code_constraint_checks"))
    flags += [f"[{f['code']}{' - ' + f['criterion'] if f.get('criterion') else ''}] "
              f"{f['message']}" for f in consistency]

    suggestions = [
        s for s in result.get("improvement_suggestions", [])
        if isinstance(s, str)
    ]

    issues = [s for s in result.get("issues", []) if isinstance(s, str)]

    return {
        "criteria": criteria,
        "suggested_score": str(total),
        "rubric_total_source": "python_sum_of_validated_criteria",
        "contract": result.get("contract", "criterion_only_v1"),
        "ignored_model_fields": list(result.get("ignored_fields", [])),
        "observed_failures": list(result.get("observed_failures", [])),
        "suspected_bugs": list(result.get("suspected_bugs", [])),
        "consistency_flags": consistency,
        "overall_feedback": result.get("overall_feedback", ""),
        "issues": issues,
        "improvement_suggestions": suggestions,
        "reliability_flags": flags,
        "manual_review_required": bool(flags)
    }
