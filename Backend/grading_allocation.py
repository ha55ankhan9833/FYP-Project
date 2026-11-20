"""
Single source of truth for splitting assessment marks between
equally weighted test cases and teacher rubric criteria.

Rounding policy (applies everywhere in grading):
- All marks are Python Decimal values, never floats.
- The test-case allocation is quantized to 0.01 using ROUND_HALF_UP.
- The rubric allocation is the exact remainder (total - test allocation),
  so the two allocations always add up to the assessment total and no
  mark is double-counted or lost.
- The equal-weight test score is quantized to 0.01 using ROUND_HALF_UP.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MARK_QUANTUM = Decimal("0.01")
HUNDRED = Decimal("100")


def to_mark(value, name):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {name}")

    if not number.is_finite():
        raise ValueError(f"Invalid {name}")

    return number


def quantize_mark(value):
    return Decimal(value).quantize(MARK_QUANTUM, rounding=ROUND_HALF_UP)


def split_allocation(total_marks, validation_percentage):
    """
    Return (test_marks_allocated, rubric_marks_allocated) as Decimals.

    validation_percentage is the share allocated to test cases.
    """

    total = to_mark(total_marks, "total marks")
    percentage = to_mark(validation_percentage, "validation percentage")

    if total <= 0:
        raise ValueError("Total marks must be positive")

    if not Decimal("0") <= percentage <= HUNDRED:
        raise ValueError("Validation percentage must be 0 to 100")

    test_marks = quantize_mark(total * percentage / HUNDRED)

    if test_marks > total:
        test_marks = total

    rubric_marks = total - test_marks

    return test_marks, rubric_marks
