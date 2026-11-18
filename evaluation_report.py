"""
Structured automatic-evaluation report (teacher-facing).

One report shape for every evaluation mode, so the teacher always sees
what the marks are based on:

  VERIFIED_EXECUTION  real execution evidence exists (secure sandbox, or the
                      recorded run of an owner-approved trusted fixture)
  LLM_ANALYSIS        no execution evidence: the offline Qwen model reviewed
                      the source only. Test status is NOT_RUN and no test
                      marks are suggested - predicted correctness is never
                      reported as tested correctness.
  COMPILATION_FAILED  genuine compiler verdict, no tests, no LLM
  MANUAL              teacher manual-grading fallback

The report never contains hidden test inputs or expected outputs (only
test numbers), and it is never sent to students.

New evaluations embed the report as JSON in the teacher-only feedback
(REPORT_MARKER). Older evaluations get a report derived from their stored
fields (derive_report), marked as such.
"""

import json
import re
from decimal import Decimal

REPORT_VERSION = "evaluation_report_v1"
REPORT_MARKER = "=== Structured evaluation report (JSON, teacher only) ==="

VERIFIED_EXECUTION = "VERIFIED_EXECUTION"
LLM_ANALYSIS = "LLM_ANALYSIS"
COMPILATION_FAILED = "COMPILATION_FAILED"
MANUAL = "MANUAL"

TEST_VERIFIED = "VERIFIED"
TEST_NOT_RUN = "NOT_RUN"

AI_LABEL = "AI analysis of the source code (predicted, NOT tested)"
MODEL_NAME = "qwen2.5-coder:3b (local Ollama)"

CENT = Decimal("0.01")


def _s(value):
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(CENT))


def _list(values):
    return [str(v) for v in (values or []) if isinstance(v, str) and v.strip()]


def build_report(*, mode, evidence_basis, assessment, constraints_text,
                 constraint_report, rubric, ai_result, test_alloc, rubric_alloc,
                 tests_configured, tests_run=0, tests_passed=0,
                 failed_test_numbers=None, test_marks=None, rubric_marks=None,
                 suggested_total=None, reliability_flags=None):
    """Pure: build the report dict from already-validated grading values."""
    verified = mode == VERIFIED_EXECUTION
    if verified and not tests_run:
        raise ValueError("VERIFIED_EXECUTION requires executed tests")
    if not verified and (tests_run or test_marks is not None):
        raise ValueError("Only VERIFIED_EXECUTION may report test results or marks")

    maxima = {r["criterion_name"]: _s(r["max_marks"]) for r in rubric or []}
    ai_criteria = (ai_result or {}).get("criteria") or []
    rubric_rows = [{
        "criterion_name": c["criterion_name"],
        "max_marks": maxima.get(c["criterion_name"]),
        "suggested_marks": _s(c["rating"]),
        "feedback": c.get("explanation"),
        # Without execution there are no failing tests to cite: any number
        # the model invents is dropped here (and flagged by the consistency
        # checks), never shown as a test outcome.
        "cited_failed_tests": (list(c.get("cited_failed_tests") or [])
                               if verified else []),
        "basis": ("AI review informed by verified test results" if verified
                  else "AI code review only (predicted, NOT tested)"),
    } for c in ai_criteria]

    if verified:
        test_section = {
            "status": TEST_VERIFIED,
            "tests_configured": tests_configured,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "tests_failed": tests_run - tests_passed,
            "failed_test_numbers": sorted(failed_test_numbers or []),
            "test_marks": _s(test_marks),
            "test_marks_available": _s(test_alloc),
            "basis": evidence_basis,
        }
    else:
        test_section = {
            "status": TEST_NOT_RUN,
            "tests_configured": tests_configured,
            "tests_run": 0,
            "tests_passed": None,
            "tests_failed": None,
            "failed_test_numbers": [],
            "test_marks": None,
            "test_marks_available": _s(test_alloc),
            "basis": evidence_basis,
            "note": ("The hidden test cases were NOT executed. No pass/fail result "
                     "and no test-case marks are suggested; any test-case marks "
                     "are the teacher's decision."),
        }

    checks = (constraint_report or {}).get("checks", [])
    ai = ai_result or {}
    return {
        "report_version": REPORT_VERSION,
        "evaluation_mode": mode,
        "evidence_basis": evidence_basis,
        "assessment": {
            "assessment_id": assessment.get("assessment_id"),
            "total_marks": _s(assessment.get("total_marks")),
            "test_marks_allocated": _s(test_alloc),
            "rubric_marks_allocated": _s(rubric_alloc),
        },
        "constraints": {
            "approved_text": constraints_text,
            "static_checks": [{"constraint": c["constraint"], "status": c["status"],
                               "evidence": c["evidence"]} for c in checks],
            "summary": ("No code constraints to check" if not checks else
                        "All code constraints SATISFIED" if not
                        (constraint_report or {}).get("requires_review") else
                        "Some code constraints VIOLATED or INCONCLUSIVE - review"),
            "basis": "static source analysis (no execution); never changes marks",
        },
        "test_execution": test_section,
        "logic_analysis": None if ai_result is None else {
            "label": AI_LABEL if not verified else
            "AI analysis of the source code, with verified test results supplied",
            "model": MODEL_NAME,
            "overall": ai.get("overall_feedback") or "",
            "suspected_bugs": _list(ai.get("suspected_bugs")),
            "issues": _list(ai.get("issues")),
            "improvement_suggestions": _list(ai.get("improvement_suggestions")),
        },
        "rubric": rubric_rows,
        "suggested_marks": {
            "test_marks": _s(test_marks),
            "test_marks_basis": ("verified test execution" if verified else
                                 "NOT SUGGESTED - tests not run"),
            "rubric_marks": _s(rubric_marks),
            "rubric_marks_basis": ("AI suggestion (Python-validated criterion "
                                   "marks); teacher review required"
                                   if ai_result is not None else None),
            "suggested_total": _s(suggested_total),
            "total_marks": _s(assessment.get("total_marks")),
            "note": (None if verified else
                     "Suggested total covers rubric marks only; test-case "
                     "marks were not verified."),
        },
        "reliability_flags": list(reliability_flags or []),
        "teacher_approval": {"required": True, "status": "PENDING_REVIEW"},
    }


