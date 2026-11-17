from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from equal_weight_test_scoring import calculate_equal_weight_score
from grading_allocation import split_allocation


def to_decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {name}")

    if not result.is_finite():
        raise ValueError(f"Invalid {name}")

    return result


def calculate_grading_score(
    total_marks,
    validation_percentage,
    tests_passed,
    tests_run,
    rubric_score
):
    """
    Combine equal-weight test marks and rubric marks.

    validation_percentage = test-case allocation.
    Remaining percentage = rubric allocation.

    Scores are calculated without executing student code.
    """

    total = to_decimal(total_marks, "total marks")
    percentage = to_decimal(
        validation_percentage,
        "validation percentage"
    )
    rubric = to_decimal(rubric_score, "rubric score")

    if total <= 0:
        raise ValueError("Total marks must be positive")

    if not Decimal("0") <= percentage <= Decimal("100"):
        raise ValueError("Validation percentage must be 0 to 100")

    # Shared rounding policy: see grading_allocation.py
    test_limit, rubric_limit = split_allocation(total, percentage)

    if not Decimal("0") <= rubric <= rubric_limit:
        raise ValueError("Rubric score exceeds allocated marks")

    if test_limit == 0:
        if type(tests_passed) is not int or tests_passed != 0:
            raise ValueError("Invalid test counts")
        if type(tests_run) is not int or tests_run != 0:
            raise ValueError("No test marks allocated")
        test_score = Decimal("0")
    else:
        test_result = calculate_equal_weight_score(
            tests_passed,
            tests_run,
            test_limit
        )
        test_score = Decimal(test_result["test_score"])

    suggested = (test_score + rubric).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    if suggested > total:
        suggested = total

    return {
        "total_marks": str(total),
        "validation_percentage": str(percentage),
        "test_marks_allocated": str(test_limit),
        "rubric_marks_allocated": str(rubric_limit),
        "test_score": str(test_score),
        "rubric_score": str(rubric),
        "suggested_score": str(suggested),
        "weighting": "EQUAL_TEST_CASES"
    }
