"""
LLM ANALYSIS MODE: automatic evaluation of a submission for which no safe
execution evidence exists (no secure sandbox, no approved recorded run).

    claim -> load submission (ownership-checked) -> config, rubric, approved
    constraints -> static code-constraint checks -> offline Qwen reviews the
    SOURCE ONLY against the problem, constraints and rubric -> Python
    validates every criterion mark -> Evaluation saved as
    AI_ANALYSIS_UNVERIFIED (tests_run = 0) with a structured report ->
    teacher reviews and finalizes through the existing API.

What it never does
- Compile or execute anything. No sandbox is used or created; the secure
  execution gate is not consulted for permission and is not changed.
- Send hidden test inputs or expected outputs to the model (only the
  number of configured tests, which the model is told were NOT run).
- Report pass/fail, award test-case marks, or label predicted correctness
  as tested. The suggested total covers rubric marks only.
- Run when a real sandbox exists: then the normal execution pipeline
  must grade the job, so the job is released untouched.

Failures (Ollama down, invalid AI output, missing rubric) release the job
exactly like the execution pipeline does: no Evaluation, no automatic zero.
"""

import re
from decimal import Decimal

from code_constraint_checker import check_code_constraints
from evaluation_report import (LLM_ANALYSIS, build_report, report_section)
from grading_allocation import split_allocation
from grading_pipeline import (MAX_ATTEMPTS, GradingDependencies,
                              InfrastructureFailure, OwnershipLost)
from grading_rubric_allocation_adapter import validate_rubric_allocation
from grading_verification import VerificationFailure, verify_rubric
from rubric_ai_grader import RubricGradingUnavailable

AUTO_STATUS = "AI_ANALYSIS_UNVERIFIED"
EVIDENCE_BASIS = "none - source code analysis only (no compilation, no execution)"
LEASE_SECONDS = 600              # Qwen 3B on CPU can take a few minutes
WORKER_PREFIX = "llm-analysis"

# AI prose that talks about test outcomes although no test was run.
_TEST_CLAIM = re.compile(
    r"\b(pass(es|ed)?|fail(s|ed)?)\b[^.]{0,60}\btests?\b|\btests?\b[^.]{0,60}"
    r"\b(pass(es|ed)?|fail(s|ed)?)\b", re.IGNORECASE)


def unverified_test_claims(ai_result):
    texts = [c.get("explanation") or "" for c in ai_result.get("criteria", [])]
    texts.append(ai_result.get("overall_feedback") or "")
    return [t for t in texts if _TEST_CLAIM.search(t)]


def compose_feedback(report):
    """Teacher-only text; the embedded JSON report is the structured copy."""
    marks = report["suggested_marks"]
    lines = [
        "=== AUTOMATIC EVALUATION - LLM ANALYSIS MODE (NO TEST EXECUTION) ===",
        "The submission was NOT compiled or run. Hidden tests: "
        f"{report['test_execution']['tests_configured']} configured, NOT RUN.",
        "Test-case marks: NOT SUGGESTED (unverified) - teacher decision.",
        "",
        "=== Code constraints (static analysis; no marks changed) ===",
    ]
    lines += [f"- {c['constraint']}: {c['status']} ({c['evidence']})"
              for c in report["constraints"]["static_checks"]] or ["- none"]
    lines += ["", f"=== {report['logic_analysis']['label']} ===",
              f"Model: {report['logic_analysis']['model']}"]
    for row in report["rubric"]:
        lines.append(f"- {row['criterion_name']}: {row['suggested_marks']} / "
                     f"{row['max_marks']} - {row['feedback']}")
    if report["logic_analysis"]["overall"]:
        lines.append("Overall: " + report["logic_analysis"]["overall"])
    for title, key in (("Suspected bugs (AI inference, not demonstrated by a test)",
                        "suspected_bugs"), ("Issues", "issues"),
                       ("Suggestions", "improvement_suggestions")):
        if report["logic_analysis"][key]:
            lines.append(title + ":")
            lines += [f"- {s}" for s in report["logic_analysis"][key]]
    if report["reliability_flags"]:
        lines.append("Reliability flags (teacher review required):")
        lines += [f"- {f}" for f in report["reliability_flags"]]
    lines += ["", f"Suggested rubric marks: {marks['rubric_marks']} / "
                  f"{report['assessment']['rubric_marks_allocated']}",
              f"Suggested total (rubric only, unverified): {marks['suggested_total']} "
              f"/ {marks['total_marks']} - not final until the teacher approves."]
    return "\n".join(lines)


def _renew(deps, job_id, worker_id, token):
    if deps.renew_lease(job_id, worker_id, token, LEASE_SECONDS) is None:
        raise OwnershipLost()


