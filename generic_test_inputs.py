"""
Generic, assessment-independent test INPUT generation from
teacher-approved value constraints.

Supported (fail closed on anything else):
    one integer-range rule per line, e.g.
        1 <= N <= 1000000
        -100 < X < 100
        1 <= a, b <= 1000          (several variables, same range)
    1 to MAX_VARIABLES distinct variables in total.

Input layout: the variables' values on one line, separated by single
spaces, in the order they are declared in the constraints. C++ `cin >>`
reads this layout regardless of whether the problem says "space" or
"newline" separated.

Single-variable constraints delegate to the original
test_input_generator.generate_integer_test_inputs so existing behaviour
(e.g. Assessment 4) is byte-for-byte unchanged.

No code is executed here; expected outputs come from the reference
solution in test_case_pipeline.
"""

import random
import re

from test_input_generator import generate_integer_test_inputs


# 3 variables -> 2 + 2*3 + 1 = 9 structural cases, which fits the fixed
# 10-test rule (test_case_policy); 4 variables would need 11.
MAX_VARIABLES = 3
MAX_ABS_BOUND = 10 ** 18          # fits C++ long long
MAX_TOTAL_CASES = 30
MAX_RANDOM_CASES = 20

_NAME = r"[A-Za-z_][A-Za-z_0-9]*"
_RULE = re.compile(
    rf"(-?\d+)\s*(<=|<)\s*({_NAME}(?:\s*,\s*{_NAME})*)\s*(<=|<)\s*(-?\d+)"
)


def parse_approved_value_constraints(constraints):
    """
    Return [{"variable", "minimum", "maximum"}] in declaration order.
    Raises ValueError for unsupported or contradictory constraints.
    """

    if not isinstance(constraints, str) or not constraints.strip():
        raise ValueError("Approved value constraints are required")

    variables = []
    seen = set()

    for raw_line in constraints.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue

        match = _RULE.fullmatch(line)
        if match is None:
            raise ValueError(
                f"Unsupported approved constraint for automatic test "
                f"generation: {line!r}"
            )

        lower, lower_op, names, upper_op, upper = match.groups()
        minimum = int(lower) + (1 if lower_op == "<" else 0)
        maximum = int(upper) - (1 if upper_op == "<" else 0)

        if minimum > maximum:
            raise ValueError(f"Empty range in constraint: {line!r}")

        if max(abs(minimum), abs(maximum)) > MAX_ABS_BOUND:
            raise ValueError("Constraint bounds exceed supported range")

        for name in (n.strip() for n in names.split(",")):
            key = name.casefold()
            if key in seen:
                raise ValueError(f"Variable {name} constrained twice")
            seen.add(key)
            variables.append({"variable": name,
                              "minimum": minimum,
                              "maximum": maximum})

    if not variables:
        raise ValueError("Approved value constraints are required")

    if len(variables) > MAX_VARIABLES:
        raise ValueError(
            f"At most {MAX_VARIABLES} constrained variables are supported")

    return variables


def _format(values):
    return " ".join(str(v) for v in values)


def generate_inputs(variables, random_count=5, seed=42):
    """
    Return [{"input_data", "category", "test_name"}], unique inputs.
    """

    if type(random_count) is not int or not 0 <= random_count <= MAX_RANDOM_CASES:
        raise ValueError(
            f"random_count must be an integer between 0 and {MAX_RANDOM_CASES}")

    if not variables:
        raise ValueError("At least one variable is required")

    if len(variables) == 1:
        v = variables[0]
        return generate_integer_test_inputs(
            minimum=v["minimum"], maximum=v["maximum"],
            random_count=random_count, seed=seed)

    rng = random.Random(seed)
    mins = [v["minimum"] for v in variables]
    maxs = [v["maximum"] for v in variables]
    mids = [lo + (hi - lo) // 2 for lo, hi in zip(mins, maxs)]

    cases, used = [], set()

    def add(values, category, name):
        key = tuple(values)
        if key in used or len(cases) >= MAX_TOTAL_CASES:
            return
        used.add(key)
        cases.append({"input_data": _format(values),
                      "category": category, "test_name": name})

    add(mins, "boundary", "All Minimum Values")
    add(maxs, "boundary", "All Maximum Values")

    for index, v in enumerate(variables):
        low = list(mids); low[index] = v["minimum"]
        high = list(mids); high[index] = v["maximum"]
        add(low, "boundary", f"{v['variable']} Minimum")
        add(high, "boundary", f"{v['variable']} Maximum")

    add(mids, "normal", "All Midpoint Values")

    total_space = 1
    for lo, hi in zip(mins, maxs):
        total_space *= (hi - lo + 1)

    target = min(random_count, total_space - len(used))
    attempts = 0
    added = 0
    while added < target and attempts < target * 50:
        attempts += 1
        values = [rng.randint(lo, hi) for lo, hi in zip(mins, maxs)]
        before = len(cases)
        add(values, "random", f"Random Case {added + 1}")
        if len(cases) > before:
            added += 1

    return cases


def validate_inputs(variables, test_cases):
    """Every input must contain exactly one in-range integer per variable."""

    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("Test cases are required")

    seen = set()

    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict) or not isinstance(case.get("input_data"), str):
            raise ValueError(f"Test {index}: invalid input")

        tokens = case["input_data"].split()

        if len(tokens) != len(variables):
            raise ValueError(
                f"Test {index}: expected {len(variables)} integer value(s)")

        values = []
        for token, v in zip(tokens, variables):
            if re.fullmatch(r"[+-]?\d+", token) is None:
                raise ValueError(f"Test {index}: {v['variable']} is not an integer")
            number = int(token)
            if not v["minimum"] <= number <= v["maximum"]:
                raise ValueError(
                    f"Test {index}: {v['variable']}={number} outside approved "
                    f"range [{v['minimum']}, {v['maximum']}]")
            values.append(number)

        key = tuple(values)
        if key in seen:
            raise ValueError(f"Test {index}: duplicate input")
        seen.add(key)

    return {"valid": True, "validated_count": len(test_cases),
            "variables": variables}
