import logging
import os
import socket
import sys

from db import get_connection

WORKER_PREVIEW_SQL = """
SELECT TOP (1)
    j.job_id,
    j.submission_id,
    j.job_status,
    j.attempt_count,
    s.file_name,
    s.student_id,
    s.assessment_section_id
FROM dbo.GradingJob AS j
INNER JOIN dbo.Submission AS s
    ON s.submission_id = j.submission_id
WHERE j.job_status = 'PENDING'
ORDER BY j.created_at ASC, j.job_id ASC;
"""

def preview_next_grading_job():
    """Read-only worker preview; never claims or executes jobs."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(WORKER_PREVIEW_SQL)
        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "job_id": row[0],
            "submission_id": row[1],
            "job_status": row[2],
            "attempt_count": row[3],
            "file_name": row[4],
            "student_id": row[5],
            "assessment_section_id": row[6],
            "next_step": "AWAITING_SECURE_EXECUTION"
        }
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def default_worker_id():
    return f"{socket.gethostname()}-{os.getpid()}"[:100]


def run_worker_once(worker_id=None):
    """
    Process at most one grading job.

    Does nothing (and claims nothing) while no verified secure
    sandbox exists - see secure_execution_gate.py.
    """
    from grading_pipeline import run_worker_once as pipeline_run_once

    return pipeline_run_once(worker_id or default_worker_id())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "--preview":
        print(preview_next_grading_job())
    elif len(sys.argv) > 1 and sys.argv[1] == "--llm-analysis":
        # Opt-in: analyse one job WITHOUT execution (llm_analysis_grading).
        from llm_analysis_grading import WORKER_PREFIX, run_analysis_worker_once
        print(run_analysis_worker_once(f"{WORKER_PREFIX}-{default_worker_id()}"[:100]))
    else:
        print(run_worker_once())
