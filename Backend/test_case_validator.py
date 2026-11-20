from generic_test_inputs import (
    parse_approved_value_constraints,
    validate_inputs
)


def validate_generated_test_cases(constraints, test_cases, input_schema=None):
    """
    Validate generated test inputs against the teacher-approved
    value constraints (see generic_test_inputs for the supported,
    fail-closed constraint grammar).

    Every approved line must be an interpretable integer range;
    anything else is rejected rather than ignored.

    input_schema: the CHECKED, teacher-approved structured schema from the
    finalized approval draft (input_schema.check_schema). When given, inputs
    are validated against it with their exact text (spaces and newlines are
    significant) instead of the legacy integer rule. When None, behaviour is
    exactly the legacy rule.
    """

    if input_schema is not None:
        return validate_structured_test_cases(input_schema, test_cases)

    variables = parse_approved_value_constraints(constraints)
    result = validate_inputs(variables, test_cases)

    if len(variables) == 1:
        # Backwards-compatible keys for single-integer assessments.
        result["minimum"] = variables[0]["minimum"]
        result["maximum"] = variables[0]["maximum"]

    return result


def structured_input_key(text):
    """Duplicate key for structured inputs: exact text, final newline optional."""
    return text if text.endswith("\n") else text + "\n"


def validate_structured_test_cases(input_schema, test_cases):
    from input_schema import validate_input

    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("Test cases are required")

    seen = set()

    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict) or not isinstance(case.get("input_data"), str):
            raise ValueError(f"Test {index}: invalid input")

        text = case["input_data"]

        if "\r" in text:
            raise ValueError(f"Test {index}: carriage returns are not allowed")

        check = validate_input(input_schema, text)

        if not check["valid"]:
            first = check["errors"][0]
            raise ValueError(f"Test {index}: {first['code']}: {first['message']}")

        key = structured_input_key(text)

        if key in seen:
            raise ValueError(f"Test {index}: duplicate input")

        seen.add(key)

    return {"valid": True, "validated_count": len(test_cases),
            "input_schema": True}
