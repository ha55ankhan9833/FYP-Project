"""
Release a claimed grading job after an INFRASTRUCTURE failure.

Infrastructure failures (sandbox unavailable, compiler unavailable,
grading timeout, Ollama unavailable, missing rubric/test configuration,
worker crash) are NOT the student's fault:

- No Evaluation row is written, so no automatic zero is recorded.
- The job returns to PENDING for another attempt, or becomes FAILED
  when attempts are exhausted, so the teacher grades it manually.

Only the worker holding the current claim token can release the job.
"""

from uuid import UUID

from db import get_connection
from diagnostics_sanitizer import sanitize_compiler_diagnostics


MAX_ERROR_LENGTH = 2000   # GradingJob.last_error NVARCHAR(2000)


RELEASE_JOB_SQL = """
UPDATE dbo.GradingJob
SET
    job_status = CASE
        WHEN attempt_count >= ? THEN 'FAILED'
        ELSE 'PENDING'
    END,
    worker_id = NULL,
    claim_token = NULL,
    lease_expires_at = NULL,
    last_error = ?
OUTPUT
    inserted.job_id,
    inserted.submission_id,
    inserted.job_status,
    inserted.attempt_count
WHERE job_id = ?
  AND worker_id = ?
  AND claim_token = ?
  AND job_status = 'PROCESSING';
"""


def release_job_after_infrastructure_failure(
    job_id,
    worker_id,
    claim_token,
    error_code,
    error_detail="",
    max_attempts=3,
    connection_factory=get_connection
):
    """
    Returns the new job state, or None if this worker no longer
    owns the claim (another worker took over, or it already finished).
    """

    if type(job_id) is not int or job_id <= 0:
        raise ValueError("Invalid job ID")

    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("Invalid worker ID")

    worker_id = worker_id.strip()

    if len(worker_id) > 100:
        raise ValueError("Worker ID too long")

    try:
        token = str(UUID(str(claim_token)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid claim token")

    if not isinstance(error_code, str) or not error_code.strip():
        raise ValueError("Error code required")

    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError("Maximum attempts must be between 1 and 3")

    message = error_code.strip()

    if error_detail:
        # last_error is returned by teacher APIs: no host paths in it.
        message = f"{message}: " + sanitize_compiler_diagnostics(
            str(error_detail).strip())

    message = message[:MAX_ERROR_LENGTH]

    conn = None

    try:
        conn = connection_factory()
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute(
            RELEASE_JOB_SQL,
            (max_attempts, message, job_id, worker_id, token)
        )

        row = cursor.fetchone()
        conn.commit()

        if row is None:
            return None

        return {
            "job_id": int(row[0]),
            "submission_id": int(row[1]),
            "job_status": row[2],
            "attempt_count": int(row[3]),
            "last_error": message,
            "evaluation_written": False
        }

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
