import json


def build_reference_evidence(test_cases):
    """
    Prepare reference-only evidence for offline LLM grading.

    No student code execution.
    No fabricated student outputs.
    No marks assigned.
    """

    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("Non-empty test case list required")

    evidence_cases = []
    seen_numbers = set()

    for case in test_cases:
        if not isinstance(case, dict):
            raise ValueError("Invalid test case")

        required = {
            "test_number",
            "input_data",
            "expected_output"
        }

        if not required.issubset(case):
            raise ValueError("Missing test case fields")

        number = case["test_number"]

        if type(number) is not int or number <= 0:
            raise ValueError("Invalid test number")

        if number in seen_numbers:
            raise ValueError("Duplicate test number")

        seen_numbers.add(number)

        input_data = case["input_data"]
        expected_output = case["expected_output"]

        if not isinstance(input_data, str):
            raise ValueError("Invalid input data")

        if not isinstance(expected_output, str):
            raise ValueError("Invalid expected output")

        evidence_cases.append({
            "test_number": number,
            "input": input_data,
            "expected_output": expected_output,
            "student_output": None,
            "student_executed": False,
            "passed": None
        })

    return {
        "evidence_type": "trusted_reference_only",
        "student_execution_performed": False,
        "student_pass_count": None,
        "student_test_count": None,
        "cases": evidence_cases,
        "warning": (
            "Expected outputs come from approved reference tests. "
            "Student outputs have NOT been observed. "
            "Do not claim any student test passed or failed."
        )
    }
