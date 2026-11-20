import re


def parse_integer_ranges(constraints):
    """
    Parse approved integer constraints such as:
    1 <= N <= 100
    -10 <= X <= 10
    0 < N < 50

    Returns a list of structured ranges.
    """
    if not isinstance(constraints, str):
        raise ValueError("Constraints must be text")

    pattern = re.compile(
        r"(?<![\w.])"
        r"(-?\d+)\s*(<=|<)\s*"
        r"([A-Za-z_][A-Za-z_0-9]*)\s*"
        r"(<=|<)\s*(-?\d+)"
        r"(?![\w.])"
    )

    results = []

    for match in pattern.finditer(constraints):
        lower = int(match.group(1))
        lower_op = match.group(2)
        variable = match.group(3)
        upper_op = match.group(4)
        upper = int(match.group(5))

        minimum = lower + (1 if lower_op == "<" else 0)
        maximum = upper - (1 if upper_op == "<" else 0)

        if minimum > maximum:
            raise ValueError(
                f"Invalid range for {variable}: "
                f"{minimum} > {maximum}"
            )

        results.append({
            "variable": variable,
            "minimum": minimum,
            "maximum": maximum,
            "data_type": "integer"
        })

    return results
