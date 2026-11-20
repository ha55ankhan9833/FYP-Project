from decimal import Decimal, InvalidOperation
from db import get_connection
from grading_allocation import split_allocation


GRADING_CONFIG_SQL = """
SELECT
    a.assessment_id,
    a.total_marks,
    a.validation_percentage,
    a.late_penalty_marks
FROM dbo.Assessment AS a
WHERE a.assessment_id = ?;
"""


PROBLEM_CONTEXT_SQL = """
SELECT problem_statement, input_format, output_format,
       value_constraints, additional_constraints, code_constraints
FROM dbo.Assessment
WHERE assessment_id = ?;
"""


def load_assessment_problem_context(assessment_id):
    """Read-only problem text used as LLM rubric-grading context."""

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(PROBLEM_CONTEXT_SQL, (assessment_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        constraints = "\n".join(
            part.strip() for part in (row[3], row[4])
            if isinstance(part, str) and part.strip()
        )

        # Teacher-approved code requirements (e.g. recursion_required)
        # are rubric-relevant, so the AI grader must see them.
        if isinstance(row[5], str) and row[5].strip():
            constraints += (
                ("\n" if constraints else "")
                + "Required code constraints: " + row[5].strip()
            )

        return {
            "problem": row[0],
            "input_format": row[1],
            "output_format": row[2],
            "constraints": constraints or "No additional constraints.",
            # Teacher-approved code constraints as a list, for static checks.
            "code_constraints": [
                line.strip() for line in (row[5] or "").splitlines()
                if line.strip()
            ]
        }

    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def load_grading_configuration(assessment_id):
    """
    Load assessment-specific grading allocation.

    validation_percentage determines the percentage
    allocated to equally weighted test cases.

    Remaining marks are allocated to rubric grading.
    """

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(GRADING_CONFIG_SQL, (assessment_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        try:
            total = Decimal(str(row[1]))
            percentage = Decimal(str(row[2]))
            penalty = Decimal(str(row[3]))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("Invalid grading configuration")

        if not total.is_finite() or total <= 0:
            raise ValueError("Invalid assessment total marks")

        if not percentage.is_finite() or not (
            Decimal("0") <= percentage <= Decimal("100")
        ):
            raise ValueError("Invalid validation percentage")

        if not penalty.is_finite() or penalty < 0:
            raise ValueError("Invalid late penalty")

        test_marks, rubric_marks = split_allocation(total, percentage)

        return {
            "assessment_id": int(row[0]),
            "total_marks": total,
            "validation_percentage": percentage,
            "test_marks_allocated": test_marks,
            "rubric_marks_allocated": rubric_marks,
            "late_penalty_marks": penalty
        }

    finally:
        if conn is not None:
            conn.rollback()
            conn.close()
