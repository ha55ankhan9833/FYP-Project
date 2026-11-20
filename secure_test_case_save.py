import hashlib
from decimal import Decimal

from db import get_connection
from assessment_rubric_service import (
    RubricNotApprovedError, rubric_ready_for_generation)
from marks_allocation import compute_allocation
from test_case_approval_validator import validate_assessment_approval
from test_case_validator import structured_input_key, validate_generated_test_cases


from test_case_policy import (  # noqa: E402
    REQUIRED_TEST_CASE_COUNT as MAX_TEST_CASES,
    enforce_test_case_count
)

MAX_INPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 64 * 1024     # matches test_case_pipeline limit


SPEC_FIELDS_SQL = """
SELECT problem_statement, input_format, output_format
FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_id = ?
"""


def _recheck_verified_spec(cursor, assessment_id, spec_id, constraints, approved_schema,
                           test_cases):
    """Under the save lock: the assessment must still match exactly the same
    reviewed spec, and every stored output must equal the spec's oracle."""
    from verified_problem_specs import safe_match

    cursor.execute(SPEC_FIELDS_SQL, assessment_id)
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Assessment not found")
    spec, _ = safe_match(row[0], row[1], row[2], constraints, approved_schema)
    if spec is None or spec["spec_id"] != spec_id:
        raise ValueError("Verified problem specification no longer matches")
    oracle = spec["oracle"]
    for case in test_cases:
        expected = (oracle.expected_output(approved_schema, case["input_data"])
                    if approved_schema is not None
                    else oracle.expected_output(case["input_data"].strip()))
        if expected != case["expected_output"]:
            raise ValueError("Expected output does not match the verified oracle")


