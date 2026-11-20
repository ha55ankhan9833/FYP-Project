from db import get_connection
from test_case_policy import enforce_test_case_count


def load_grading_test_cases(assessment_id):
    """
    Internal backend-only test-case retrieval.

    Returns approved reference inputs and expected outputs.
    Does not execute student code or determine marks.

    Caller must enforce submission authorization.
    Never expose returned hidden cases to students.
    """

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Assessment ID must be a positive integer")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                test_number,
                test_name,
                category,
                input_data,
                expected_output
            FROM dbo.TestCase
            WHERE assessment_id = ?
            ORDER BY test_number
        """, assessment_id)

        rows = cursor.fetchall()

        if not rows:
            raise ValueError(
                "No saved test cases found for this assessment"
            )

        # Fail closed: grading an assessment whose stored test set breaks
        # the fixed 10-test rule would change every test's weight. The
        # pipeline treats this as a configuration failure (job released,
        # never an automatic zero).
        enforce_test_case_count(len(rows), f"Assessment {assessment_id}")

        test_cases = []

        for row in rows:
            test_cases.append({
                "test_number": int(row[0]),
                "test_name": str(row[1]),
                "category": str(row[2]),
                "input_data": str(row[3]),
                "expected_output": str(row[4])
            })

        return test_cases

    finally:
        if conn is not None:
            conn.close()
