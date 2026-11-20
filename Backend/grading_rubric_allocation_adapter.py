from decimal import Decimal, InvalidOperation
from grading_score_calculator import calculate_grading_score
from grading_allocation import split_allocation


def as_decimal(value, name):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {name}")

    if not number.is_finite():
        raise ValueError(f"Invalid {name}")

    return number


def validate_rubric_allocation(rubric, allocated_marks):
    """
    Validate teacher rubric against allocated rubric marks.
    """

    maximum = as_decimal(allocated_marks, "rubric allocation")

    if maximum < 0:
        raise ValueError("Negative rubric allocation")

    if maximum == 0:
        if rubric not in (None, []):
            raise ValueError("Rubric must be empty for zero allocation")
        return []

    if not isinstance(rubric, list) or not rubric:
        raise ValueError("Rubric criteria required")

    names = set()
    total = Decimal("0")

    for item in rubric:
        if not isinstance(item, dict):
            raise ValueError("Invalid rubric criterion")

        name = item.get("criterion_name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid criterion name")

        # Names are compared case-insensitively and trimmed so that
        # "Efficiency" and " efficiency" cannot both exist.
        normalized = name.strip().casefold()

        if normalized in names:
            raise ValueError("Duplicate criterion name")

        names.add(normalized)

        allowed = as_decimal(item.get("max_marks"), "criterion maximum")

        if allowed < 0:
            raise ValueError("Negative criterion maximum")

        total += allowed

    if total != maximum:
        raise ValueError("Rubric maximum does not match allocation")

    return rubric


def combine_grading_results(
    config,
    rubric,
    ai_result,
    tests_passed,
    tests_run
):
    """
    Calculate suggested marks with independently validated
    rubric awards and equally weighted test-case marks.

    Does not execute student code or call the LLM.
    """

    if not isinstance(config, dict):
        raise ValueError("Invalid grading configuration")

    total_marks = as_decimal(
        config.get("total_marks"), "total marks"
    )
    percentage = as_decimal(
        config.get("validation_percentage"),
        "validation percentage"
    )

    if total_marks <= 0 or not Decimal("0") <= percentage <= Decimal("100"):
        raise ValueError("Invalid assessment configuration")

    # Shared rounding policy: see grading_allocation.py
    _, rubric_limit = split_allocation(total_marks, percentage)

    validate_rubric_allocation(rubric, rubric_limit)

    if rubric_limit == 0:
        if ai_result is not None:
            raise ValueError("AI result not expected for zero rubric allocation")
        rubric_score = Decimal("0")
    else:
        if not isinstance(ai_result, dict):
            raise ValueError("AI grading result required")

        criteria = ai_result.get("criteria")

        if not isinstance(criteria, list):
            raise ValueError("AI criteria missing")

        expected = {
            item["criterion_name"]: as_decimal(
                item["max_marks"], "criterion maximum"
            )
            for item in rubric
        }

        if len(criteria) != len(expected):
            raise ValueError("AI criterion count mismatch")

        seen = set()
        rubric_score = Decimal("0")

        for item in criteria:
            if not isinstance(item, dict):
                raise ValueError("Invalid AI criterion")

            name = item.get("criterion_name")

            if name not in expected or name in seen:
                raise ValueError("Unexpected or duplicate AI criterion")

            seen.add(name)

            rating = as_decimal(
                item.get("rating"), "criterion rating"
            )

            if not Decimal("0") <= rating <= expected[name]:
                raise ValueError("AI criterion rating out of range")

            rubric_score += rating

        if "suggested_score" in ai_result:
            claimed = as_decimal(
                ai_result["suggested_score"],
                "AI suggested score"
            )

            if claimed != rubric_score:
                raise ValueError("AI score does not match criterion sum")

    result = calculate_grading_score(
        total_marks=total_marks,
        validation_percentage=percentage,
        tests_passed=tests_passed,
        tests_run=tests_run,
        rubric_score=rubric_score
    )

    result["ai_called"] = rubric_limit > 0
    result["rubric_validated"] = True

    return result
