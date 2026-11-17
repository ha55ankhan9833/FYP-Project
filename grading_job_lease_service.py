from uuid import UUID
from db import get_connection


RENEW_LEASE_SQL = """
UPDATE dbo.GradingJob
SET lease_expires_at =
    DATEADD(SECOND, ?, SYSUTCDATETIME())
OUTPUT inserted.lease_expires_at
WHERE job_id = ?
  AND worker_id = ?
  AND claim_token = ?
  AND job_status = 'PROCESSING'
  AND lease_expires_at > SYSUTCDATETIME();
"""


def renew_grading_job_lease(
    job_id,
    worker_id,
    claim_token,
    lease_seconds=300
):
    """
    Renew an active grading job lease.

    Returns the new expiry timestamp, or None if the
    worker no longer owns an active claim.
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

    if type(lease_seconds) is not int or not 30 <= lease_seconds <= 3600:
        raise ValueError("Lease must be between 30 and 3600 seconds")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            RENEW_LEASE_SQL,
            (lease_seconds, job_id, worker_id, str(token))
        )

        row = cursor.fetchone()
        conn.commit()

        if row is None:
            return None

        return row[0].isoformat() if row[0] is not None else None

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
