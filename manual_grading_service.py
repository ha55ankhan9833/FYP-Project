"""
Teacher-only MANUAL GRADING FALLBACK for submissions that automatic
grading has not processed (job PENDING, e.g. while secure student
execution is disabled) or could not process (job FAILED).

What it records (one transaction, existing locking conventions):
  Evaluation: auto_evaluation_status = 'MANUALLY_GRADED', result_status =
    'FINALIZED', final_score = teacher's mark, suggested_score = NULL,
    tests_passed = tests_run = 0 (no tests were run), instructor_feedback =
    optional student-visible text, auto_evaluation_feedback = a teacher-only
    audit record (who, when, why, previous job state).
  GradingJob: PENDING/FAILED -> COMPLETED, worker_id = 'manual-grade:<teacher>',
    claim token and lease cleared, attempt_count unchanged. The automatic
    worker therefore never claims it afterwards.

What it never does: overwrite an Evaluation (automatic, AI or manual),
take over a job a worker holds (PROCESSING), fabricate compilation
results, test passes, verified test marks, AI rubric marks or execution
evidence. No schema change is required.
"""

import json
import re
from decimal import Decimal

import pyodbc

from db import get_connection

MANUAL_STATUS = "MANUALLY_GRADED"
MANUAL_RECORD_MARKER = "=== Manual grading record (teacher only) ==="
MIN_JUSTIFICATION, MAX_JUSTIFICATION = 10, 2000
ELIGIBLE_JOB_STATUSES = ("PENDING", "FAILED")

LOCK_SUBMISSION_SQL = """
SELECT a.total_marks, a.assessment_id, j.job_id, j.job_status, j.attempt_count,
       CASE WHEN j.lease_expires_at > SYSUTCDATETIME() THEN 1 ELSE 0 END,
       SYSUTCDATETIME()
FROM dbo.Submission AS s WITH (UPDLOCK, HOLDLOCK)
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a ON a.assessment_id = sec.assessment_id
LEFT JOIN dbo.GradingJob AS j WITH (UPDLOCK, HOLDLOCK)
    ON j.submission_id = s.submission_id
WHERE s.submission_id = ? AND a.teacher_id = ?;
"""

LOCK_EVALUATION_SQL = """
SELECT result_status, auto_evaluation_status
FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)
WHERE submission_id = ?;
"""

CLOSE_JOB_SQL = """
UPDATE dbo.GradingJob
SET job_status = 'COMPLETED', completed_at = SYSUTCDATETIME(),
    worker_id = ?, claim_token = NULL, lease_expires_at = NULL
WHERE job_id = ? AND submission_id = ?
  AND job_status IN ('PENDING', 'FAILED');
"""

INSERT_MANUAL_EVALUATION_SQL = """
INSERT INTO dbo.Evaluation (
    submission_id, suggested_score, auto_evaluation_status,
    auto_evaluation_feedback, final_score, result_status,
    instructor_feedback, tests_passed, tests_run
)
VALUES (?, NULL, 'MANUALLY_GRADED', ?, ?, 'FINALIZED', ?, 0, 0);
"""


def _clean_text(value, field, minimum, maximum, required):
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if required and len(value) < minimum:
        raise ValueError(f"{field} must be at least {minimum} characters")
    return value[:maximum] or None


def build_manual_record(teacher_id, recorded_at, final_score, total_marks,
                        justification, job_id, job_status, attempt_count):
    record = {
        "type": MANUAL_STATUS,
        "teacher_id": teacher_id,
        "recorded_at_utc": recorded_at.isoformat() if hasattr(recorded_at, "isoformat")
        else str(recorded_at),
        "final_score": str(final_score),
        "total_marks": str(Decimal(str(total_marks))),
        "justification": justification,
        "previous_job": {"job_id": job_id, "job_status": job_status,
                         "attempt_count": attempt_count},
        "automatic_grading_performed": False,
    }
    return (MANUAL_RECORD_MARKER + "\n" + json.dumps(record, indent=1, sort_keys=True)
            + "\nAutomatic grading was NOT performed: there is no compilation result, "
              "test execution, verified test marks or AI rubric assessment for this "
              "submission.")


def parse_manual_record(feedback):
    """The teacher-only audit record, or None for other evaluations."""
    if not feedback or MANUAL_RECORD_MARKER not in feedback:
        return None
    body = feedback.split(MANUAL_RECORD_MARKER, 1)[1]
    match = re.search(r"\{.*\}", body, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else None
    except ValueError:
        return None


def manually_grade_submission(submission_id, teacher_id, final_score,
                              justification, instructor_feedback=None,
                              connection_factory=get_connection):
    # Reuse the existing review rules (score bounds, hidden-data guard).
    import evaluation_review_service as review

    if type(submission_id) is not int or submission_id <= 0:
        raise ValueError("Invalid submission ID")
    justification = _clean_text(justification, "justification", MIN_JUSTIFICATION,
                                MAX_JUSTIFICATION, required=True)
    instructor_feedback = _clean_text(instructor_feedback, "instructor_feedback",
                                      0, review.MAX_FEEDBACK, required=False)

    conn = connection_factory()
    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute(LOCK_SUBMISSION_SQL, (submission_id, teacher_id))
        row = cursor.fetchone()
        if row is None:
            raise review.NotFoundError("Submission not found")
        total_marks, assessment_id, job_id, job_status, attempts, lease_active, now = row
        score = review.validate_final_score(final_score, total_marks)

        cursor.execute(LOCK_EVALUATION_SQL, (submission_id,))
        existing = cursor.fetchone()
        if existing is not None:
            raise review.ConflictError(
                "An evaluation already exists for this submission; review it "
                "through the normal finalization instead"
                if existing[0] != review.FINALIZED
                else "Evaluation is already finalized")

        if job_id is None:
            raise review.ConflictError("Submission has no grading job")
        if job_status == "PROCESSING":
            raise review.ConflictError(
                "Automatic grading is in progress for this submission"
                if lease_active else
                "An automatic grading claim has expired; try again shortly")
        if job_status not in ELIGIBLE_JOB_STATUSES:
            raise review.ConflictError(
                f"Grading job is {job_status}; manual grading is not available")

        if instructor_feedback:
            cursor.execute(review.HIDDEN_INPUTS_SQL, (assessment_id,))
            if review.find_hidden_values(instructor_feedback, cursor.fetchall()):
                raise ValueError("instructor_feedback must not contain hidden "
                                 "test inputs or expected outputs")

        cursor.execute(CLOSE_JOB_SQL, (f"manual-grade:{teacher_id}"[:100],
                                       int(job_id), submission_id))
        if cursor.rowcount != 1:
            raise review.ConflictError("Grading job changed concurrently")

        record = build_manual_record(teacher_id, now, score, total_marks,
                                     justification, int(job_id), job_status,
                                     int(attempts or 0))
        try:
            cursor.execute(INSERT_MANUAL_EVALUATION_SQL, (
                submission_id, record, score, instructor_feedback))
        except pyodbc.IntegrityError:
            raise review.ConflictError("Evaluation was created concurrently")

        conn.commit()
        return {"submission_id": submission_id, "final_score": str(score),
                "result_status": review.FINALIZED, "mode": MANUAL_STATUS,
                "previous_job_status": job_status}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
