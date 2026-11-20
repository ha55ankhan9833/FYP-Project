"""Shared fail-closed publishing checks, inside the caller's transaction.

No execution, generation or writes. Saved outputs are read only, never
recomputed or accepted from a publishing request.
"""
from decimal import Decimal, InvalidOperation

from marks_allocation import compute_allocation, MarksAllocationError
from test_case_approval_validator import validate_assessment_approval
from test_case_validator import validate_generated_test_cases
from test_case_policy import REQUIRED_TEST_CASE_COUNT

SAVED_CASES_SQL = """
SELECT test_number, input_data, expected_output, marks
FROM dbo.TestCase WITH (HOLDLOCK)
WHERE assessment_id = ? ORDER BY test_number;
"""


def publication_problems(cursor, assessment_id, total_marks,
                         validation_percentage, assessment_type):
    from assessment_rubric_service import check_rubric_ready
    problems = []
    if assessment_type not in ("Assignment", "Quiz"):
        problems.append({"code": "ASSESSMENT_TYPE_UNSUPPORTED"})
    allocation = None
    try:
        allocation = compute_allocation(total_marks, validation_percentage)
    except MarksAllocationError:
        problems.append({"code": "MARKS_ALLOCATION_INVALID"})
    cursor.execute(SAVED_CASES_SQL, (assessment_id,))
    rows = cursor.fetchall()
    if len(rows) != REQUIRED_TEST_CASE_COUNT:
        problems.append({"code": "TEST_CASES_NOT_GENERATED",
                         "detail": f"{len(rows)} of {REQUIRED_TEST_CASE_COUNT} test cases"})
    else:
        try:
            if {r[0] for r in rows} != set(range(1, REQUIRED_TEST_CASE_COUNT + 1)):
                raise ValueError("Invalid case positions")
            if any(not isinstance(r[2], str) or not r[2].strip() for r in rows):
                raise ValueError("Missing expected output")
            if allocation and any(Decimal(str(r[3])) != Decimal(allocation["per_case_marks"]) for r in rows):
                raise ValueError("Unequal or incorrect marks")
        except (ValueError, TypeError, InvalidOperation):
            problems.append({"code": "TEST_CASES_INVALID",
                             "detail": "Saved cases require unique positions, outputs and exact equal marks"})
    try:
        approval = validate_assessment_approval(cursor, assessment_id)
        if rows:
            validate_generated_test_cases(
                approval["approved_value_constraints"],
                [{"input_data": r[1], "expected_output": r[2]} for r in rows],
                input_schema=approval.get("approved_input_schema"))
    except ValueError:
        problems.append({"code": "TEST_CASES_NOT_VALIDATED",
                         "detail": "Saved inputs must match the finalized teacher-approved constraints and schema"})
    try:
        if check_rubric_ready(cursor, assessment_id, total_marks, validation_percentage) is not True:
            raise ValueError("Rubric storage missing")
    except ValueError:
        problems.append({"code": "RUBRIC_INCOMPLETE",
                         "detail": "An approved complete rubric and available rubric storage are required"})
    return problems
