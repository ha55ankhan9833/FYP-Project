"""
Execution back-ends for reference-solution test generation.

host_runner    - runs an owner-approved, allow-listed reference on the host
sandbox_runner - runs any APPROVED reference inside the verified sandbox

The pure construction logic lives in test_case_builder and is
re-exported here for backwards compatibility.
"""

from cpp_runner import run_trusted_cpp
from test_case_builder import (  # noqa: F401  (re-exported)
    MAX_ADDITIONAL_INPUTS,
    MAX_EXPECTED_OUTPUT_CHARS,
    build_test_cases,
    merge_additional_inputs
)


def host_runner(executable_path, timeout=2):
    """Runs an allow-listed, human-reviewed reference ONLY."""

    def run_batch(stdin_values):
        outputs = []
        for index, stdin in enumerate(stdin_values, start=1):
            result = run_trusted_cpp(
                executable_path=executable_path,
                input_data=stdin,
                timeout=timeout
            )
            if not result["success"]:
                raise RuntimeError(
                    f"Reference solution failed on test {index}: "
                    f"{result['error']}")
            outputs.append(result["expected_output"])
        return outputs

    return run_batch


def sandbox_runner(sandbox, artifact):
    """Runs the reference inside the verified secure sandbox."""

    def run_batch(stdin_values):
        cases = [{"test_number": i, "input_data": s}
                 for i, s in enumerate(stdin_values, start=1)]
        results = sandbox.run_tests(artifact, cases)
        by_number = {r.get("test_number"): r for r in results}
        outputs = []
        for case in cases:
            r = by_number.get(case["test_number"])
            if r is None or r.get("status") != "completed":
                raise RuntimeError(
                    f"Reference solution failed in sandbox on test "
                    f"{case['test_number']}: "
                    f"{(r or {}).get('status', 'missing')}")
            outputs.append(r.get("stdout"))
        return outputs

    return run_batch


def generate_test_cases_from_trusted_reference(
    constraints,
    executable_path,
    additional_inputs=None
):
    """
    Backwards-compatible entry point (host execution of an
    allow-listed reference executable). Always exactly 10 cases.
    """

    return build_test_cases(
        constraints,
        host_runner(executable_path),
        additional_inputs=additional_inputs
    )
