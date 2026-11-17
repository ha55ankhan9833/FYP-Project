"""
Student-facing result, CONSTRUCTED from explicitly permitted fields.

Nothing from the stored automatic feedback text is copied into the result
except sanitized compiler diagnostics for a genuine compilation failure.
In particular a student never receives: the AI suggested total, AI
explanations, reliability flags, the teacher-only verification report,
evidence provenance, failed test numbers, hidden inputs or expected
outputs. No text redaction is needed because no free text that the AI
wrote (after seeing hidden tests) is published.

Rubric breakdown published to the student:
  TEACHER_APPROVED                      teacher-approved criterion marks
  AI_RECOMMENDATION_ACCEPTED_BY_TEACHER AI criterion marks, only when
                                        test marks + their sum == the
                                        teacher's final total (marks only)
  FINAL_TOTAL_ONLY                      otherwise: no per-criterion marks,
                                        so published marks never disagree
                                        with the final total
"""

from decimal import Decimal, InvalidOperation

from diagnostics_sanitizer import sanitize_compiler_diagnostics
from equal_weight_test_scoring import calculate_equal_weight_score
from grading_allocation import split_allocation

CENT = Decimal("0.01")
# Prefix of the label the development fixture grader puts on its
# feedback; evidence provenance is teacher-only information.
DEV_EVIDENCE_LABEL_PREFIX = "[DEVELOPMENT TEST"
TEACHER_ONLY_MARKER = "=== Python verification report (teacher only) ==="

# Evaluations with no automatic test evidence (infrastructure-failure
# manual grade, and the teacher's explicit manual-grading fallback).
MANUAL_STATUSES = ("MANUAL_GRADING", "MANUALLY_GRADED")

TEACHER_APPROVED = "TEACHER_APPROVED"
AI_ACCEPTED = "AI_RECOMMENDATION_ACCEPTED_BY_TEACHER"
FINAL_TOTAL_ONLY = "FINAL_TOTAL_ONLY"


def _dec(value):
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None


def _s(value):
    return None if value is None else str(value)


def verified_test_marks(auto_status, tests_passed, tests_run, total_marks,
                        validation_percentage):
    """
    (test_marks, test_marks_available) re-derived with the grading rule.
    test_marks is None when no verified test result exists (manual grade).
    """
    test_alloc, _ = split_allocation(total_marks, validation_percentage)
    if auto_status == "COMPILATION_FAILED":
        return Decimal("0.00"), test_alloc
    if auto_status in MANUAL_STATUSES:
        return None, test_alloc
    if test_alloc == 0:
        return Decimal("0.00"), test_alloc
    if not tests_run:
        return None, test_alloc
    return (Decimal(calculate_equal_weight_score(int(tests_passed), int(tests_run),
                                                 test_alloc)["test_score"]),
            test_alloc)


def compilation_diagnostics(feedback):
    """Sanitized compiler output only (no labels, no teacher-only text)."""
    if not feedback:
        return None
    text = feedback.split(TEACHER_ONLY_MARKER, 1)[0]
    lines = [line for line in text.splitlines()
             if not line.lstrip().startswith(DEV_EVIDENCE_LABEL_PREFIX)]
    return sanitize_compiler_diagnostics("\n".join(lines)).strip() or None


def outcome_of(auto_status):
    return {"COMPILATION_FAILED": "COMPILATION_FAILED",
            "MANUAL_GRADING": "MANUAL_GRADE",
            "MANUALLY_GRADED": "MANUALLY_GRADED"}.get(auto_status, "GRADED")


def build_student_result(*, final_score, total_marks, validation_percentage,
                         auto_status, tests_passed, tests_run,
                         instructor_feedback, stored_feedback,
                         rubric_rows, ai_criteria, approved_criteria):
    """
    rubric_rows: [{"criterion_name", "max_marks"}] (assessment rubric)
    ai_criteria: [{"criterion_name", "rating"}] (AI recommendation)
    approved_criteria: [{"criterion_name", "approved_marks", "teacher_comment"}]
                       or None/[] when the teacher approved none
    """
    final = _dec(final_score)
    total = _dec(total_marks)
    test_marks, test_available = verified_test_marks(
        auto_status, tests_passed, tests_run, total, validation_percentage)
    rubric_available = (total - test_available).quantize(CENT)
    maxima = {r["criterion_name"]: _dec(r["max_marks"]) for r in rubric_rows or []}

    criteria, source, rubric_marks = [], FINAL_TOTAL_ONLY, None
    if approved_criteria:
        source = TEACHER_APPROVED
        criteria = [{"criterion_name": c["criterion_name"],
                     "marks": _s(_dec(c["approved_marks"])),
                     "max_marks": _s(maxima.get(c["criterion_name"])),
                     "teacher_comment": c.get("teacher_comment")}
                    for c in approved_criteria]
        rubric_marks = sum(_dec(c["approved_marks"]) for c in approved_criteria)
    elif ai_criteria and test_marks is not None and final is not None:
        ai_sum = sum(_dec(c["rating"]) or Decimal("0") for c in ai_criteria)
        if (test_marks + ai_sum).quantize(CENT) == final:
            source = AI_ACCEPTED
            criteria = [{"criterion_name": c["criterion_name"],
                         "marks": _s(_dec(c["rating"])),
                         "max_marks": _s(maxima.get(c["criterion_name"]))}
                        for c in ai_criteria]
            rubric_marks = ai_sum
    if rubric_marks is None and test_marks is not None and final is not None:
        remainder = (final - test_marks).quantize(CENT)
        if Decimal("0") <= remainder <= rubric_available:
            rubric_marks = remainder

    outcome = outcome_of(auto_status)
    summary = []
    if outcome == "MANUALLY_GRADED":
        # No tests ran: do not present 0/0 as a test result.
        tests_passed = tests_run = None
        summary.append("Your teacher graded this submission manually.")
    elif outcome == "COMPILATION_FAILED":
        summary.append("Your program did not compile, so no tests were run. "
                       "See the compiler diagnostics.")
    elif tests_run:
        summary.append(f"{tests_passed} of {tests_run} hidden tests passed.")
    if instructor_feedback:
        summary.append("Your teacher left feedback on this submission.")

    return {
        "outcome": outcome,
        "final_score": _s(final),
        "total_marks": _s(total),
        "tests_passed": tests_passed,
        "tests_run": tests_run,
        "test_marks": _s(test_marks),
        "test_marks_available": _s(test_available.quantize(CENT)),
        "rubric_marks": _s(rubric_marks.quantize(CENT) if rubric_marks is not None
                           else None),
        "rubric_marks_available": _s(rubric_available),
        "rubric_source": source,
        "criteria": criteria,
        "instructor_feedback": instructor_feedback,
        "compilation_diagnostics": (compilation_diagnostics(stored_feedback)
                                    if outcome == "COMPILATION_FAILED" else None),
        "summary": summary,
    }
