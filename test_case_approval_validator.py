import json


def validate_assessment_approval(cursor, assessment_id):
    """
    Verify that an assessment's constraints match its
    finalized, teacher-approved constraint review draft.

    Caller must provide a cursor inside an active transaction.
    """

    cursor.execute("""
        SELECT
            draft_json,
            is_finalized
        FROM dbo.ConstraintReviewDraft WITH (UPDLOCK, HOLDLOCK)
        WHERE assessment_id = ?
    """, assessment_id)

    rows = cursor.fetchall()

    if len(rows) != 1:
        raise ValueError(
            "Assessment must have exactly one linked approval draft"
        )

    draft_json, is_finalized = rows[0]

    if not is_finalized:
        raise ValueError("Constraint draft is not finalized")

    try:
        draft = json.loads(draft_json)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid constraint draft JSON") from error

    if not isinstance(draft, dict):
        raise ValueError("Invalid constraint draft structure")

    if draft.get("finalized") is not True:
        raise ValueError("Draft JSON is not finalized")

    approval = draft.get("approval")

    if not isinstance(approval, dict):
        raise ValueError("Missing teacher approval")

    if approval.get("value") is not True:
        raise ValueError("Value constraints not approved")

    if approval.get("code") is not True:
        raise ValueError("Code constraints not approved")

    constraints = draft.get("constraints")

    if not isinstance(constraints, list):
        raise ValueError("Invalid draft constraints")

    approved_value = []
    approved_code = []

    for item in constraints:
        if not isinstance(item, dict):
            raise ValueError("Invalid constraint item")

        category = item.get("category")
        status = item.get("review_status")
        content = item.get("text")

        if category not in ("value", "code"):
            raise ValueError("Unsupported constraint category")

        if status not in ("ACCEPTED", "REJECTED"):
            raise ValueError("Unresolved constraint review status")

        # A REJECTED suggestion never reaches the assessment, so an
        # empty AI suggestion the teacher rejected must not block it.
        if status == "REJECTED" and not (
            isinstance(content, str) and content.strip()
        ):
            continue

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Invalid constraint text")

        if status == "ACCEPTED":
            if category == "value":
                approved_value.append(content.strip())
            else:
                approved_code.append(content.strip())

    cursor.execute("""
        SELECT value_constraints, code_constraints
        FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
        WHERE assessment_id = ?
    """, assessment_id)

    assessment = cursor.fetchone()

    if assessment is None:
        raise ValueError("Assessment not found")

    stored_value = (assessment[0] or "").strip()
    stored_code = (assessment[1] or "").strip()

    expected_value = "\n".join(approved_value)
    expected_code = "\n".join(approved_code)

    if stored_value != expected_value:
        raise ValueError(
            "Assessment value constraints differ from approved draft"
        )

    if stored_code != expected_code:
        raise ValueError(
            "Assessment code constraints differ from approved draft"
        )

    return {
        "valid": True,
        "assessment_id": assessment_id,
        "approved_value_constraints": expected_value,
        "approved_code_constraints": expected_code
    }
