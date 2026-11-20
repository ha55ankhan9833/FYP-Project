import hashlib

from db import get_connection


def save_reference_solution(assessment_id, teacher_id, source_code):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("C++ source code is required")

    if len(source_code.encode("utf-8")) > 200_000:
        raise ValueError("Source code exceeds 200 KB limit")

    source_hash = hashlib.sha256(
        source_code.encode("utf-8")
    ).hexdigest()

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT assessment_id
            FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
              AND teacher_id = ?
        """, assessment_id, teacher_id)

        if cursor.fetchone() is None:
            raise ValueError("Assessment not found or access denied")

        cursor.execute("""
            SELECT reference_solution_id
            FROM dbo.ReferenceSolution WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        if cursor.fetchone() is not None:
            raise ValueError(
                "Reference solution already exists; "
                "replacement workflow is not implemented"
            )

        cursor.execute("""
            INSERT INTO dbo.ReferenceSolution (
                assessment_id,
                source_code,
                source_hash,
                language,
                approval_status
            )
            OUTPUT INSERTED.reference_solution_id
            VALUES (?, ?, ?, 'cpp', 'PENDING')
        """, assessment_id, source_code, source_hash)

        reference_solution_id = int(cursor.fetchone()[0])

        conn.commit()

        return {
            "success": True,
            "reference_solution_id": reference_solution_id,
            "assessment_id": assessment_id,
            "source_hash": source_hash,
            "approval_status": "PENDING"
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_reference_solution(assessment_id, teacher_id):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                rs.reference_solution_id,
                rs.assessment_id,
                rs.source_code,
                rs.source_hash,
                rs.language,
                rs.approval_status,
                rs.created_at,
                rs.approved_at
            FROM dbo.ReferenceSolution AS rs
            INNER JOIN dbo.Assessment AS a
                ON a.assessment_id = rs.assessment_id
            WHERE rs.assessment_id = ?
              AND a.teacher_id = ?
        """, assessment_id, teacher_id)

        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "reference_solution_id": int(row[0]),
            "assessment_id": int(row[1]),
            "source_code": row[2],
            "source_hash": row[3],
            "language": row[4],
            "approval_status": row[5],
            "created_at": (
                row[6].isoformat() if row[6] else None
            ),
            "approved_at": (
                row[7].isoformat() if row[7] else None
            )
        }

    finally:
        conn.close()
