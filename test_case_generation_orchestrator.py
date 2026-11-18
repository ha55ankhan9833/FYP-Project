"""
Generic test-case generation for ANY assessment the authenticated
teacher owns.

assessment_id and teacher_id come from the route (URL + verified JWT).
There are no hard-coded assessment, reference or constraint values.

Execution of the teacher's APPROVED reference solution:
- 'sandbox'      when secure_execution_gate provides a verified sandbox;
- 'trusted_host' only for references whose exact SHA-256 is in the
                 human-reviewed allowlist (trusted_reference_hashes.json);
- otherwise generation is refused. Student code is never run here.
"""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from cpp_compiler import compile_cpp
from diagnostics_sanitizer import sanitize_compiler_diagnostics
from generic_test_inputs import parse_approved_value_constraints
from secure_execution_gate import get_secure_sandbox
from secure_test_case_save import save_generated_test_cases_secure
from test_case_generation_service import (
    choose_execution_mode,
    load_trusted_reference_hashes,
    validate_generation_preflight
)
from test_case_builder import build_structured_test_cases, select_inputs, select_structured_inputs
from test_case_pipeline import build_test_cases, host_runner, sandbox_runner
from test_case_validator import validate_generated_test_cases


class HostExecutionNotApproved(ValueError):
    pass


class ExpectedOutputsUnavailable(ValueError):
    """
    Structured inputs are valid, but no approved mechanism (verified sandbox
    or allow-listed trusted reference) can produce verified expected outputs.
    Nothing was executed or saved; `inputs` are the exact validated inputs
    that would be used, so the teacher can review them.
    """

    def __init__(self, reason, inputs):
        super().__init__(reason)
        self.reason = reason
        self.inputs = inputs


def _generate_on_host(source_code, constraints, additional_inputs=None):
    # Defence in depth: re-check the owner-approved allowlist right
    # here, so no future caller can reach host execution by skipping
    # choose_execution_mode().
    digest = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
    if digest not in load_trusted_reference_hashes():
        raise HostExecutionNotApproved(
            "Reference solution has not been reviewed for host execution "
            "and no secure sandbox is available")

    with TemporaryDirectory(prefix="fyp_trusted_generation_") as folder:
        source_path = Path(folder) / "reference.cpp"
        executable_path = Path(folder) / "reference.exe"
        source_path.write_text(source_code, encoding="utf-8")

        compilation = compile_cpp(source_path, executable_path, timeout=30)

        if not compilation["success"]:
            # This message reaches the server log; keep host paths out.
            raise RuntimeError(
                "Trusted reference compilation failed: "
                + sanitize_compiler_diagnostics(str(compilation["error"]),
                                                folder)
            )

        return build_test_cases(
            constraints, host_runner(executable_path),
            additional_inputs=additional_inputs)


def _generate_in_sandbox(sandbox, source_code, constraints,
                         additional_inputs=None):
    compiled = sandbox.compile(source_code)

    try:
        if compiled.get("status") != "COMPILED":
            raise RuntimeError(
                "Reference solution did not compile in the sandbox: "
                + str(compiled.get("diagnostics", ""))[:2000]
            )
        return build_test_cases(
            constraints, sandbox_runner(sandbox, compiled["artifact"]),
            additional_inputs=additional_inputs)
    finally:
        sandbox.cleanup(compiled.get("artifact"))


def _structured_on_host(source_code, input_schema, additional_inputs):
    digest = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
    if digest not in load_trusted_reference_hashes():
        raise HostExecutionNotApproved(
            "Reference solution has not been reviewed for host execution "
            "and no secure sandbox is available")

    with TemporaryDirectory(prefix="fyp_trusted_generation_") as folder:
        source_path = Path(folder) / "reference.cpp"
        executable_path = Path(folder) / "reference.exe"
        source_path.write_text(source_code, encoding="utf-8")
        compilation = compile_cpp(source_path, executable_path, timeout=30)
        if not compilation["success"]:
            raise RuntimeError(
                "Trusted reference compilation failed: "
                + sanitize_compiler_diagnostics(str(compilation["error"]), folder))
        return build_structured_test_cases(
            input_schema, host_runner(executable_path), additional_inputs)


