from uuid import UUID
from db import get_connection
from diagnostics_sanitizer import sanitize_compiler_diagnostics


class CompilationJobOwnershipError(Exception):
    pass


class CompilationJobCompletionError(Exception):
    pass


LOCK_JOB_SQL = """
SELECT submission_id
FROM dbo.GradingJob WITH (UPDLOCK, HOLDLOCK)
WHERE job_id = ?
  AND worker_id = ?
  AND claim_token = ?
  AND job_status = 'PROCESSING'
  AND lease_expires_at > SYSUTCDATETIME();
"""


CHECK_EVALUATION_SQL = """
SELECT submission_id
FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)
WHERE submission_id = ?;
"""


INSERT_FAILURE_SQL = """
INSERT INTO dbo.Evaluation (
    submission_id,
    suggested_score,
    auto_evaluation_status,
    auto_evaluation_feedback,
    final_score,
    result_status,
    tests_passed,
    tests_run
)
VALUES (
    ?, 0, 'COMPILATION_FAILED', ?,
    NULL, NULL, 0, 0
);
"""


COMPLETE_JOB_SQL = """
UPDATE dbo.GradingJob
SET
    job_status = 'COMPLETED',
    completed_at = SYSUTCDATETIME(),
    lease_expires_at = NULL
WHERE job_id = ?
  AND worker_id = ?
  AND claim_token = ?
  AND job_status = 'PROCESSING'
  AND lease_expires_at > SYSUTCDATETIME();
"""


def complete_compilation_failure_job(
    job_id,
    worker_id,
    claim_token,
    compilation_result
):
    """
    Atomically save a genuine compilation failure
    and complete the owned grading job.

    The caller must provide a result originating from
    a trusted, isolated compilation workflow.
    This function does not authenticate its origin.
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

    if not isinstance(compilation_result, dict):
        raise ValueError("Invalid compilation result")

    if compilation_result.get("success") is not False:
        raise ValueError("Expected unsuccessful compilation")

    if compilation_result.get("status") != "COMPILATION_FAILED":
        raise ValueError("Only genuine compilation failures accepted")

    diagnostics = compilation_result.get("diagnostics")

    if not isinstance(diagnostics, str):
        raise ValueError("Invalid compiler diagnostics")

    # Sanitize BEFORE persisting: students see this text once finalized.
    diagnostics = sanitize_compiler_diagnostics(diagnostics)[:20000].strip()

    if not diagnostics:
        diagnostics = "C++ compilation failed without diagnostics."

    conn = None

    try:
        conn = get_connection()
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute(
            LOCK_JOB_SQL,
            (job_id, worker_id, token)
        )

        job_row = cursor.fetchone()

        if job_row is None:
            raise CompilationJobOwnershipError(
                "Job ownership invalid or lease expired"
            )

        submission_id = int(job_row[0])

        cursor.execute(
            CHECK_EVALUATION_SQL,
            (submission_id,)
        )

        if cursor.fetchone() is not None:
            raise CompilationJobCompletionError(
                "Evaluation already exists"
            )

        cursor.execute(
            INSERT_FAILURE_SQL,
            (submission_id, diagnostics)
        )

        cursor.execute(
            COMPLETE_JOB_SQL,
            (job_id, worker_id, token)
        )

        if cursor.rowcount != 1:
            raise CompilationJobCompletionError(
                "Job completion ownership check failed"
            )

        conn.commit()

        return {
            "job_id": job_id,
            "submission_id": submission_id,
            "job_status": "COMPLETED",
            "auto_evaluation_status": "COMPILATION_FAILED",
            "suggested_score": 0,
            "final_score": None,
            "tests_passed": 0,
            "tests_run": 0,
            "llm_called": False
        }

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
