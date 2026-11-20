"""
Read-only readiness checklist for one teacher-owned assessment: what is
still missing before test cases can be generated, and before the
assessment can be published (assigned to sections).

Nothing is written, compiled or executed. Every check reuses the rule the
real endpoint enforces:
  constraints   test_case_approval_validator.validate_assessment_approval
  input format  approved structured schema, or the legacy integer grammar
  reference     ReferenceSolution APPROVED, language cpp, intact SHA-256
  outputs       sandbox (secure_execution_gate) / owner-approved host
                allowlist (trusted_reference_hashes.json) / reviewed oracle
                bound to the reference hash (verified_oracles), exactly as
                the generation endpoints decide
  test cases    test_case_policy.REQUIRED_TEST_CASE_COUNT
  rubric        assessment_rubric_service.check_rubric_ready (approved rubric is
                required before generation as well as before publishing)
  marks         marks_allocation.compute_allocation (exact equal 2-decimal
                share for each of the 10 cases)
"""

import hashlib

from db import get_connection

ASSESSMENT_SQL = """
SELECT assessment_id, course_code, total_marks, validation_percentage
FROM dbo.Assessment
WHERE assessment_id = ? AND teacher_id = ?;
"""

REFERENCE_SQL = """
SELECT reference_solution_id, source_code, source_hash, language, approval_status
FROM dbo.ReferenceSolution
WHERE assessment_id = ?;
"""

TEST_COUNT_SQL = "SELECT COUNT(*) FROM dbo.TestCase WHERE assessment_id = ?;"
SECTION_COUNT_SQL = "SELECT COUNT(*) FROM dbo.AssessmentSection WHERE assessment_id = ?;"


class AssessmentNotFound(ValueError):
    pass


def _output_source(source_hash, input_schema, sandbox_available, trusted_hashes, oracles):
    if sandbox_available:
        return {"source": "SECURE_SANDBOX", "endpoint": "generate-test-cases"}
    if source_hash in trusted_hashes:
        return {"source": "TRUSTED_HOST_ALLOWLIST", "endpoint": "generate-test-cases"}
    for oracle in oracles.values():
        if oracle.reference_sha256 != source_hash:
            continue
        if getattr(oracle, "structured", False):
            if input_schema is None:
                continue
            try:
                oracle.check_schema(input_schema)
            except ValueError:
                continue                     # verified for a different shape/domain
        elif input_schema is not None:
            continue
        return {"source": "VERIFIED_ORACLE", "endpoint": "generate-test-cases/oracle",
                "oracle_id": oracle.oracle_id}
    return None


