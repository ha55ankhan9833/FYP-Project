from uuid import UUID
from decimal import Decimal, InvalidOperation
from db import get_connection


class GradingJobOwnershipError(Exception):
    pass


class GradingJobCompletionError(Exception):
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


ASSESSMENT_LIMIT_SQL = """
SELECT a.total_marks
FROM dbo.Submission AS s
JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
JOIN dbo.Assessment AS a
    ON a.assessment_id = sec.assessment_id
WHERE s.submission_id = ?;
"""


CHECK_EVALUATION_SQL = """
SELECT submission_id
FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)
WHERE submission_id = ?;
"""


INSERT_EVALUATION_SQL = """
INSERT INTO dbo.Evaluation (
    submission_id,
    suggested_score,
    auto_evaluation_status,
    auto_evaluation_feedback,
    tests_passed,
    tests_run
)
VALUES (?, ?, ?, ?, ?, ?);
"""


INSERT_CRITERION_SQL = """
INSERT INTO dbo.EvaluationCriterion (
    submission_id,
    criterion_name,
    rating,
    explanation
)
VALUES (?, ?, ?, ?);
"""


ALLOWED_AUTO_STATUSES = ("COMPLETED", "NEEDS_REVIEW")
MAX_CRITERION_RATING = Decimal("999.99")   # EvaluationCriterion DECIMAL(5,2)


def _validate_criteria(criteria):
    """Validate rubric awards that were already checked by Python grading."""

    if criteria is None:
        return []

    if not isinstance(criteria, list):
        raise ValueError("Criteria must be a list")

    names = set()
    validated = []

    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("Invalid criterion")

        name = item.get("criterion_name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid criterion name")

        name = name.strip()

        if len(name) > 150:
            raise ValueError("Criterion name too long")

        if name.casefold() in names:
            raise ValueError("Duplicate criterion name")

        names.add(name.casefold())

        try:
            rating = Decimal(str(item.get("rating")))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("Invalid criterion rating")

        if (
            isinstance(item.get("rating"), bool)
            or not rating.is_finite()
            or not Decimal("0") <= rating <= MAX_CRITERION_RATING
            or rating != rating.quantize(Decimal("0.01"))
        ):
            raise ValueError("Invalid criterion rating")

        explanation = item.get("explanation")

        if explanation is not None and not isinstance(explanation, str):
            raise ValueError("Invalid criterion explanation")

        validated.append(
            (name, rating, (explanation or "")[:4000] or None)
        )

    return validated


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


def complete_grading_job(
    job_id,
    worker_id,
    claim_token,
    suggested_score,
    feedback,
    tests_passed,
    tests_run,
    criteria=None,
    auto_evaluation_status="COMPLETED"
):
    """
    Atomically insert an Evaluation (plus optional per-criterion
    rubric rows) and complete a grading job owned by an active worker.

    Caller must supply independently verified grading
    evidence and a validated suggested score.

    final_score / result_status are never set here: the teacher
    finalizes through the evaluation review API.
    """

    if auto_evaluation_status not in ALLOWED_AUTO_STATUSES:
        raise ValueError("Invalid automatic evaluation status")

    validated_criteria = _validate_criteria(criteria)

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

    try:
        score = Decimal(str(suggested_score))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid suggested score")

    if not score.is_finite() or score < Decimal("0"):
        raise ValueError("Suggested score must be finite and nonnegative")

    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError("Feedback is required")

    if len(feedback) > 20000:
        raise ValueError("Feedback is too long")

    if type(tests_passed) is not int or type(tests_run) is not int:
        raise ValueError("Test counts must be integers")

    if not 0 <= tests_passed <= tests_run <= 10000:
        raise ValueError("Invalid test counts")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            LOCK_JOB_SQL,
            (job_id, worker_id, token)
        )

        row = cursor.fetchone()

        if row is None:
            raise GradingJobOwnershipError(
                "Job ownership invalid or lease expired"
            )

        submission_id = row[0]

        cursor.execute(
            ASSESSMENT_LIMIT_SQL,
            (submission_id,)
        )

        marks_row = cursor.fetchone()

        if marks_row is None or marks_row[0] is None:
            raise GradingJobCompletionError(
                "Assessment maximum marks unavailable"
            )

        try:
            total_marks = Decimal(str(marks_row[0]))
        except (InvalidOperation, ValueError, TypeError):
            raise GradingJobCompletionError(
                "Invalid assessment maximum marks"
            )

        if not total_marks.is_finite() or total_marks <= 0:
            raise GradingJobCompletionError(
                "Invalid assessment maximum marks"
            )

        if score > total_marks:
            raise GradingJobCompletionError(
                "Suggested score exceeds assessment total marks"
            )

        cursor.execute(CHECK_EVALUATION_SQL, (submission_id,))

        if cursor.fetchone() is not None:
            raise GradingJobCompletionError("Evaluation already exists")

        cursor.execute(
            INSERT_EVALUATION_SQL,
            (
                submission_id,
                score,
                auto_evaluation_status,
                feedback.strip(),
                tests_passed,
                tests_run
            )
        )

        for name, rating, explanation in validated_criteria:
            cursor.execute(
                INSERT_CRITERION_SQL,
                (submission_id, name, rating, explanation)
            )

        cursor.execute(
            COMPLETE_JOB_SQL,
            (job_id, worker_id, token)
        )

        if cursor.rowcount != 1:
            raise GradingJobCompletionError(
                "Job completion ownership check failed"
            )

        conn.commit()

        return {
            "job_id": job_id,
            "submission_id": submission_id,
            "job_status": "COMPLETED",
            "suggested_score": str(score),
            "auto_evaluation_status": auto_evaluation_status,
            "criteria_saved": len(validated_criteria),
            "tests_passed": tests_passed,
            "tests_run": tests_run
        }

    except Exception:
        if conn is not None:
            conn.rollback()
        raise

    finally:
        if conn is not None:
            conn.close()
