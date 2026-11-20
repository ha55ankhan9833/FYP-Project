from uuid import UUID
from db import get_connection


OWNERSHIP_SQL = """
SELECT
    job_id,
    submission_id,
    lease_expires_at
FROM dbo.GradingJob
WHERE job_id = ?
  AND worker_id = ?
  AND claim_token = ?
  AND job_status = 'PROCESSING'
  AND lease_expires_at > SYSUTCDATETIME();
"""


def verify_grading_job_ownership(job_id, worker_id, claim_token):
    """
    Check whether a worker currently owns an active job.

    This is a read-only advisory check.
    Final evaluation saving must recheck ownership
    inside the same transaction as the write.
    """

    if type(job_id) is not int or job_id <= 0:
        raise ValueError("Invalid job ID")

    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("Invalid worker ID")

    worker_id = worker_id.strip()

    if len(worker_id) > 100:
        raise ValueError("Worker ID too long")

    try:
        token = UUID(str(claim_token))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid claim token")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            OWNERSHIP_SQL,
            (job_id, worker_id, str(token))
        )

        row = cursor.fetchone()

        if row is None:
            return {
                "owned": False,
                "job_id": job_id,
                "submission_id": None,
                "lease_expires_at": None
            }

        return {
            "owned": True,
            "job_id": row[0],
            "submission_id": row[1],
            "lease_expires_at": (
                row[2].isoformat()
                if row[2] is not None else None
            )
        }

    finally:
        if conn is not None:
            conn.close()
