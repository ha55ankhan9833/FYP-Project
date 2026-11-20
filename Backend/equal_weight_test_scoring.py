from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def calculate_equal_weight_score(
    tests_passed,
    tests_run,
    allocated_marks
):
    """
    Calculate equally weighted test-case marks.

    All tests have identical weight.
    No database access or student-code execution.
    """

    if type(tests_passed) is not int:
        raise ValueError("tests_passed must be an integer")

    if type(tests_run) is not int:
        raise ValueError("tests_run must be an integer")

    if not 0 <= tests_passed <= tests_run:
        raise ValueError("Invalid test counts")

    if tests_run == 0:
        raise ValueError("Cannot calculate score without tests")

    try:
        marks = Decimal(str(allocated_marks))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid allocated marks")

    if not marks.is_finite() or marks <= 0:
        raise ValueError("Allocated marks must be positive")

    per_test = marks / Decimal(tests_run)
    raw_score = (
        marks * Decimal(tests_passed) / Decimal(tests_run)
    )

    score = raw_score.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    return {
        "tests_passed": tests_passed,
        "tests_run": tests_run,
        "allocated_marks": str(marks),
        "marks_per_test": str(per_test),
        "test_score": str(score),
        "weighting": "EQUAL"
    }
