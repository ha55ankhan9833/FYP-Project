"""
DEVELOPMENT-ONLY: grade ONE real Submission whose source code is byte-for-
byte an approved trusted fixture, using that fixture's RECORDED execution
evidence and the normal persistence services.

    .venv\\Scripts\\python.exe dev_tools\\fixture_submission_grader.py --submission-id 1
        -> read-only plan (nothing claimed, nothing written)
    .venv\\Scripts\\python.exe dev_tools\\fixture_submission_grader.py --submission-id 1 --grade --confirm-job 1
        -> claims THAT job, grades it, writes Evaluation (+ criteria)

What this is NOT
- It never compiles or executes anything. The submission is NOT
  independently executed: its source is matched to an approved fixture
  by SHA-256 and the evidence recorded when that fixture was run once by
  trusted_execution_harness is replayed. Every saved feedback text says so.
- It is not reachable from Flask. No route imports it, no request can
  supply evidence or pass/fail values, and the production worker still
  claims nothing because secure_execution_gate stays disabled.

What is REAL
- The Submission / GradingJob created by the student through the API.
- Ownership: the job is claimed by id with a fresh claim token and lease;
  prepare, lease renewal, completion and release re-check worker, token
  and lease exactly as for the production worker.
- Assessment configuration, rubric and the 10 hidden tests (DB).
- Evidence integrity: recorded report hash, fixture hash, tests and
  pass/fail re-derivation (recorded_evidence_grading.verify_record).
- Ollama criterion grading, Python scoring and verification, and the
  atomic Evaluation + EvaluationCriterion insert.
"""

import argparse
import hashlib
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import grading_configuration_service  # noqa: E402
import grading_test_case_loader  # noqa: E402
from dev_tools import recorded_evidence_grading as reg  # noqa: E402
from dev_tools import trusted_execution_harness as teh  # noqa: E402
from dev_tools.grading_preview_harness import (_plain,  # noqa: E402
                                               read_only_connection)
from grading_allocation import split_allocation  # noqa: E402
from grading_job_claim_service import claim_grading_job_by_id  # noqa: E402
from grading_pipeline import (MAX_ATTEMPTS, GradingDependencies,  # noqa: E402
                              process_claimed_job)
from secure_execution_gate import SandboxInfrastructureError  # noqa: E402

ASSESSMENT_ID = teh.ASSESSMENT_ID
LEASE_SECONDS = 600            # Ollama on CPU takes ~75 s per submission
MAX_FEEDBACK = 20000


class SubmissionNotEligible(RuntimeError):
    pass


def evidence_label(key, digest):
    return (f"[DEVELOPMENT TEST - graded from the RECORDED execution evidence "
            f"of approved trusted fixture '{key}' (sha256 {digest[:12]}...). "
            f"This submission was NOT independently compiled or executed.]")


# ----------------------------------------------------------------------
# Read-only eligibility check
# ----------------------------------------------------------------------

SUBMISSION_SQL = """
SELECT s.submission_id, s.student_id, s.assessment_section_id, s.source_code,
       sec.assessment_id, sec.section_id, a.course_code, a.teacher_id,
       j.job_id, j.job_status, j.attempt_count,
       CASE WHEN EXISTS (
           SELECT 1 FROM dbo.Enrollment_Export AS e
           WHERE e.student_id COLLATE DATABASE_DEFAULT
                 = s.student_id COLLATE DATABASE_DEFAULT
             AND e.section_id COLLATE DATABASE_DEFAULT
                 = sec.section_id COLLATE DATABASE_DEFAULT
             AND e.course_no COLLATE DATABASE_DEFAULT
                 = a.course_code COLLATE DATABASE_DEFAULT) THEN 1 ELSE 0 END,
       CASE WHEN EXISTS (
           SELECT 1 FROM dbo.SectionCourseTeacher AS sct
           WHERE sct.section_id COLLATE DATABASE_DEFAULT
                 = sec.section_id COLLATE DATABASE_DEFAULT
             AND sct.course_no COLLATE DATABASE_DEFAULT
                 = a.course_code COLLATE DATABASE_DEFAULT
             AND sct.teacher_id COLLATE DATABASE_DEFAULT
                 = a.teacher_id COLLATE DATABASE_DEFAULT) THEN 1 ELSE 0 END,
       CASE WHEN EXISTS (SELECT 1 FROM dbo.Evaluation AS ev
                         WHERE ev.submission_id = s.submission_id)
            THEN 1 ELSE 0 END
FROM dbo.Submission AS s
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a ON a.assessment_id = sec.assessment_id
LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id
WHERE s.submission_id = ?;
"""