def save_generated_test_cases_secure(
    assessment_id,
    teacher_id,
    reference_solution_id,
    expected_source_hash,
    test_cases,
    structured=False,
    verified_spec_id=None
):
    """
    structured=False (default): legacy 1-3 integer inputs, unchanged rules.
    structured=True: inputs were built from the teacher-approved input schema;
    the exact text (spaces/newlines) is kept and validated against the schema
    re-read under lock. A mismatch between `structured` and the approved draft
    (schema added/removed) is refused.

    Output source (exactly one):
      reference binding   reference_solution_id + expected_source_hash: the
                          APPROVED reference must be intact and unchanged.
      problem-spec binding verified_spec_id (reference args None): the
                          assessment's full specification is re-matched under
                          lock against verified_problem_specs.json and must
                          still match exactly that spec.
    """
    if type(structured) is not bool:
        raise ValueError("structured must be true or false")

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    if verified_spec_id is not None:
        if not isinstance(verified_spec_id, str) or not verified_spec_id.strip():
            raise ValueError("Invalid verified spec ID")
        if reference_solution_id is not None or expected_source_hash is not None:
            raise ValueError("Use either a reference binding or a verified spec, not both")
    else:
        if type(reference_solution_id) is not int or reference_solution_id <= 0:
            raise ValueError("Invalid reference solution ID")

        if not isinstance(expected_source_hash, str) or len(expected_source_hash) != 64:
            raise ValueError("Invalid expected source hash")

    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("Test cases are required")

    enforce_test_case_count(len(test_cases), "A saved test set")

    for case in test_cases:
        if isinstance(case, dict):
            if len(str(case.get("input_data", ""))) > MAX_INPUT_CHARS:
                raise ValueError("Test input exceeds size limit")
            if len(str(case.get("expected_output", ""))) > MAX_OUTPUT_CHARS:
                raise ValueError("Expected output exceeds size limit")
            if len(str(case.get("test_name", ""))) > 200:
                raise ValueError("Test name too long")
            if len(str(case.get("category", ""))) > 50:
                raise ValueError("Test category too long")

    required = {
        "test_number", "test_name", "category",
        "input_data", "expected_output"
    }

    numbers = set()
    inputs = set()

    for case in test_cases:
        if not isinstance(case, dict) or not required.issubset(case):
            raise ValueError("Invalid test case structure")

        number = case["test_number"]

        if type(number) is not int or number <= 0 or number in numbers:
            raise ValueError("Invalid or duplicate test number")

        numbers.add(number)

        for field in ("test_name", "category", "input_data", "expected_output"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"Invalid {field}")

        if structured:
            normalized_input = structured_input_key(case["input_data"])
        else:
            normalized_input = " ".join(case["input_data"].split())

        if normalized_input in inputs:
            raise ValueError("Duplicate test input")

        inputs.add(normalized_input)

    if numbers != set(range(1, len(test_cases) + 1)):
        raise ValueError("Test numbers must start at 1 and be consecutive")

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT total_marks, value_constraints, code_constraints,
                   validation_percentage
            FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ? AND teacher_id = ?
        """, assessment_id, teacher_id)

        assessment = cursor.fetchone()

        if assessment is None:
            raise ValueError("Assessment not found or access denied")

        total_marks = Decimal(str(assessment[0]))
        constraints = str(assessment[1] or "").strip()
        code_constraints = str(assessment[2] or "").strip()
        validation_percentage = assessment[3]

        approval = validate_assessment_approval(cursor, assessment_id)

        if not approval["valid"]:
            raise ValueError("Constraints not approved")

        if approval["approved_value_constraints"] != constraints:
            raise ValueError("Approved constraints changed")

        if approval["approved_code_constraints"] != code_constraints:
            raise ValueError("Approved code constraints changed")

        approved_schema = approval.get("approved_input_schema")

        if verified_spec_id is None:
            cursor.execute("""
                SELECT source_code, source_hash, language, approval_status
                FROM dbo.ReferenceSolution WITH (UPDLOCK, HOLDLOCK)
                WHERE assessment_id = ? AND reference_solution_id = ?
            """, assessment_id, reference_solution_id)

            reference = cursor.fetchone()

            if reference is None:
                raise ValueError("Reference solution not found")

            source_code, stored_hash, language, status = reference

            if status != "APPROVED" or language != "cpp":
                raise ValueError("Approved C++ reference required")

            actual_hash = hashlib.sha256(
                source_code.encode("utf-8")
            ).hexdigest()

            # Expected outputs were produced from exactly this approved,
            # hash-verified reference of THIS assessment (no fixed IDs).
            if actual_hash != stored_hash or actual_hash != expected_source_hash:
                raise ValueError("Reference source hash mismatch")
        else:
            _recheck_verified_spec(cursor, assessment_id, verified_spec_id,
                                   constraints, approved_schema, test_cases)

        if structured != (approved_schema is not None):
            raise ValueError("Approved input schema changed")

        validate_generated_test_cases(constraints, test_cases,
                                      input_schema=approved_schema)

        if not total_marks.is_finite() or total_marks <= 0:
            raise ValueError("Invalid total marks")

        # Equal weight: every row stores exactly test_marks / 10. The share
        # must be exact at the column's 2 decimal places; otherwise the save
        # is refused (MarksAllocationError) rather than rounded, so the 10
        # rows always sum to the allocation. Grading never reads this column;
        # it uses (passed / total) x test allocation (grading_allocation.py).
        allocation = compute_allocation(total_marks, validation_percentage,
                                        len(test_cases))
        test_marks_allocated = Decimal(allocation["test_marks"])
        marks_per_test = Decimal(allocation["per_case_marks"])
        if marks_per_test * len(test_cases) != test_marks_allocated:
            raise ValueError("Equal test-case marks do not sum to the allocation")

        cursor.execute("""
            SELECT COUNT(*)
            FROM dbo.TestCase WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        if int(cursor.fetchone()[0]) != 0:
            raise ValueError("Assessment already has test cases")

        # The approved rubric fixes the allocation the cases are saved with.
        if not rubric_ready_for_generation(cursor, assessment_id, total_marks,
                                           validation_percentage):
            raise RubricNotApprovedError(
                "Approve the rubric (criteria summing exactly to "
                f"{allocation['rubric_marks']} rubric marks) before saving test cases")

        for case in test_cases:
            marks = marks_per_test

            cursor.execute("""
                INSERT INTO dbo.TestCase (
                    assessment_id, test_number, test_name,
                    category, visibility, input_data,
                    expected_output, marks
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                assessment_id,
                case["test_number"],
                case["test_name"],
                case["category"],
                "hidden",
                case["input_data"],
                case["expected_output"],
                marks
            )

        conn.commit()

        return {
            "success": True,
            "assessment_id": assessment_id,
            "reference_solution_id": reference_solution_id,
            "output_source": ("VERIFIED_PROBLEM_SPEC" if verified_spec_id
                              else "APPROVED_REFERENCE"),
            **({"spec_id": verified_spec_id} if verified_spec_id else {}),
            "saved_count": len(test_cases),
            "total_marks": str(total_marks),
            "validation_percentage": allocation["validation_percentage"],
            "test_marks_allocated": str(test_marks_allocated),
            "rubric_marks_allocated": allocation["rubric_marks"],
            "marks_per_test": str(marks_per_test),
            "weighting": "EQUAL"
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
