from db import get_connection


def load_pending_submissions(limit=20):
    """
    Read-only discovery of submissions without evaluations.

    This does not claim jobs or prevent concurrent workers
    from discovering the same submission.
    """

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be an integer between 1 and 100")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT TOP (?)
                s.submission_id,
                s.student_id,
                s.assessment_section_id,
                s.file_name,
                s.uploaded_at
            FROM dbo.Submission s
            WHERE NOT EXISTS (
                SELECT 1
                FROM dbo.Evaluation e
                WHERE e.submission_id = s.submission_id
            )
            ORDER BY s.uploaded_at ASC, s.submission_id ASC
        """, (limit,))

        rows = cursor.fetchall()

        return [
            {
                "submission_id": row[0],
                "student_id": row[1],
                "assessment_section_id": row[2],
                "file_name": row[3],
                "uploaded_at": (
                    row[4].isoformat()
                    if row[4] is not None
                    else None
                )
            }
            for row in rows
        ]

    finally:
        if conn is not None:
            conn.close()
