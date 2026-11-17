from decimal import Decimal
from db import get_connection
from test_case_validator import validate_generated_test_cases
from test_case_approval_validator import validate_assessment_approval


def save_generated_test_cases(assessment_id, test_cases):
    """
    Atomically save generated test cases for an assessment.

    Only call after approved constraints and reference outputs
    have been independently verified.
    """

    if isinstance(assessment_id, bool) or not isinstance(assessment_id, int):
        raise ValueError("Assessment ID must be an integer")

    if assessment_id <= 0:
        raise ValueError("Assessment ID must be positive")

    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("At least one test case is required")

    required_fields = {
        "test_number", "test_name", "category",
        "input_data", "expected_output"
    }

    numbers = set()
    inputs = set()

    for case in test_cases:
        if not isinstance(case, dict):
            raise ValueError("Each test case must be a dictionary")

        if not required_fields.issubset(case):
            raise ValueError("Missing required test case fields")

        number = case["test_number"]

        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("Invalid test number")

        if number in numbers:
            raise ValueError("Duplicate test number")

        numbers.add(number)

        for field in ("input_data", "expected_output"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"Invalid {field}")

        if case["input_data"] in inputs:
            raise ValueError("Duplicate test input")

        inputs.add(case["input_data"])

        for field in ("test_name", "category"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"Invalid {field}")

    if numbers != set(range(1, len(test_cases) + 1)):
        raise ValueError("Test numbers must be consecutive starting at 1")

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT total_marks, value_constraints, code_constraints
            FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        row = cursor.fetchone()

        if row is None:
            raise ValueError("Assessment not found")

        if row[2] and str(row[2]).strip():
            raise ValueError(
                "Code constraints require separate validation"
            )

        approval_result = validate_assessment_approval(
            cursor,
            assessment_id
        )

        if approval_result["approved_value_constraints"] != str(row[1] or "").strip():
            raise ValueError(
                "Approved constraints changed during saving"
            )

        validate_generated_test_cases(
            str(row[1] or ""),
            test_cases
        )

        total_marks = Decimal(str(row[0]))

        if not total_marks.is_finite() or total_marks <= 0:
            raise ValueError("Assessment total marks must be positive")

        cursor.execute("""
            SELECT COUNT(*)
            FROM dbo.TestCase WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        if cursor.fetchone()[0] != 0:
            raise ValueError(
                "Assessment already has test cases; refusing duplicates"
            )

        # Allocate marks in hundredths to avoid rounding drift.
        total_cents = int(total_marks * 100)

        if Decimal(total_cents) != total_marks * 100:
            raise ValueError("Total marks exceed two-decimal precision")

        if total_cents < len(test_cases):
            raise ValueError(
                "Not enough marks to give every test case 0.01"
            )

        base, remainder = divmod(total_cents, len(test_cases))

        for index, case in enumerate(test_cases):
            cents = base + (1 if index < remainder else 0)
            marks = Decimal(cents) / Decimal(100)

            cursor.execute("""
                INSERT INTO dbo.TestCase (
                    assessment_id,
                    test_number,
                    test_name,
                    category,
                    visibility,
                    input_data,
                    expected_output,
                    marks
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                assessment_id,
                case["test_number"],
                case["test_name"],
                case["category"],
                "hidden",
                case["input_data"],
                case["expected_output"],
                marks
            )

        conn.commit()

        return {
            "success": True,
            "assessment_id": assessment_id,
            "saved_count": len(test_cases),
            "total_marks": str(total_marks)
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