def source_sha256(source_code):
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def match_fixture(source_code):
    """Fixture key whose APPROVED hash equals this exact source, else None."""
    digest = source_sha256(source_code)
    for key, record in reg.REVIEWED_RECORDS.items():
        if digest == record["fixture_sha256"]:
            return key, digest
    return None, digest


def load_submission(submission_id, connection_factory):
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(SUBMISSION_SQL, (submission_id,))
        row = cursor.fetchone()
    finally:
        conn.rollback()
        conn.close()
    if row is None:
        raise SubmissionNotEligible("Submission not found")
    keys = ["submission_id", "student_id", "assessment_section_id",
            "source_code", "assessment_id", "section_id", "course_code",
            "teacher_id", "job_id", "job_status", "attempt_count",
            "enrolled", "teacher_linked", "evaluation_exists"]
    return dict(zip(keys, row))


def check_eligibility(sub):
    problems = []
    key, digest = match_fixture(sub["source_code"] or "")
    expected_assessment = teh.assessment_for(key) if key else None
    if key is not None and int(sub["assessment_id"]) != expected_assessment:
        problems.append(f"submission is for assessment {sub['assessment_id']}, "
                        f"not {expected_assessment}")
    if sub["job_id"] is None:
        problems.append("no GradingJob exists for this submission")
    elif sub["job_status"] != "PENDING":
        problems.append(f"job is {sub['job_status']}, not PENDING")
    elif int(sub["attempt_count"]) >= MAX_ATTEMPTS:
        problems.append("job has no attempts left")
    if sub["evaluation_exists"]:
        problems.append("an Evaluation already exists")
    if not sub["enrolled"]:
        problems.append("student is not enrolled in the assigned section/course")
    if not sub["teacher_linked"]:
        problems.append("assessment teacher does not teach this section/course")
    if key is None:
        problems.append(f"source sha256 {digest} is not an approved fixture "
                        f"- this tool never grades arbitrary student code")
    elif teh.fixture_sha256(key) != digest:
        problems.append(f"fixture file {key} changed on disk")
    if problems:
        raise SubmissionNotEligible("; ".join(problems))
    return key, digest


def build_plan(submission_id, connection_factory=None, test_loader=None,
               config_loader=None):
    """Read-only. Verifies submission, fixture match and recorded evidence."""
    connection_factory = connection_factory or (
        lambda: read_only_connection(db.get_connection))
    test_loader = test_loader or grading_test_case_loader.load_grading_test_cases
    config_loader = config_loader or \
        grading_configuration_service.load_grading_configuration

    sub = load_submission(submission_id, connection_factory)
    key, digest = check_eligibility(sub)

    assessment_id = teh.assessment_for(key)
    tests = test_loader(assessment_id)
    config = config_loader(assessment_id)
    test_alloc, rubric_alloc = split_allocation(config["total_marks"],
                                                config["validation_percentage"])
    report, report_sha = reg.read_record(key)
    verification = reg.verify_record(key, report, report_sha, tests, digest,
                                     test_alloc)
    return {
        "submission_id": int(sub["submission_id"]),
        "student_id": sub["student_id"],
        "section_id": sub["section_id"],
        "assessment_id": int(sub["assessment_id"]),
        "job_id": int(sub["job_id"]),
        "job_status": sub["job_status"],
        "attempt_count": int(sub["attempt_count"]),
        "fixture": key,
        "source_sha256": digest,
        "evidence": verification,
        "expected_outcome": ("COMPILATION_FAILED"
                             if verification["compile_classification"] ==
                             "STUDENT_CODE_COMPILATION_ERROR" else "GRADED"),
        "ollama_will_be_called": verification["compile_classification"] == "COMPILED",
        "test_marks_allocated": str(test_alloc),
        "rubric_marks_allocated": str(rubric_alloc),
        "_report": report,
    }


# ----------------------------------------------------------------------
# Grading (writes through the normal services)
# ----------------------------------------------------------------------

