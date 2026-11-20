"""
DEVELOPMENT-ONLY runner for LLM ANALYSIS MODE (llm_analysis_grading) on the
isolated FYP_E2E_Test database.

    .venv\\Scripts\\python.exe dev_tools\\run_e2e_evaluation_worker.py --dry-run --assessment-id 6 --fixture array_sum_int_accumulator
        -> full analysis with the REAL offline Qwen model and REAL read-only
           assessment data, for a reviewed fixture source; every write is
           captured in memory (nothing claimed, nothing saved)
    .venv\\Scripts\\python.exe dev_tools\\run_e2e_evaluation_worker.py --submission-id 7
        -> read-only plan for one real submission
    .venv\\Scripts\\python.exe dev_tools\\run_e2e_evaluation_worker.py --submission-id 7 --analyse --confirm-job 8
        -> claims THAT job and saves an AI_ANALYSIS_UNVERIFIED Evaluation

Never compiles or executes anything. Refuses when a production sandbox is
configured (then real execution must grade) and when the connected
database is not FYP_E2E_Test.
"""

import argparse
import json
import socket
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import llm_analysis_grading as lag  # noqa: E402
from dev_tools import trusted_execution_harness as teh  # noqa: E402
from dev_tools.grading_preview_harness import (_plain,  # noqa: E402
                                               install_read_only_guard,
                                               read_only_connection)
from grading_pipeline import MAX_ATTEMPTS, GradingDependencies  # noqa: E402

PLAN_SQL = """
SELECT s.submission_id, s.student_id, sec.assessment_id, j.job_id, j.job_status,
       j.attempt_count,
       CASE WHEN EXISTS (SELECT 1 FROM dbo.Evaluation AS e
                         WHERE e.submission_id = s.submission_id) THEN 1 ELSE 0 END
FROM dbo.Submission AS s
JOIN dbo.AssessmentSection AS sec ON sec.assessment_section_id = s.assessment_section_id
LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id
WHERE s.submission_id = ?;
"""


class Refused(RuntimeError):
    pass


def require_environment():
    import secure_execution_gate
    if secure_execution_gate.get_secure_sandbox() is not None:
        raise Refused("a production sandbox is configured; real execution must grade")
    conn = read_only_connection(db.get_connection)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DB_NAME()")
        name = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    if name != db.E2E_TEST_DATABASE:
        raise Refused(f"connected to {name}, not {db.E2E_TEST_DATABASE}")


def plan(submission_id):
    conn = read_only_connection(db.get_connection)
    try:
        cur = conn.cursor()
        cur.execute(PLAN_SQL, (submission_id,))
        row = cur.fetchone()
    finally:
        conn.rollback()
        conn.close()
    if row is None:
        raise Refused("submission not found")
    keys = ["submission_id", "student_id", "assessment_id", "job_id", "job_status",
            "attempt_count", "evaluation_exists"]
    info = dict(zip(keys, row))
    problems = []
    if info["job_id"] is None or info["job_status"] != "PENDING":
        problems.append(f"job is {info['job_status']}, not PENDING")
    if info["evaluation_exists"]:
        problems.append("an Evaluation already exists")
    info["eligible"] = not problems
    info["problems"] = problems
    return info


def dry_run_dependencies(assessment_id, source_code, captured):
    """Real read-only loaders + real Qwen; every write captured in memory."""
    import assessment_rubric_service
    install_read_only_guard()
    guarded = lambda: read_only_connection(db.get_connection)  # noqa: E731
    base = GradingDependencies()

    def capture(name, value):
        captured.append({"call": name, **value})

    return GradingDependencies(
        claim=lambda *a, **k: None,
        mark_exhausted=lambda: [],
        prepare=lambda job_id, worker_id, token: {
            "job_id": job_id, "submission_id": 0, "source_code": source_code},
        renew_lease=lambda *a: True,
        load_assessment_marks=lambda sid: {"assessment_id": assessment_id},
        load_config=base.load_config,
        load_context=base.load_context,
        load_rubric=lambda aid: assessment_rubric_service.load_assessment_rubric(
            aid, connection_factory=guarded),
        load_test_cases=base.load_test_cases,
        get_sandbox=base.get_sandbox,
        grade_rubric=base.grade_rubric,
        complete=lambda job_id, worker_id, token, score, feedback, passed, run,
        **kwargs: capture("complete", {"suggested_score": str(score),
                                       "tests_passed": passed, "tests_run": run,
                                       "feedback_chars": len(feedback), **kwargs}) or {
            "job_id": job_id, "submission_id": 0, "job_status": "COMPLETED (dry run)",
            "suggested_score": str(score), "criteria_saved": 0,
            "auto_evaluation_status": kwargs.get("auto_evaluation_status")},
        release=lambda job_id, worker_id, token, code, detail, max_attempts:
        capture("release", {"code": code, "detail": detail}) or {"job_status": "PENDING"},
    )


def default_worker_id():
    return f"{lag.WORKER_PREFIX}-e2e-{socket.gethostname()}"[:100]


def main():
    parser = argparse.ArgumentParser(description="LLM analysis mode on FYP_E2E_Test")
    parser.add_argument("--submission-id", type=int)
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--confirm-job", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assessment-id", type=int)
    parser.add_argument("--fixture", choices=sorted(teh.FIXTURES))
    args = parser.parse_args()

    db.select_database(db.E2E_TEST_DATABASE)
    try:
        require_environment()
        if args.dry_run:
            if not (args.assessment_id and args.fixture):
                raise Refused("--dry-run needs --assessment-id and --fixture")
            if teh.assessment_for(args.fixture) != args.assessment_id:
                raise Refused("fixture belongs to another assessment")
            source = teh.fixture_path(args.fixture).read_bytes().decode("utf-8")
            captured = []
            deps = dry_run_dependencies(args.assessment_id, source, captured)
            result = lag.analyse_claimed_job(
                {"job_id": 0, "claim_token": str(uuid.uuid4())}, "dry-run", deps)
            print(json.dumps(_plain({k: v for k, v in result.items()}), indent=1,
                             default=str))
            print("captured writes (NOT executed):", json.dumps(captured, default=str))
            print("DRY RUN - nothing claimed or written")
            return
        if not args.submission_id:
            raise Refused("--submission-id is required")
        info = plan(args.submission_id)
        print(json.dumps(info, indent=1, default=str))
        if not args.analyse:
            print("PLAN ONLY - nothing claimed or written")
            return
        if not info["eligible"]:
            raise Refused("; ".join(info["problems"]))
        if args.confirm_job != info["job_id"]:
            raise Refused(f"--confirm-job must be {info['job_id']}")
        from grading_job_claim_service import claim_grading_job_by_id
        worker = default_worker_id()
        claim = claim_grading_job_by_id(info["job_id"], worker,
                                        lease_seconds=lag.LEASE_SECONDS,
                                        max_attempts=MAX_ATTEMPTS)
        if claim is None or int(claim["submission_id"]) != args.submission_id:
            raise Refused("job could not be claimed for this submission")
        result = lag.analyse_claimed_job(claim, worker, GradingDependencies())
        print(json.dumps(_plain({k: result.get(k) for k in (
            "outcome", "error_code", "job_status", "suggested_score",
            "auto_evaluation_status", "criteria_saved", "tests_passed", "tests_run",
            "evaluation_mode")}), indent=1, default=str))
    except Refused as error:
        sys.exit(f"REFUSED: {error}")


if __name__ == "__main__":
    main()
