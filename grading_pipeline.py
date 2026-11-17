"""
End-to-end grading pipeline for one claimed GradingJob.

    claim -> load submission (ownership-checked) -> load config,
    rubric, approved test cases -> sandbox compile -> sandbox run
    -> validate execution evidence -> (Ollama rubric grading if
    rubric allocation > 0) -> Python score calculation
    -> atomic Evaluation + EvaluationCriterion + job completion.

Outcome rules
- Genuine compiler verdict COMPILATION_FAILED from the sandbox
  -> suggested score 0, no LLM call (trusted evidence).
- Anything else that goes wrong (sandbox/compiler unavailable,
  timeouts in infrastructure, Ollama down, invalid AI output,
  missing rubric or tests, unexpected exceptions)
  -> job released (PENDING / FAILED after max attempts),
     NO Evaluation row, NO automatic zero.
- If the worker loses its claim at any point it stops and writes
  nothing (stale-worker rejection is also enforced in SQL).

No function in this module executes student code on the host.
Execution happens only inside the object returned by
secure_execution_gate.get_secure_sandbox(), which is None today.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from grading_allocation import split_allocation
from grading_rubric_allocation_adapter import (
    combine_grading_results,
    validate_rubric_allocation,
)
from rubric_ai_grader import RubricGradingUnavailable
from secure_execution_gate import SandboxInfrastructureError
from code_constraint_checker import check_code_constraints
from grading_verification import (VerificationFailure, report_to_text,
                                  verify_and_report)
from student_grading_evidence_builder import build_student_grading_evidence
from test_case_policy import REQUIRED_TEST_CASE_COUNT


LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
# Evidence sources that only the development harnesses may use.
DEVELOPMENT_PROVENANCES = frozenset({"synthetic_development",
                                     "trusted_fixture_recorded"})


class OwnershipLost(Exception):
    pass


class InfrastructureFailure(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _default(name):
    """Lazy import so tests can run without pyodbc/DB modules loaded."""

    def resolve():
        if name == "claim":
            from grading_job_claim_service import claim_next_grading_job
            return claim_next_grading_job
        if name == "mark_exhausted":
            from grading_job_retry_service import mark_exhausted_grading_jobs
            return mark_exhausted_grading_jobs
        if name == "prepare":
            from grading_worker_preparation import prepare_claimed_grading_job
            return prepare_claimed_grading_job
        if name == "renew_lease":
            from grading_job_lease_service import renew_grading_job_lease
            return renew_grading_job_lease
        if name == "load_assessment_marks":
            from grading_assessment_marks_service import (
                load_submission_assessment_marks)
            return load_submission_assessment_marks
        if name == "load_config":
            from grading_configuration_service import load_grading_configuration
            return load_grading_configuration
        if name == "load_context":
            from grading_configuration_service import (
                load_assessment_problem_context)
            return load_assessment_problem_context
        if name == "load_rubric":
            from assessment_rubric_service import load_assessment_rubric
            return load_assessment_rubric
        if name == "load_test_cases":
            from grading_test_case_loader import load_grading_test_cases
            return load_grading_test_cases
        if name == "get_sandbox":
            from secure_execution_gate import get_secure_sandbox
            return get_secure_sandbox
        if name == "grade_rubric":
            from rubric_ai_grader import grade_rubric_with_ollama
            return grade_rubric_with_ollama
        if name == "complete":
            from grading_job_completion_service import complete_grading_job
            return complete_grading_job
        if name == "complete_compilation_failure":
            from grading_compilation_failure_completion import (
                complete_compilation_failure_job)
            return complete_compilation_failure_job
        if name == "release":
            from grading_job_failure_service import (
                release_job_after_infrastructure_failure)
            return release_job_after_infrastructure_failure
        raise KeyError(name)

    return field(default_factory=resolve)


@dataclass
class GradingDependencies:
    claim: Callable = _default("claim")
    mark_exhausted: Callable = _default("mark_exhausted")
    prepare: Callable = _default("prepare")
    renew_lease: Callable = _default("renew_lease")
    load_assessment_marks: Callable = _default("load_assessment_marks")
    load_config: Callable = _default("load_config")
    load_context: Callable = _default("load_context")
    load_rubric: Callable = _default("load_rubric")
    load_test_cases: Callable = _default("load_test_cases")
    get_sandbox: Callable = _default("get_sandbox")
    grade_rubric: Callable = _default("grade_rubric")
    complete: Callable = _default("complete")
    complete_compilation_failure: Callable = _default(
        "complete_compilation_failure")
    release: Callable = _default("release")
    # Only dev_tools/grading_preview_harness sets this. Production
    # dependencies keep False, so synthetic evidence is always refused.
    allow_synthetic_evidence: bool = False


# ----------------------------------------------------------------------

def compose_feedback(score, evidence, ai_result, constraint_report=None):
    """
    Keep deterministic test results, static constraint checks and AI
    rubric feedback separate. Hidden test INPUTS are never written here
    (only test numbers), because this text can later reach students.
    """

    failed = sorted(c["test_number"] for c in evidence.get("cases", [])
                    if c.get("passed") is False)
    lines = [
        "=== Automated test results (deterministic, equal weight) ===",
        f"Tests passed: {evidence['tests_passed']} / {evidence['tests_run']}",
        f"Test marks: {score['test_score']} / "
        f"{score['test_marks_allocated']}",
    ]
    if failed:
        lines.append(f"Failed test numbers: {failed}")

    checks = (constraint_report or {}).get("checks", [])
    if checks:
        lines += ["", "=== Code constraints (static analysis; no marks "
                      "changed) ==="]
        lines += [f"- {c['constraint']}: {c['status']} ({c['evidence']})"
                  for c in checks]

    if ai_result is None:
        lines += ["", "=== Rubric ===",
                  "No rubric marks allocated (validation percentage 100%)."]
    else:
        lines += [
            "",
            "=== AI rubric assessment (provisional - requires teacher "
            "review) ===",
            f"Rubric marks: {score['rubric_score']} / "
            f"{score['rubric_marks_allocated']}",
        ]
        for item in ai_result["criteria"]:
            cited = item.get("cited_failed_tests") or []
            lines.append(
                f"- {item['criterion_name']}: {item['rating']} - "
                f"{item['explanation']}"
                + (f" [cites failing tests {cited}]" if cited else "")
            )
        if ai_result.get("overall_feedback"):
            lines += ["", "Overall: " + ai_result["overall_feedback"]]
        if ai_result.get("observed_failures"):
            lines.append("Observed failures (AI, from supplied evidence):")
            lines += [f"- test {o['test_number']}: {o['description']}"
                      for o in ai_result["observed_failures"]]
        if ai_result.get("suspected_bugs"):
            lines.append("Suspected bugs (AI inference, not demonstrated "
                         "by a test):")
            lines += [f"- {s}" for s in ai_result["suspected_bugs"]]
        if ai_result.get("issues"):
            lines.append("Issues found:")
            lines += [f"- {s}" for s in ai_result["issues"]]
        if ai_result.get("improvement_suggestions"):
            lines.append("Suggestions:")
            lines += [f"- {s}" for s in ai_result["improvement_suggestions"]]
        if ai_result.get("reliability_flags"):
            lines.append("Reliability flags (manual review required):")
            lines += [f"- {f}" for f in ai_result["reliability_flags"]]

    if ai_result is None and (constraint_report or {}).get("requires_review"):
        lines += ["Review required: code constraints are not all verified."]

    lines += ["", f"Suggested total: {score['suggested_score']} / "
                  f"{score['total_marks']} (not final until approved "
                  "by the teacher)"]

    return "\n".join(lines)[:20000]


def _renew(deps, job_id, worker_id, token):
    if deps.renew_lease(job_id, worker_id, token, LEASE_SECONDS) is None:
        raise OwnershipLost()


def process_claimed_job(claim, worker_id, deps=None):
    deps = deps or GradingDependencies()
    job_id = claim["job_id"]
    token = claim["claim_token"]

    try:
        return _grade(claim, worker_id, deps)

    except OwnershipLost:
        return {"job_id": job_id, "outcome": "OWNERSHIP_LOST",
                "evaluation_written": False}

    except InfrastructureFailure as failure:
        released = deps.release(job_id, worker_id, token,
                                failure.code, failure.detail,
                                MAX_ATTEMPTS)
        return {
            "job_id": job_id,
            "outcome": "INFRASTRUCTURE_FAILURE",
            "error_code": failure.code,
            "job_status": released["job_status"] if released else None,
            "evaluation_written": False
        }

    except Exception as error:   # never let an unknown error zero a student
        released = deps.release(job_id, worker_id, token,
                                "UNEXPECTED_WORKER_ERROR",
                                type(error).__name__ + ": " + str(error),
                                MAX_ATTEMPTS)
        return {
            "job_id": job_id,
            "outcome": "INFRASTRUCTURE_FAILURE",
            "error_code": "UNEXPECTED_WORKER_ERROR",
            "job_status": released["job_status"] if released else None,
            "evaluation_written": False
        }


def _grade(claim, worker_id, deps):
    job_id = claim["job_id"]
    token = claim["claim_token"]

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

    test_alloc, rubric_alloc = split_allocation(
        config["total_marks"], config["validation_percentage"]
    )

    # ---- configuration (teacher-side) checks: never a student fault --
    rubric = []
    if rubric_alloc > 0:
        try:
            rubric = deps.load_rubric(assessment_id)
            validate_rubric_allocation(
                [{"criterion_name": c["criterion_name"],
                  "max_marks": c["max_marks"]} for c in rubric],
                rubric_alloc
            )
        except Exception as error:
            raise InfrastructureFailure("RUBRIC_NOT_CONFIGURED", str(error))

    test_cases = []
    if test_alloc > 0:
        try:
            test_cases = deps.load_test_cases(assessment_id)
        except ValueError as error:
            raise InfrastructureFailure("TEST_CASES_NOT_CONFIGURED",
                                        str(error))

    sandbox = deps.get_sandbox()
    if sandbox is None:
        raise InfrastructureFailure("SECURE_EXECUTION_UNAVAILABLE")

    # Development-only evidence sources declare themselves: synthetic
    # (modelled outputs) or trusted_fixture_recorded (replayed outputs of
    # an owner-approved developer fixture). Neither is ever a student
    # sandbox result, and the production worker refuses both.
    synthetic = bool(getattr(sandbox, "synthetic", False))
    declared = getattr(sandbox, "evidence_provenance", None)
    if declared is not None and declared not in DEVELOPMENT_PROVENANCES:
        raise InfrastructureFailure("UNKNOWN_EVIDENCE_PROVENANCE", str(declared))
    development_only = synthetic or declared is not None
    if development_only and not deps.allow_synthetic_evidence:
        raise InfrastructureFailure(
            "SYNTHETIC_EVIDENCE_REJECTED",
            "development evidence is only accepted by the development harness")
    provenance = declared or ("synthetic_development" if synthetic
                              else "sandbox_execution")
    synthetic = provenance == "synthetic_development"

    # ---- compile inside the sandbox ---------------------------------
    _renew(deps, job_id, worker_id, token)

    try:
        compiled = sandbox.compile(job["source_code"])
    except SandboxInfrastructureError as error:
        raise InfrastructureFailure("SANDBOX_COMPILE_FAILURE", str(error))

    status = compiled.get("status") if isinstance(compiled, dict) else None

    if status == "COMPILATION_FAILED":
        # Trusted compiler verdict -> zero suggested score, no LLM call.
        from grading_compilation_failure_completion import (
            CompilationJobOwnershipError)
        try:
            result = deps.complete_compilation_failure(
                job_id, worker_id, token,
                {"success": False, "status": "COMPILATION_FAILED",
                 "diagnostics": compiled.get("diagnostics") or ""}
            )
        except CompilationJobOwnershipError:
            raise OwnershipLost()
        return {**result, "outcome": "COMPILATION_FAILED"}

    if status != "COMPILED":
        raise InfrastructureFailure("SANDBOX_INVALID_RESPONSE", str(status))

    # ---- run approved tests inside the sandbox ----------------------
    try:
        if test_cases:
            _renew(deps, job_id, worker_id, token)
            try:
                raw_results = sandbox.run_tests(
                    compiled["artifact"],
                    # expected outputs never enter the sandbox
                    [{"test_number": t["test_number"],
                      "input_data": t["input_data"]} for t in test_cases]
                )
            except SandboxInfrastructureError as error:
                raise InfrastructureFailure("SANDBOX_RUN_FAILURE", str(error))
        else:
            raw_results = []
    finally:
        sandbox.cleanup(compiled.get("artifact"))

    if test_cases:
        try:
            evidence = build_student_grading_evidence(
                [{"test_number": t["test_number"],
                  "input_data": t["input_data"],
                  "expected_output": t["expected_output"]}
                 for t in test_cases],
                raw_results
            )
        except ValueError as error:
            raise InfrastructureFailure("INVALID_EXECUTION_EVIDENCE",
                                        str(error))

        if evidence["tests_run"] != REQUIRED_TEST_CASE_COUNT:
            raise InfrastructureFailure(
                "TEST_CASES_NOT_CONFIGURED",
                f"expected exactly {REQUIRED_TEST_CASE_COUNT} tests, "
                f"ran {evidence['tests_run']}")
    else:
        evidence = {"tests_run": 0, "tests_passed": 0, "cases": []}

    evidence = {**evidence, "provenance": provenance,
                "synthetic": synthetic,
                "development_only": development_only}

    # ---- static code-constraint verification (no execution) ---------
    # Never deducts marks: VIOLATED / INCONCLUSIVE only request review.
    constraint_report = check_code_constraints(
        job["source_code"], context.get("code_constraints") or [])
    evidence = {**evidence, "code_constraint_checks": constraint_report}

    # ---- AI rubric grading (only when rubric marks exist) -----------
    ai_result = None
    if rubric_alloc > 0:
        _renew(deps, job_id, worker_id, token)
        try:
            ai_result = deps.grade_rubric(
                context, rubric, rubric_alloc,
                job["source_code"], evidence
            )
        except RubricGradingUnavailable as error:
            raise InfrastructureFailure("AI_GRADING_UNAVAILABLE", str(error))
        except ValueError as error:
            raise InfrastructureFailure("AI_GRADING_CONFIG_ERROR", str(error))

    # ---- deterministic Python scoring --------------------------------
    try:
        score = combine_grading_results(
            config,
            [{"criterion_name": c["criterion_name"],
              "max_marks": c["max_marks"]} for c in rubric],
            None if ai_result is None else {
                "criteria": [{"criterion_name": c["criterion_name"],
                              "rating": c["rating"]}
                             for c in ai_result["criteria"]],
                "suggested_score": ai_result["suggested_score"]
            },
            evidence["tests_passed"],
            evidence["tests_run"]
        )
    except ValueError as error:
        raise InfrastructureFailure("SCORE_VALIDATION_FAILED", str(error))

    # ---- independent Python verification (before anything is saved) --
    try:
        report = verify_and_report(config, score, evidence, ai_result, rubric,
                                   constraint_report, test_alloc, rubric_alloc)
    except VerificationFailure as error:
        raise InfrastructureFailure("VERIFICATION_FAILED", str(error))

    feedback = (compose_feedback(score, evidence, ai_result, constraint_report)
                + report_to_text(report))[:20000]
    needs_review = bool(
        (ai_result and ai_result.get("manual_review_required"))
        or constraint_report["requires_review"]
        or report["review_status"] == "NEEDS_REVIEW")

    from grading_job_completion_service import GradingJobOwnershipError

    try:
        saved = deps.complete(
            job_id, worker_id, token,
            Decimal(score["suggested_score"]),
            feedback,
            evidence["tests_passed"],
            evidence["tests_run"],
            criteria=(ai_result or {}).get("criteria"),
            auto_evaluation_status=(
                "NEEDS_REVIEW" if needs_review else "COMPLETED"
            )
        )
    except GradingJobOwnershipError:
        raise OwnershipLost()

    return {
        **saved,
        "outcome": "GRADED",
        "score_breakdown": score,
        "verification_report": report,
        "llm_called": ai_result is not None
    }


def run_worker_once(worker_id, deps=None):
    """
    One worker iteration. Refuses to claim work when no verified
    sandbox exists, so no attempt is burned and nothing executes.
    """

    deps = deps or GradingDependencies()

    exhausted = deps.mark_exhausted()

    if deps.get_sandbox() is None:
        return {
            "outcome": "SECURE_EXECUTION_DISABLED",
            "claimed": False,
            "exhausted_jobs_marked_failed": len(exhausted or [])
        }

    claim = deps.claim(worker_id, LEASE_SECONDS, MAX_ATTEMPTS)

    if claim is None:
        return {"outcome": "NO_JOB", "claimed": False,
                "exhausted_jobs_marked_failed": len(exhausted or [])}

    result = process_claimed_job(claim, worker_id, deps)
    result["claimed"] = True
    return result
