import hashlib

from db import get_connection
from reference_solution_service import get_reference_solution


def review_reference_solution(assessment_id, teacher_id):
    solution = get_reference_solution(
        assessment_id, teacher_id
    )

    if solution is None:
        raise ValueError(
            "Reference solution not found or access denied"
        )

    calculated_hash = hashlib.sha256(
        solution["source_code"].encode("utf-8")
    ).hexdigest()

    if calculated_hash != solution["source_hash"]:
        raise ValueError("Stored source hash mismatch")

    return {
        "reference_solution_id": solution["reference_solution_id"],
        "assessment_id": solution["assessment_id"],
        "source_code": solution["source_code"],
        "source_hash": solution["source_hash"],
        "language": solution["language"],
        "approval_status": solution["approval_status"],
        "requires_explicit_approval": (
            solution["approval_status"] == "PENDING"
        )
    }


def approve_reference_solution(
    assessment_id,
    teacher_id,
    reviewed_source_hash
):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    if not isinstance(reviewed_source_hash, str):
        raise ValueError("Reviewed source hash required")

    if len(reviewed_source_hash) != 64:
        raise ValueError("Invalid reviewed source hash")

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                rs.reference_solution_id,
                rs.source_code,
                rs.source_hash,
                rs.approval_status
            FROM dbo.ReferenceSolution AS rs
                WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN dbo.Assessment AS a
                WITH (UPDLOCK, HOLDLOCK)
                ON a.assessment_id = rs.assessment_id
            WHERE rs.assessment_id = ?
              AND a.teacher_id = ?
        """, assessment_id, teacher_id)

        row = cursor.fetchone()

        if row is None:
            raise ValueError(
                "Reference solution not found or access denied"
            )

        reference_id = int(row[0])
        source_code = row[1]
        stored_hash = row[2]
        status = row[3]

        if status != "PENDING":
            raise ValueError(
                "Only PENDING reference solutions can be approved"
            )

        actual_hash = hashlib.sha256(
            source_code.encode("utf-8")
        ).hexdigest()

        if actual_hash != stored_hash:
            raise ValueError("Stored source hash mismatch")

        if reviewed_source_hash != actual_hash:
            raise ValueError(
                "Reviewed source hash does not match"
            )

        cursor.execute("""
            UPDATE dbo.ReferenceSolution
            SET approval_status = 'APPROVED',
                approved_at = SYSUTCDATETIME()
            WHERE reference_solution_id = ?
              AND approval_status = 'PENDING'
              AND source_hash = ?
        """, reference_id, actual_hash)

        if cursor.rowcount != 1:
            raise ValueError(
                "Reference solution approval conflict"
            )

        conn.commit()

        return {
            "success": True,
            "reference_solution_id": reference_id,
            "assessment_id": assessment_id,
            "approval_status": "APPROVED",
            "source_hash": actual_hash
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
