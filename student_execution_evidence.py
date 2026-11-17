from output_comparison import DEFAULT_POLICY, outputs_match


def validate_student_execution_evidence(reference_cases, results):
    """
    Validate results supplied by a trusted execution orchestrator.

    This function does NOT execute code and does NOT establish
    that the caller or supplied results are trustworthy.
    Only the sandbox orchestrator may supply production results.
    """

    if not isinstance(reference_cases, list) or not reference_cases:
        raise ValueError("Reference cases required")

    if not isinstance(results, list):
        raise ValueError("Execution results must be a list")

    reference_by_number = {}

    for case in reference_cases:
        if not isinstance(case, dict):
            raise ValueError("Invalid reference case")

        number = case.get("test_number")

        if type(number) is not int or number <= 0:
            raise ValueError("Invalid reference test number")

        if number in reference_by_number:
            raise ValueError("Duplicate reference test number")

        if not isinstance(case.get("expected_output"), str):
            raise ValueError("Invalid expected output")

        reference_by_number[number] = case

    if len(results) != len(reference_cases):
        raise ValueError("Execution result count mismatch")

    seen = set()
    validated = []

    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Invalid execution result")

        number = result.get("test_number")

        if type(number) is not int or number not in reference_by_number:
            raise ValueError("Unknown test number")

        if number in seen:
            raise ValueError("Duplicate execution result")

        seen.add(number)

        status = result.get("status")

        if status not in ("completed", "timeout", "runtime_error"):
            raise ValueError("Invalid execution status")

        output = result.get("stdout")

        if status == "completed":
            if not isinstance(output, str):
                raise ValueError("Completed execution requires stdout")
        elif output is not None:
            raise ValueError("Failed execution must have null stdout")

        expected = reference_by_number[number]["expected_output"]

        # Single comparison rule for the whole system (output_comparison).
        passed = (
            status == "completed"
            and outputs_match(output, expected, DEFAULT_POLICY)
        )

        validated.append({
            "test_number": number,
            "status": status,
            "student_output": output,
            "expected_output": expected,
            "passed": passed
        })

    validated.sort(key=lambda item: item["test_number"])

    return {
        "student_execution_performed": True,
        "tests_run": len(validated),
        "tests_passed": sum(item["passed"] for item in validated),
        "cases": validated
    }
