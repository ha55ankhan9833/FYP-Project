from db import get_connection


EXHAUSTED_JOBS_SQL = """
UPDATE dbo.GradingJob
SET
    job_status = 'FAILED',
    worker_id = NULL,
    claim_token = NULL,
    lease_expires_at = NULL,
    last_error = 'Maximum grading attempts exhausted'
OUTPUT
    inserted.job_id,
    inserted.submission_id,
    inserted.attempt_count
WHERE job_status = 'PROCESSING'
  AND attempt_count >= 3
  AND lease_expires_at < SYSUTCDATETIME();
"""


def mark_exhausted_grading_jobs():
    """
    Mark expired PROCESSING jobs as FAILED when
    maximum retry attempts have been exhausted.

    Does not execute student C++ or call the LLM.
    """

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(EXHAUSTED_JOBS_SQL)
        rows = cursor.fetchall()

        conn.commit()

        return [
            {
                "job_id": row[0],
                "submission_id": row[1],
                "attempt_count": row[2]
            }
            for row in rows
        ]

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
