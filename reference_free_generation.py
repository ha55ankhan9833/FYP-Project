"""
Reference-free test-case generation (no teacher C++ upload, no C++ execution).

    preflight (owner, approved constraints matching the assessment, approved
    schema, no existing tests)  ->  the assessment's complete specification
    must match an owner-reviewed entry of verified_problem_specs.json  ->
    that spec's verified oracle computes every output with two independent
    algorithms  ->  constraint/schema validation  ->  atomic secure save,
    which re-checks the spec match, rubric approval and equal marks under
    lock.

When no reviewed spec matches, VerificationRequired is raised and nothing is
generated: no fallback outputs are ever produced.
"""

from db import get_connection
from oracle_test_case_service import build_oracle_test_cases
from secure_test_case_save import save_generated_test_cases_secure
from test_case_approval_validator import validate_assessment_approval
from verified_problem_specs import TEACHER_MESSAGE, VerificationRequired, safe_match

PREFLIGHT_SQL = """
SELECT value_constraints, code_constraints, problem_statement, input_format, output_format
FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_id = ? AND teacher_id = ?;
"""

COUNT_TESTS_SQL = """
SELECT COUNT(*) FROM dbo.TestCase WITH (UPDLOCK, HOLDLOCK) WHERE assessment_id = ?;
"""


def reference_free_preflight(assessment_id, teacher_id, connection_factory=get_connection,
                             specs=None):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    conn = connection_factory()
    try:
        conn.autocommit = False
        cursor = conn.cursor()
        cursor.execute(PREFLIGHT_SQL, (assessment_id, teacher_id.strip()))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Assessment not found or access denied")
        constraints = str(row[0] or "").strip()
        code_constraints = str(row[1] or "").strip()

        approval = validate_assessment_approval(cursor, assessment_id)
        if not approval["valid"]:
            raise ValueError("Constraints not approved")
        if approval["approved_value_constraints"] != constraints:
            raise ValueError("Approved constraints do not match assessment")
        if approval["approved_code_constraints"] != code_constraints:
            raise ValueError("Approved code constraints do not match assessment")
        input_schema = approval.get("approved_input_schema")

        cursor.execute(COUNT_TESTS_SQL, (assessment_id,))
        if int(cursor.fetchone()[0]):
            raise ValueError("Assessment already has test cases")

        spec, details = safe_match(row[2], row[3], row[4], constraints, input_schema,
                                   **({"specs": specs} if specs is not None else {}))
        if spec is None:
            raise VerificationRequired(TEACHER_MESSAGE, details)

        return {"assessment_id": assessment_id, "constraints": constraints,
                "code_constraints": code_constraints, "input_schema": input_schema,
                "spec": spec}
    finally:
        conn.rollback()
        conn.close()


def generate_reference_free(assessment_id, teacher_id, additional_inputs=None, dry_run=True,
                            preflight_fn=reference_free_preflight,
                            save_fn=save_generated_test_cases_secure):
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be true or false")

    preflight = preflight_fn(assessment_id, teacher_id)
    spec = preflight["spec"]
    oracle = spec["oracle"]
    input_schema = preflight["input_schema"]
    test_cases = build_oracle_test_cases(oracle, preflight["constraints"], input_schema,
                                         additional_inputs)

    summary = {
        "assessment_id": assessment_id,
        "execution_mode": "verified_python_oracle",
        "output_source": "VERIFIED_PROBLEM_SPEC",
        "reference_required": False,
        "reference_solution_id": None,
        "oracle_id": oracle.oracle_id,
        "spec_id": spec["spec_id"],
        "input_format": ("approved_input_schema" if input_schema is not None
                         else "legacy_integers"),
        "generated_count": len(test_cases),
        "dry_run": dry_run,
    }
    if dry_run:
        return {**summary, "saved_count": 0, "test_cases": test_cases}

    saved = save_fn(assessment_id=assessment_id, teacher_id=teacher_id.strip(),
                    reference_solution_id=None, expected_source_hash=None,
                    test_cases=test_cases, structured=input_schema is not None,
                    verified_spec_id=spec["spec_id"])
    return {**summary, "saved_count": saved["saved_count"],
            **{k: saved[k] for k in ("marks_per_test", "test_marks_allocated",
                                     "validation_percentage", "rubric_marks_allocated",
                                     "weighting") if k in saved}}