def _analyse(claim, worker_id, deps):
    job_id, token = claim["job_id"], claim["claim_token"]

    job = deps.prepare(job_id, worker_id, token)
    if job is None:
        raise OwnershipLost()
    submission_id = job["submission_id"]

    marks = deps.load_assessment_marks(submission_id)
    if marks is None:
        raise InfrastructureFailure("ASSESSMENT_NOT_FOUND")
    assessment_id = int(marks["assessment_id"])
    config = deps.load_config(assessment_id)
    context = deps.load_context(assessment_id)
    if config is None or context is None:
        raise InfrastructureFailure("ASSESSMENT_NOT_FOUND")

    # A real sandbox means real evidence is possible: do not substitute AI.
    if deps.get_sandbox() is not None:
        raise InfrastructureFailure("EXECUTION_AVAILABLE",
                                    "secure sandbox configured; use the execution pipeline")

    test_alloc, rubric_alloc = split_allocation(config["total_marks"],
                                                config["validation_percentage"])
    if rubric_alloc <= 0:
        raise InfrastructureFailure("NO_RUBRIC_FOR_ANALYSIS",
                                    "LLM analysis needs rubric marks; grade manually")
    try:
        rubric = deps.load_rubric(assessment_id)
        validate_rubric_allocation(
            [{"criterion_name": c["criterion_name"], "max_marks": c["max_marks"]}
             for c in rubric], rubric_alloc)
    except Exception as error:
        raise InfrastructureFailure("RUBRIC_NOT_CONFIGURED", str(error))

    tests_configured = 0
    if test_alloc > 0:
        try:
            tests_configured = len(deps.load_test_cases(assessment_id))
        except ValueError as error:
            raise InfrastructureFailure("TEST_CASES_NOT_CONFIGURED", str(error))

    constraint_report = check_code_constraints(
        job["source_code"], context.get("code_constraints") or [])
    # The model is told explicitly that no test was executed.
    evidence = {"tests_run": 0, "tests_passed": 0, "cases": [],
                "provenance": "no_execution", "synthetic": False,
                "development_only": False,
                "code_constraint_checks": constraint_report}

    _renew(deps, job_id, worker_id, token)
    try:
        ai_result = deps.grade_rubric(context, rubric, rubric_alloc,
                                      job["source_code"], evidence)
    except RubricGradingUnavailable as error:
        raise InfrastructureFailure("AI_GRADING_UNAVAILABLE", str(error))
    except ValueError as error:
        raise InfrastructureFailure("AI_GRADING_CONFIG_ERROR", str(error))

    rubric_total = sum((Decimal(c["rating"]) for c in ai_result["criteria"]),
                       Decimal("0"))
    try:
        verify_rubric(ai_result, rubric, rubric_alloc,
                      {"rubric_score": str(rubric_total)})
    except VerificationFailure as error:
        raise InfrastructureFailure("VERIFICATION_FAILED", str(error))

    flags = list(ai_result.get("reliability_flags") or [])
    if unverified_test_claims(ai_result):
        flags.append("[UNVERIFIED_TEST_CLAIM] The AI text mentions passing or "
                     "failing tests, but no test was run - treat as prediction.")
    if constraint_report.get("requires_review"):
        flags.append("[CONSTRAINT_NOT_VERIFIED] Code constraints not all SATISFIED")
    flags.append("[NO_EXECUTION_EVIDENCE] Test-case behaviour was not verified.")

    report = build_report(
        mode=LLM_ANALYSIS, evidence_basis=EVIDENCE_BASIS,
        assessment={"assessment_id": assessment_id,
                    "total_marks": config["total_marks"]},
        constraints_text=context.get("constraints"),
        constraint_report=constraint_report, rubric=rubric, ai_result=ai_result,
        test_alloc=test_alloc, rubric_alloc=rubric_alloc,
        tests_configured=tests_configured, rubric_marks=rubric_total,
        suggested_total=rubric_total, reliability_flags=flags)
    feedback = (compose_feedback(report) + report_section(report))[:20000]

    from grading_job_completion_service import GradingJobOwnershipError
    _renew(deps, job_id, worker_id, token)
    try:
        saved = deps.complete(job_id, worker_id, token, rubric_total, feedback,
                              0, 0, criteria=ai_result["criteria"],
                              auto_evaluation_status=AUTO_STATUS)
    except GradingJobOwnershipError:
        raise OwnershipLost()

    return {**saved, "outcome": "ANALYSED", "evaluation_mode": LLM_ANALYSIS,
            "report": report, "llm_called": True}


def analyse_claimed_job(claim, worker_id, deps=None):
    """Same failure policy as grading_pipeline.process_claimed_job."""
    deps = deps or GradingDependencies()
    job_id, token = claim["job_id"], claim["claim_token"]
    try:
        return _analyse(claim, worker_id, deps)
    except OwnershipLost:
        return {"job_id": job_id, "outcome": "OWNERSHIP_LOST",
                "evaluation_written": False}
    except InfrastructureFailure as failure:
        released = deps.release(job_id, worker_id, token, failure.code,
                                failure.detail, MAX_ATTEMPTS)
        return {"job_id": job_id, "outcome": "INFRASTRUCTURE_FAILURE",
                "error_code": failure.code,
                "job_status": released["job_status"] if released else None,
                "evaluation_written": False}
    except Exception as error:     # never let an unknown error zero a student
        released = deps.release(job_id, worker_id, token, "UNEXPECTED_WORKER_ERROR",
                                type(error).__name__ + ": " + str(error), MAX_ATTEMPTS)
        return {"job_id": job_id, "outcome": "INFRASTRUCTURE_FAILURE",
                "error_code": "UNEXPECTED_WORKER_ERROR",
                "job_status": released["job_status"] if released else None,
                "evaluation_written": False}


def run_analysis_worker_once(worker_id, deps=None, claim_fn=None):
    """
    Claim at most one PENDING job and analyse it. Refuses to run when a
    secure sandbox exists (the execution pipeline owns those jobs).
    """
    deps = deps or GradingDependencies()
    if deps.get_sandbox() is not None:
        return {"outcome": "EXECUTION_AVAILABLE", "claimed": False}
    deps.mark_exhausted()
    claim = (claim_fn or deps.claim)(worker_id, LEASE_SECONDS, MAX_ATTEMPTS)
    if claim is None:
        return {"outcome": "NO_JOB", "claimed": False}
    result = analyse_claimed_job(claim, worker_id, deps)
    result["claimed"] = True
    return result
