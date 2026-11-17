import json
from db import get_connection


def get_approved_constraints(draft_id, teacher_id, assessment_id=None):
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("Valid draft_id required")

    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT draft_json, is_finalized, assessment_id
            FROM dbo.ConstraintReviewDraft
            WHERE draft_id = ?
              AND teacher_id = ?
        """, draft_id, teacher_id)

        row = cursor.fetchone()

        if row is None:
            raise ValueError("Draft not found or access denied")

        draft = json.loads(row[0])

        if not row[1] or not draft.get("finalized"):
            raise ValueError("Draft must be finalized")

        approval = draft.get("approval", {})

        if not approval.get("value") or not approval.get("code"):
            raise ValueError("Both categories must be approved")

        linked_assessment = row[2]

        if linked_assessment is not None:
            if assessment_id is None or linked_assessment != assessment_id:
                raise ValueError("Draft belongs to another assessment")

        approved = {
            "value_constraints": [],
            "code_constraints": []
        }

        for constraint in draft.get("constraints", []):
            if constraint.get("review_status") != "ACCEPTED":
                continue

            category = constraint.get("category")
            value = constraint.get("text")

            if not isinstance(value, str) or not value.strip():
                continue

            if category == "value":
                approved["value_constraints"].append(value.strip())
            elif category == "code":
                approved["code_constraints"].append(value.strip())

        return approved

    finally:
        conn.close()



def prepare_approved_constraint_fields(data, teacher_id, assessment_id=None):
    if not isinstance(data, dict):
        raise ValueError("Valid assessment data required")

    if "value_constraints" in data or "code_constraints" in data:
        raise ValueError(
            "Direct constraint submission is not allowed; use draft_id"
        )

    draft_id = data.get("draft_id")

    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("Finalized draft_id required")

    approved = get_approved_constraints(
        draft_id=draft_id,
        teacher_id=teacher_id,
        assessment_id=assessment_id
    )

    return {
        "value_constraints": "\n".join(approved["value_constraints"]),
        "code_constraints": "\n".join(approved["code_constraints"])
    }



def link_draft_to_assessment(cursor, draft_id, teacher_id, assessment_id):
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("Valid draft_id required")

    if not isinstance(assessment_id, int) or assessment_id <= 0:
        raise ValueError("Valid assessment_id required")

    cursor.execute("""
        UPDATE dbo.ConstraintReviewDraft
        SET assessment_id = ?,
            updated_at = SYSUTCDATETIME()
        WHERE draft_id = ?
          AND teacher_id = ?
          AND assessment_id IS NULL
          AND is_finalized = 1
    """, assessment_id, draft_id, teacher_id)

    if cursor.rowcount != 1:
        raise ValueError(
            "Draft already linked, not finalized, or access denied"
        )



def get_locked_approved_constraints(cursor, draft_id, teacher_id):
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError("Valid draft_id required")

    cursor.execute("""
        SELECT draft_json, is_finalized, assessment_id
        FROM dbo.ConstraintReviewDraft WITH (UPDLOCK, HOLDLOCK)
        WHERE draft_id = ?
          AND teacher_id = ?
    """, draft_id, teacher_id)

    row = cursor.fetchone()

    if row is None:
        raise ValueError("Draft not found or access denied")

    if row[2] is not None:
        raise ValueError("Draft already linked to an assessment")

    draft = json.loads(row[0])

    if not row[1] or not draft.get("finalized"):
        raise ValueError("Draft must be finalized")

    approval = draft.get("approval", {})

    if approval.get("value") is not True or approval.get("code") is not True:
        raise ValueError("Both categories must be approved")

    approved = {
        "value_constraints": [],
        "code_constraints": []
    }

    for constraint in draft.get("constraints", []):
        if constraint.get("review_status") != "ACCEPTED":
            continue

        category = constraint.get("category")
        value = constraint.get("text")

        if not isinstance(value, str) or not value.strip():
            continue

        if category == "value":
            approved["value_constraints"].append(value.strip())
        elif category == "code":
            approved["code_constraints"].append(value.strip())

    return {
        "value_constraints": "\n".join(approved["value_constraints"]),
        "code_constraints": "\n".join(approved["code_constraints"])
    }