class FixtureBoundSandbox(reg.RecordedEvidenceSandbox):
    """Replays evidence only for the exact approved source it was built for."""

    def __init__(self, report, fixture_sha256):
        super().__init__(report)
        self._fixture_sha256 = fixture_sha256

    def compile(self, source_code):
        if source_sha256(source_code) != self._fixture_sha256:
            raise SandboxInfrastructureError(
                "Submitted source does not match the approved fixture")
        return super().compile(source_code)


def build_dependencies(plan, overrides=None):
    label = evidence_label(plan["fixture"], plan["source_sha256"])
    base = GradingDependencies()
    complete, complete_failure = base.complete, base.complete_compilation_failure

    def labelled_complete(job_id, worker_id, token, score, feedback,
                          passed, run, **kwargs):
        text = (label + "\n" + feedback)[:MAX_FEEDBACK]
        return complete(job_id, worker_id, token, score, text, passed, run,
                        **kwargs)

    def labelled_failure(job_id, worker_id, token, result):
        return complete_failure(job_id, worker_id, token, {
            **result, "diagnostics": label + "\n" + result["diagnostics"]})

    sandbox = FixtureBoundSandbox(plan["_report"], plan["source_sha256"])
    values = {"get_sandbox": lambda: sandbox,
              "complete": labelled_complete,
              "complete_compilation_failure": labelled_failure,
              "allow_synthetic_evidence": True}     # development path ONLY
    values.update(overrides or {})
    return GradingDependencies(**values), sandbox


def grade(plan, worker_id, claim_fn=claim_grading_job_by_id, overrides=None):
    claim = claim_fn(plan["job_id"], worker_id, lease_seconds=LEASE_SECONDS,
                     max_attempts=MAX_ATTEMPTS)
    if claim is None:
        raise SubmissionNotEligible("Job could not be claimed (no longer PENDING)")
    if int(claim["submission_id"]) != plan["submission_id"]:
        raise SubmissionNotEligible("Claimed job belongs to another submission")
    deps, sandbox = build_dependencies(plan, overrides)
    result = process_claimed_job(claim, worker_id, deps)
    return {**_plain(result),
            "replay": {"compile_calls": sandbox.compile_calls,
                       "run_calls": sandbox.run_calls,
                       "real_compilation": False, "real_execution": False}}


def require_test_database(connection_factory=None):
    """--grade writes only to the isolated E2E database, never FYP_Database."""
    connection_factory = connection_factory or (
        lambda: read_only_connection(db.get_connection))
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DB_NAME()")
        name = cursor.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    if name != db.E2E_TEST_DATABASE:
        raise SubmissionNotEligible(
            f"Grading writes are only allowed in {db.E2E_TEST_DATABASE} "
            f"(connected to {name})")
    return name


def default_worker_id():
    return f"dev-fixture-grader-{socket.gethostname()}"[:100]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--submission-id", type=int, required=True)
    parser.add_argument("--grade", action="store_true",
                        help="claim and grade (writes Evaluation rows)")
    parser.add_argument("--confirm-job", type=int,
                        help="must equal the planned job id when --grade")
    args = parser.parse_args()

    # This tool only ever works on the isolated E2E database.
    db.select_database(db.E2E_TEST_DATABASE)

    import secure_execution_gate
    if secure_execution_gate.get_secure_sandbox() is not None:
        sys.exit("REFUSED: a production sandbox is configured; let the "
                 "production worker grade real submissions")

    plan = build_plan(args.submission_id)
    print(json.dumps({k: v for k, v in plan.items() if k != "_report"},
                     indent=2, default=str))
    if not args.grade:
        print("PLAN ONLY - nothing claimed or written")
        return
    if args.confirm_job != plan["job_id"]:
        sys.exit(f"REFUSED: --confirm-job must be {plan['job_id']}")
    try:
        require_test_database()
    except SubmissionNotEligible as error:
        sys.exit(f"REFUSED: {error}")

    result = grade(plan, default_worker_id())
    print(json.dumps({k: result.get(k) for k in (
        "outcome", "error_code", "job_status", "suggested_score",
        "auto_evaluation_status", "criteria_saved", "tests_passed",
        "tests_run", "replay")}, indent=2, default=str))
    report = result.get("verification_report") or {}
    for warning in report.get("warnings", []):
        print("NEEDS_REVIEW warning:", warning["message"])


if __name__ == "__main__":
    main()