def report_section(report):
    return "\n" + REPORT_MARKER + "\n" + json.dumps(report, sort_keys=True)


def parse_report(feedback):
    """The embedded report, or None."""
    if not feedback or REPORT_MARKER not in feedback:
        return None
    body = feedback.split(REPORT_MARKER, 1)[1]
    match = re.search(r"\{.*\}", body, re.DOTALL)
    try:
        report = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    return report if isinstance(report, dict) and \
        report.get("report_version") == REPORT_VERSION else None


def mode_for_status(auto_status, tests_run):
    if auto_status == "AI_ANALYSIS_UNVERIFIED":
        return LLM_ANALYSIS
    if auto_status == "COMPILATION_FAILED":
        return COMPILATION_FAILED
    if auto_status in ("MANUALLY_GRADED", "MANUAL_GRADING"):
        return MANUAL
    return VERIFIED_EXECUTION if tests_run else None


def derive_report(*, auto_status, tests_passed, tests_run, suggested_score,
                  total_marks, test_marks, ai_criteria, feedback):
    """Minimal report for evaluations saved before reports were embedded."""
    provenance = None
    match = re.search(r"evidence: (\w+)", feedback or "")
    if match:
        provenance = match.group(1)
    mode = mode_for_status(auto_status, tests_run)
    return {
        "report_version": REPORT_VERSION,
        "derived_from_stored_fields": True,
        "evaluation_mode": mode,
        "evidence_basis": provenance,
        "test_execution": {
            "status": TEST_VERIFIED if mode == VERIFIED_EXECUTION else TEST_NOT_RUN,
            "tests_run": tests_run if mode == VERIFIED_EXECUTION else 0,
            "tests_passed": tests_passed if mode == VERIFIED_EXECUTION else None,
            "test_marks": test_marks,
        },
        "rubric": [{"criterion_name": c["criterion_name"],
                    "suggested_marks": c.get("rating"),
                    "feedback": c.get("explanation")} for c in ai_criteria or []],
        "suggested_marks": {"suggested_total": suggested_score,
                            "total_marks": total_marks},
        "note": "Structured details (constraints, logic analysis) were not "
                "recorded for this older evaluation; see the feedback text.",
    }


def with_teacher_status(report, result_status, final_score, teacher_criteria):
    """Overlay the live approval state (the stored report is written once)."""
    report = dict(report)
    finalized = result_status == "FINALIZED"
    report["teacher_approval"] = {
        "required": True,
        "status": "APPROVED_FINAL" if finalized else "PENDING_REVIEW",
        "final_score": final_score if finalized else None,
        "criterion_approvals": teacher_criteria or [],
    }
    return report
