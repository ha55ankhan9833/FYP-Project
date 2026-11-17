import json
from db import get_connection


def save_draft(teacher_id, draft, assessment_id=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO dbo.ConstraintReviewDraft
                (draft_id, teacher_id, assessment_id,
                 draft_json, is_finalized)
            VALUES (?, ?, ?, ?, ?)
        """,
            draft["draft_id"],
            teacher_id,
            assessment_id,
            json.dumps(draft),
            int(draft["finalized"])
        )

        conn.commit()
        return draft

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_draft(draft_id, teacher_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT draft_json
            FROM dbo.ConstraintReviewDraft
            WHERE draft_id = ?
              AND teacher_id = ?
        """, draft_id, teacher_id)

        row = cursor.fetchone()

        return json.loads(row[0]) if row else None

    finally:
        conn.close()


def update_draft(teacher_id, draft, expected_version):
    if not isinstance(expected_version, bytes) or len(expected_version) != 8:
        raise ValueError("Valid row version required")

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE dbo.ConstraintReviewDraft
            SET draft_json = ?,
                is_finalized = ?,
                updated_at = SYSUTCDATETIME()
            WHERE draft_id = ?
              AND teacher_id = ?
              AND is_finalized = 0
              AND row_version = ?
        """,
            json.dumps(draft),
            int(draft["finalized"]),
            draft["draft_id"],
            teacher_id,
            expected_version
        )

        if cursor.rowcount != 1:
            conn.rollback()
            raise ValueError(
                "Draft changed, finalized, or access denied"
            )

        conn.commit()
        return draft

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_draft_with_version(draft_id, teacher_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT draft_json, row_version
            FROM dbo.ConstraintReviewDraft
            WHERE draft_id = ?
              AND teacher_id = ?
        """, draft_id, teacher_id)

        row = cursor.fetchone()

        if row is None:
            return None, None

        return json.loads(row[0]), bytes(row[1])

    finally:
        conn.close()
