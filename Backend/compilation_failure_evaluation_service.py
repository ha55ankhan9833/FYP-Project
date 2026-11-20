from db import get_connection
from diagnostics_sanitizer import sanitize_compiler_diagnostics


def save_compilation_failure_evaluation(
    submission_id,
    compilation_result
):
    """
    Record a genuine compilation failure for a saved submission.

    Caller must supply a compilation result produced by the
    trusted compilation workflow. This function does not
    authenticate the result's origin.

    Does not finalize instructor marks.
    """

    if type(submission_id) is not int or submission_id <= 0:
        raise ValueError("Invalid submission ID")

    if not isinstance(compilation_result, dict):
        raise ValueError("Invalid compilation result")

    if compilation_result.get("success") is not False:
        raise ValueError("Expected failed compilation")

    if compilation_result.get("status") != "COMPILATION_FAILED":
        raise ValueError("Only actual compilation failures are accepted")

    diagnostics = compilation_result.get("diagnostics")

    if not isinstance(diagnostics, str):
        raise ValueError("Invalid compiler diagnostics")

    # Sanitize BEFORE persisting: students see this text once finalized.
    diagnostics = sanitize_compiler_diagnostics(diagnostics)[:20000]

    if not diagnostics.strip():
        diagnostics = "C++ compilation failed without diagnostics."

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT submission_id
            FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)
            WHERE submission_id = ?
        """, submission_id)

        if cursor.fetchone() is None:
            raise ValueError("Submission not found")

        cursor.execute("""
            SELECT submission_id
            FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)
            WHERE submission_id = ?
        """, submission_id)

        if cursor.fetchone() is not None:
            raise ValueError("Evaluation already exists")

        cursor.execute("""
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
                ?, 0,
                'COMPILATION_FAILED',
                ?,
                NULL,
                NULL,
                0,
                0
            )
        """, submission_id, diagnostics)

        conn.commit()

        return {
            "submission_id": submission_id,
            "suggested_score": 0,
            "auto_evaluation_status": "COMPILATION_FAILED",
            "auto_evaluation_feedback": diagnostics,
            "final_score": None,
            "tests_passed": 0,
            "tests_run": 0,
            "llm_called": False
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
