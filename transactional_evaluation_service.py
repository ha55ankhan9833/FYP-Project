"""
Transactional evaluation service.

Not operational until:
1. GradingJob schema is approved and created.
2. Atomic claiming is implemented and tested.
3. Evaluation payload validation is implemented.
4. Transactional INSERT and completion are implemented.
"""


class GradingTransactionNotReady(RuntimeError):
    pass


def save_owned_grading_evaluation(
    job_id,
    worker_id,
    claim_token,
    evaluation_data
):
    """
    Future atomic evaluation save.

    Required behavior:
    - Verify active job ownership under SQL locks.
    - Reject stale or expired claims.
    - Validate evaluation_data.
    - Insert Evaluation.
    - Mark GradingJob COMPLETED.
    - Commit both changes atomically.
    - Roll back on any failure.

    This function intentionally performs no database operations.
    """

    raise GradingTransactionNotReady(
        "Transactional evaluation saving is not implemented"
    )
