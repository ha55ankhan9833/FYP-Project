from generic_test_inputs import (
    parse_approved_value_constraints,
    validate_inputs
)


def validate_generated_test_cases(constraints, test_cases):
    """
    Validate generated test inputs against the teacher-approved
    value constraints (see generic_test_inputs for the supported,
    fail-closed constraint grammar).

    Every approved line must be an interpretable integer range;
    anything else is rejected rather than ignored.
    """

    variables = parse_approved_value_constraints(constraints)
    result = validate_inputs(variables, test_cases)

    if len(variables) == 1:
        # Backwards-compatible keys for single-integer assessments.
        result["minimum"] = variables[0]["minimum"]
        result["maximum"] = variables[0]["maximum"]

    return result
