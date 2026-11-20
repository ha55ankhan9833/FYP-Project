from student_execution_evidence import (
    validate_student_execution_evidence
)


def build_student_grading_evidence(reference_cases, execution_results):
    """
    Build grading evidence from trusted sandbox results.

    Caller must guarantee execution_results came from a
    trusted, isolated execution orchestrator.

    This function does not execute student code.
    """

    validated = validate_student_execution_evidence(
        reference_cases,
        execution_results
    )

    # Inputs are carried through (never trusted for pass/fail) so failed
    # tests can be tied to concrete inputs for the AI and the teacher.
    inputs = {c["test_number"]: c.get("input_data") for c in reference_cases
              if isinstance(c, dict)}

    evidence_cases = []

    for case in validated["cases"]:
        evidence_cases.append({
            "test_number": case["test_number"],
            "input_data": inputs.get(case["test_number"]),
            "expected_output": case["expected_output"],
            "student_output": case["student_output"],
            "execution_status": case["status"],
            "passed": case["passed"]
        })

    return {
        "evidence_type": "student_execution_results",
        "student_execution_performed": True,
        "tests_run": validated["tests_run"],
        "tests_passed": validated["tests_passed"],
        "cases": evidence_cases,
        "warning": (
            "Execution results must originate from the "
            "trusted sandbox orchestrator. This function "
            "does not independently verify their origin."
        )
    }