def _structured_in_sandbox(sandbox, source_code, input_schema, additional_inputs):
    compiled = sandbox.compile(source_code)
    try:
        if compiled.get("status") != "COMPILED":
            raise RuntimeError(
                "Reference solution did not compile in the sandbox: "
                + str(compiled.get("diagnostics", ""))[:2000])
        return build_structured_test_cases(
            input_schema, sandbox_runner(sandbox, compiled["artifact"]), additional_inputs)
    finally:
        sandbox.cleanup(compiled.get("artifact"))


def _generate_structured(assessment_id, teacher_id, preflight, additional_inputs):
    """Teacher-approved structured input schema: exact input text end to end."""
    input_schema = preflight["input_schema"]
    source_code = preflight["source_code"]
    source_hash = preflight["source_hash"]

    # Validate and select the exact 10 inputs before anything runs
    # (raises StructuredInputError with every problem listed).
    inputs = select_structured_inputs(input_schema, additional_inputs)

    sandbox = get_secure_sandbox()
    try:
        mode = choose_execution_mode(source_hash, sandbox is not None)
    except ValueError as error:
        raise ExpectedOutputsUnavailable(str(error), inputs) from error

    if mode == "sandbox":
        test_cases = _structured_in_sandbox(sandbox, source_code, input_schema,
                                            additional_inputs)
    else:
        test_cases = _structured_on_host(source_code, input_schema, additional_inputs)

    validate_generated_test_cases(preflight["constraints"], test_cases,
                                  input_schema=input_schema)

    saved = save_generated_test_cases_secure(
        assessment_id=assessment_id,
        teacher_id=teacher_id,
        reference_solution_id=preflight["reference_solution_id"],
        expected_source_hash=source_hash,
        test_cases=test_cases,
        structured=True
    )

    return {
        "success": True,
        "assessment_id": assessment_id,
        "reference_solution_id": preflight["reference_solution_id"],
        "source_hash": source_hash,
        "execution_mode": mode,
        "input_format": "approved_input_schema",
        "generated_count": len(test_cases),
        "saved_count": saved["saved_count"],
        "marks_per_test": saved["marks_per_test"],
        "test_marks_allocated": saved["test_marks_allocated"]
    }


def generate_and_save_test_cases(
    assessment_id,
    teacher_id,
    additional_inputs=None
):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    preflight = validate_generation_preflight(assessment_id, teacher_id)

    source_code = preflight["source_code"]
    source_hash = preflight["source_hash"]
    constraints = preflight["constraints"]

    if hashlib.sha256(source_code.encode("utf-8")).hexdigest() != source_hash:
        raise ValueError("Reference source changed after preflight")

    if preflight.get("input_schema") is not None:
        return _generate_structured(assessment_id, teacher_id, preflight,
                                    additional_inputs)

    # Fail fast (before running anything) on unsupported constraints or
    # inputs that cannot form exactly 10 test cases.
    select_inputs(parse_approved_value_constraints(constraints),
                  additional_inputs)

    sandbox = get_secure_sandbox()
    mode = choose_execution_mode(source_hash, sandbox is not None)

    if mode == "sandbox":
        test_cases = _generate_in_sandbox(
            sandbox, source_code, constraints, additional_inputs)
    else:
        test_cases = _generate_on_host(
            source_code, constraints, additional_inputs)

    validate_generated_test_cases(constraints, test_cases)

    # Saving re-locks and re-validates ownership, approval, reference
    # hash and "no existing test cases" in its own transaction.
    saved = save_generated_test_cases_secure(
        assessment_id=assessment_id,
        teacher_id=teacher_id,
        reference_solution_id=preflight["reference_solution_id"],
        expected_source_hash=source_hash,
        test_cases=test_cases
    )

    return {
        "success": True,
        "assessment_id": assessment_id,
        "reference_solution_id": preflight["reference_solution_id"],
        "source_hash": source_hash,
        "execution_mode": mode,
        "generated_count": len(test_cases),
        "saved_count": saved["saved_count"],
        "marks_per_test": saved["marks_per_test"],
        "test_marks_allocated": saved["test_marks_allocated"]
    }
