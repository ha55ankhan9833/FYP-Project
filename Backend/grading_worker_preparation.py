from uuid import UUID
from db import get_connection


LOAD_CLAIMED_SUBMISSION_SQL = """
SELECT
    j.job_id,
    j.submission_id,
    s.source_code,
    s.file_name,
    s.student_id,
    s.assessment_section_id
FROM dbo.GradingJob AS j
INNER JOIN dbo.Submission AS s
    ON s.submission_id = j.submission_id
WHERE j.job_id = ?
  AND j.worker_id = ?
  AND j.claim_token = ?
  AND j.job_status = 'PROCESSING'
  AND j.lease_expires_at > SYSUTCDATETIME();
"""


def prepare_claimed_grading_job(job_id, worker_id, claim_token):
    """
    Read a submission only when the supplied worker
    currently holds an active grading-job claim.

    Does not compile, execute, grade or save results.
    This ownership check is advisory; final writes
    must recheck ownership inside their transaction.
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
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Invalid claim token")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            LOAD_CLAIMED_SUBMISSION_SQL,
            (job_id, worker_id, token)
        )

        row = cursor.fetchone()

        if row is None:
            return None

        if row[2] is None or not isinstance(row[2], str):
            raise ValueError("Submission source code unavailable")

        return {
            "job_id": row[0],
            "submission_id": row[1],
            "source_code": row[2],
            "file_name": row[3],
            "student_id": row[4],
            "assessment_section_id": row[5]
        }

    finally:
        if conn is not None:
            conn.rollback()
            conn.close()
