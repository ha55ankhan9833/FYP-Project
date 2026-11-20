import random


def generate_integer_test_inputs(
    minimum,
    maximum,
    random_count=5,
    seed=42
):
    """
    Generate unique valid integer test inputs.

    Categories:
    - boundary
    - near_boundary
    - normal
    - random
    """

    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
    ):
        raise ValueError("Bounds must be integers")

    if minimum > maximum:
        raise ValueError("Minimum cannot exceed maximum")

    if (
        isinstance(random_count, bool)
        or not isinstance(random_count, int)
        or random_count < 0
    ):
        raise ValueError("random_count must be non-negative")

    rng = random.Random(seed)
    cases = []
    used = set()

    def add_case(value, category, name):
        if minimum <= value <= maximum and value not in used:
            used.add(value)
            cases.append({
                "input_data": str(value),
                "category": category,
                "test_name": name
            })

    add_case(minimum, "boundary", "Minimum Value")
    add_case(maximum, "boundary", "Maximum Value")

    add_case(
        minimum + 1,
        "near_boundary",
        "Just Above Minimum"
    )

    add_case(
        maximum - 1,
        "near_boundary",
        "Just Below Maximum"
    )

    midpoint = minimum + (maximum - minimum) // 2

    add_case(midpoint, "normal", "Midpoint Value")

    if minimum <= 0 <= maximum:
        add_case(0, "edge", "Zero Value")

    if minimum <= 1 <= maximum:
        add_case(1, "edge", "One Value")

    if minimum <= -1 <= maximum:
        add_case(-1, "edge", "Negative One Value")

    remaining = (
        maximum - minimum + 1 - len(used)
    )

    target = min(random_count, remaining)

    if target > 0:
        if remaining <= 10000:
            available = [
                value
                for value in range(minimum, maximum + 1)
                if value not in used
            ]

            selected = rng.sample(available, target)
        else:
            selected = []
            while len(selected) < target:
                value = rng.randint(minimum, maximum)

                if value not in used:
                    used.add(value)
                    selected.append(value)

            for value in selected:
                used.remove(value)

        for index, value in enumerate(selected, start=1):
            add_case(
                value,
                "random",
                f"Random Case {index}"
            )

    return cases