def get_generation_readiness(assessment_id, teacher_id, connection_factory=get_connection,
                             sandbox_available=None, trusted_hashes=None, oracles=None):
    from assessment_rubric_service import check_rubric_ready
    from marks_allocation import MarksAllocationError, compute_allocation
    from generic_test_inputs import parse_approved_value_constraints
    from test_case_approval_validator import validate_assessment_approval
    from test_case_policy import REQUIRED_TEST_CASE_COUNT

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    if sandbox_available is None:
        from secure_execution_gate import get_secure_sandbox
        sandbox_available = get_secure_sandbox() is not None
    if trusted_hashes is None:
        from test_case_generation_service import load_trusted_reference_hashes
        trusted_hashes = load_trusted_reference_hashes()
    if oracles is None:
        from verified_oracles import VERIFIED_ORACLES
        oracles = VERIFIED_ORACLES

    missing = []
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(ASSESSMENT_SQL, (assessment_id, teacher_id.strip()))
        row = cursor.fetchone()
        if row is None:
            raise AssessmentNotFound("Assessment not found")
        total_marks, validation_percentage = row[2], row[3]

        # Constraints + input format
        approval, input_schema, input_format = None, None, None
        try:
            approval = validate_assessment_approval(cursor, assessment_id)
            input_schema = approval.get("approved_input_schema")
        except ValueError as error:
            missing.append({"code": "CONSTRAINTS_NOT_APPROVED", "detail": str(error)})

        if approval is not None:
            if input_schema is not None:
                input_format = "APPROVED_INPUT_SCHEMA"
            else:
                try:
                    parse_approved_value_constraints(approval["approved_value_constraints"])
                    input_format = "LEGACY_INTEGERS"
                except ValueError as error:
                    missing.append({"code": "INPUT_SCHEMA_REQUIRED", "detail": str(error)})

        # Reference solution
        cursor.execute(REFERENCE_SQL, (assessment_id,))
        ref = cursor.fetchone()
        reference = {"status": "MISSING"}
        source_hash = None
        if ref is None:
            missing.append({"code": "REFERENCE_SOLUTION_MISSING"})
        else:
            intact = hashlib.sha256((ref[1] or "").encode("utf-8")).hexdigest() == ref[2]
            reference = {"reference_solution_id": int(ref[0]), "status": ref[4],
                         "source_hash": ref[2], "language": ref[3], "hash_intact": intact}
            if ref[4] != "APPROVED":
                missing.append({"code": "REFERENCE_SOLUTION_NOT_APPROVED"})
            elif ref[3] != "cpp" or not intact:
                missing.append({"code": "REFERENCE_SOLUTION_INVALID"})
            else:
                source_hash = ref[2]

        # Expected-output source (decided exactly as the endpoints do)
        output_source = None
        if source_hash is not None:
            output_source = _output_source(source_hash, input_schema, sandbox_available,
                                           trusted_hashes, oracles)
            if output_source is None:
                missing.append({
                    "code": "EXPECTED_OUTPUT_SOURCE_UNAVAILABLE",
                    "detail": ("No secure sandbox, no owner-approved host allowlist entry "
                               "and no verified oracle for this reference hash")})

        cursor.execute(TEST_COUNT_SQL, (assessment_id,))
        test_count = int(cursor.fetchone()[0])
        if test_count:
            missing.append({"code": "TEST_CASES_ALREADY_EXIST"})

        allocation = None
        try:
            allocation = compute_allocation(total_marks, validation_percentage)
        except MarksAllocationError as error:
            missing.append({"code": "MARKS_ALLOCATION_INVALID", "detail": error.message,
                            "reason": error.code, **error.details})

        rubric_ready = True
        try:
            check_rubric_ready(cursor, assessment_id, total_marks, validation_percentage)
        except ValueError:
            rubric_ready = False
        if not rubric_ready:
            missing.append({"code": "RUBRIC_NOT_APPROVED",
                            "detail": "Approve rubric criteria summing exactly to the "
                                      "rubric allocation before generating"})

        cursor.execute(SECTION_COUNT_SQL, (assessment_id,))
        section_count = int(cursor.fetchone()[0])
    finally:
        conn.rollback()
        conn.close()

    publish_missing = []
    if test_count != REQUIRED_TEST_CASE_COUNT:
        publish_missing.append({"code": "TEST_CASES_NOT_GENERATED",
                                "detail": f"{test_count} of {REQUIRED_TEST_CASE_COUNT}"})
    if not rubric_ready:
        publish_missing.append({"code": "RUBRIC_INCOMPLETE"})

    return {
        "assessment_id": assessment_id,
        "ready_to_generate": not missing,
        "missing_for_generation": missing,
        "ready_to_publish": not publish_missing,
        "missing_for_publish": publish_missing,
        "checks": {
            "constraints_approved": approval is not None,
            "input_format": input_format,
            "reference_solution": reference,
            "expected_output_source": output_source,
            "test_case_count": test_count,
            "required_test_case_count": REQUIRED_TEST_CASE_COUNT,
            "rubric_ready": rubric_ready,
            "marks_allocation": allocation,
            "assigned_section_count": section_count,
        },
    }
