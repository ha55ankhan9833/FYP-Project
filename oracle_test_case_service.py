"""
Test-case generation from a VERIFIED Python oracle (no C++ execution).

Same safety chain as the C++ generation path, with one substitution:
  preflight (owner, approved constraints, APPROVED reference with intact
  hash, no existing tests)  ->  oracle must be bound to that exact
  reference hash and verified for the approved domain  ->  input
  selection  ->  outputs computed server-side by two independent
  algorithms (must agree, computed twice)  ->  constraint validation  ->
  existing atomic secure save (re-checks everything).

Legacy oracles (prime_check_v1) serve the 1-3 integer format; structured
oracles (max_min_v1) serve a teacher-approved input schema of the shape
they were verified for, and validate every input against that schema
before computing anything.

Clients can never supply expected outputs. dry_run=True returns the
cases to the owning teacher without writing anything.
"""

from generic_test_inputs import parse_approved_value_constraints
from secure_test_case_save import save_generated_test_cases_secure
from test_case_generation_service import validate_generation_preflight
from test_case_builder import build_structured_test_cases, build_test_cases   # no execution imports
from test_case_validator import validate_generated_test_cases
from verified_oracles import OracleError, get_verified_oracle


def oracle_runner(oracle):
    def run_batch(stdin_values):
        return [oracle.expected_output(stdin.strip()) for stdin in stdin_values]
    return run_batch


def structured_oracle_runner(oracle, input_schema):
    """Exact stored input text -> oracle (schema-validated inside the oracle)."""
    def run_batch(stdin_values):
        return [oracle.expected_output(input_schema, stdin) for stdin in stdin_values]
    return run_batch


def generate_from_verified_oracle(
    assessment_id,
    teacher_id,
    oracle_id,
    additional_inputs=None,
    dry_run=True,
    preflight_fn=validate_generation_preflight,
    save_fn=save_generated_test_cases_secure,
):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be true or false")

    oracle = get_verified_oracle(oracle_id)
    preflight = preflight_fn(assessment_id, teacher_id.strip())

    if preflight["source_hash"] != oracle.reference_sha256:
        raise OracleError(
            "This oracle is not verified for the assessment's approved "
            "reference solution")

    constraints = preflight["constraints"]
    input_schema = preflight.get("input_schema")

    if input_schema is not None:
        if not oracle.structured:
            raise OracleError(
                "This oracle supports only the legacy integer input format; "
                "this assessment uses a structured input schema")
        oracle.check_schema(input_schema)
        test_cases = build_structured_test_cases(
            input_schema, structured_oracle_runner(oracle, input_schema),
            additional_inputs)
        validate_generated_test_cases(constraints, test_cases,
                                      input_schema=input_schema)
    else:
        if oracle.structured:
            raise OracleError(
                "This oracle requires a teacher-approved structured input schema")
        oracle.check_domain(parse_approved_value_constraints(constraints))
        test_cases = build_test_cases(
            constraints, oracle_runner(oracle),
            additional_inputs=additional_inputs)
        validate_generated_test_cases(constraints, test_cases)

    summary = {
        "assessment_id": assessment_id,
        "reference_solution_id": preflight["reference_solution_id"],
        "source_hash": preflight["source_hash"],
        "execution_mode": "verified_python_oracle",
        "oracle_id": oracle.oracle_id,
        "input_format": ("approved_input_schema" if input_schema is not None
                         else "legacy_integers"),
        "generated_count": len(test_cases),
        "dry_run": dry_run,
    }

    if dry_run:
        return {**summary, "saved_count": 0, "test_cases": test_cases}

    saved = save_fn(
        assessment_id=assessment_id,
        teacher_id=teacher_id.strip(),
        reference_solution_id=preflight["reference_solution_id"],
        expected_source_hash=preflight["source_hash"],
        test_cases=test_cases,
        **({"structured": True} if input_schema is not None else {}))

    return {**summary,
            "saved_count": saved["saved_count"],
            "marks_per_test": saved["marks_per_test"],
            "test_marks_allocated": saved["test_marks_allocated"],
            **{k: saved[k] for k in ("validation_percentage", "rubric_marks_allocated",
                                     "weighting") if k in saved}}
