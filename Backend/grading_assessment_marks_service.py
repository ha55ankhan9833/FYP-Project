from decimal import Decimal
from db import get_connection


ASSESSMENT_MARKS_SQL = """
SELECT
    a.assessment_id,
    a.total_marks
FROM dbo.Submission AS s
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a
    ON a.assessment_id = sec.assessment_id
WHERE s.submission_id = ?;
"""


def load_submission_assessment_marks(submission_id):
    """
    Read the assessment ID and maximum marks
    associated with a student submission.

    No database modifications.
    """

    if type(submission_id) is not int or submission_id <= 0:
        raise ValueError("Invalid submission ID")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(ASSESSMENT_MARKS_SQL, (submission_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        if row[1] is None:
            raise ValueError("Assessment total marks missing")

        total_marks = Decimal(str(row[1]))

        if not total_marks.is_finite() or total_marks <= 0:
            raise ValueError("Invalid assessment total marks")

        return {
            "assessment_id": row[0],
            "total_marks": total_marks
        }

    finally:
        if conn is not None:
            conn.close()
