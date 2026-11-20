from db import get_connection


CLAIM_SQL = """
;WITH NextJob AS (
    SELECT TOP (1)
        job_id,
        submission_id,
        job_status,
        attempt_count,
        worker_id,
        claimed_at,
        lease_expires_at,
        claim_token
    FROM dbo.GradingJob WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE attempt_count < ?
      AND (
          job_status = 'PENDING'
          OR (
              job_status = 'PROCESSING'
              AND lease_expires_at < SYSUTCDATETIME()
          )
      )
    ORDER BY created_at ASC, job_id ASC
)
UPDATE NextJob
SET
    job_status = 'PROCESSING',
    attempt_count = attempt_count + 1,
    worker_id = ?,
    claimed_at = SYSUTCDATETIME(),
    lease_expires_at = DATEADD(SECOND, ?, SYSUTCDATETIME()),
    claim_token = NEWID()
OUTPUT
    inserted.job_id,
    inserted.submission_id,
    inserted.attempt_count,
    inserted.claim_token,
    inserted.lease_expires_at;
"""


# Same claim semantics as CLAIM_SQL, restricted to ONE job. Used by the
# development fixture grader so it can never pick up an unrelated job.
CLAIM_BY_ID_SQL = """
UPDATE dbo.GradingJob
SET
    job_status = 'PROCESSING',
    attempt_count = attempt_count + 1,
    worker_id = ?,
    claimed_at = SYSUTCDATETIME(),
    lease_expires_at = DATEADD(SECOND, ?, SYSUTCDATETIME()),
    claim_token = NEWID()
OUTPUT
    inserted.job_id,
    inserted.submission_id,
    inserted.attempt_count,
    inserted.claim_token,
    inserted.lease_expires_at
WHERE job_id = ?
  AND attempt_count < ?
  AND (
      job_status = 'PENDING'
      OR (
          job_status = 'PROCESSING'
          AND lease_expires_at < SYSUTCDATETIME()
      )
  );
"""


def _validate_claim_args(worker_id, lease_seconds, max_attempts):
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("Valid worker ID required")

    if len(worker_id) > 100:
        raise ValueError("Worker ID too long")

    if type(lease_seconds) is not int or not 30 <= lease_seconds <= 3600:
        raise ValueError("Lease must be between 30 and 3600 seconds")

    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError("Maximum attempts must be between 1 and 3")

    return worker_id.strip()


def claim_grading_job_by_id(job_id, worker_id, lease_seconds=300,
                            max_attempts=3):
    """
    Atomically claim ONE specific job if it is eligible (same rules as
    claim_next_grading_job). Returns None when it is not claimable.
    """

    if type(job_id) is not int or job_id <= 0:
        raise ValueError("Invalid job ID")

    worker_id = _validate_claim_args(worker_id, lease_seconds, max_attempts)
    return _run_claim(CLAIM_BY_ID_SQL,
                      (worker_id, lease_seconds, job_id, max_attempts))


def claim_next_grading_job(worker_id, lease_seconds=300, max_attempts=3):
    """
    Atomically claim one eligible grading job.

    Does not execute student C++ or call the LLM.
    """

    worker_id = _validate_claim_args(worker_id, lease_seconds, max_attempts)
    return _run_claim(CLAIM_SQL, (max_attempts, worker_id, lease_seconds))


def _run_claim(sql, params):
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(sql, params)

        row = cursor.fetchone()

        conn.commit()

        if row is None:
            return None

        return {
            "job_id": row[0],
            "submission_id": row[1],
            "attempt_count": row[2],
            "claim_token": str(row[3]),
            "lease_expires_at": (
                row[4].isoformat()
                if row[4] is not None
                else None
            )
        }

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
