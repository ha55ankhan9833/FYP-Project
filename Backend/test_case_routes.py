from flask import Blueprint, jsonify
from flask_jwt_extended import (
    jwt_required,
    get_jwt,
    get_jwt_identity
)

from db import get_connection


test_case_bp = Blueprint(
    "test_case_bp",
    __name__
)


def _generation_body_error(body, allowed):
    """Shared request validation for both generation endpoints."""
    from test_case_policy import REQUIRED_TEST_CASE_COUNT

    if "random_count" in body:
        return (f"random_count is no longer accepted: every assessment has "
                f"exactly {REQUIRED_TEST_CASE_COUNT} test cases; random cases "
                f"fill automatically after boundary and additional inputs")

    unknown = set(body) - allowed
    if unknown:
        return "Unsupported request fields: " + ", ".join(sorted(unknown))

    extra = body.get("additional_inputs")
    if extra is not None and (
        not isinstance(extra, list)
        or len(extra) > REQUIRED_TEST_CASE_COUNT
        or not all(isinstance(item, str) for item in extra)
    ):
        return (f"additional_inputs must be a list of at most "
                f"{REQUIRED_TEST_CASE_COUNT} strings")

    return None


def _marks_or_rubric_error(error):
    """Coded responses for the equal-mark and rubric-approval save guards."""
    from assessment_rubric_service import RubricNotApprovedError
    from marks_allocation import MarksAllocationError

    if isinstance(error, MarksAllocationError):
        return jsonify({"status": "error", "code": "MARKS_ALLOCATION_INVALID",
                        "message": error.message,
                        "details": {"reason": error.code, **error.details}}), 422
    if isinstance(error, RubricNotApprovedError):
        return jsonify({"status": "error", "code": "RUBRIC_NOT_APPROVED",
                        "message": str(error)}), 409
    return None


@test_case_bp.route(
    "/api/assessments/<int:assessment_id>/test-cases",
    methods=["GET"]
)
@jwt_required()
def get_assessment_test_cases(assessment_id):

    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    teacher_id = get_jwt_identity()
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT assessment_id, total_marks
            FROM dbo.Assessment
            WHERE assessment_id = ?
              AND teacher_id = ?
        """, assessment_id, teacher_id)

        assessment = cursor.fetchone()

        if assessment is None:
            return jsonify({
                "status": "error",
                "message": "Assessment not found"
            }), 404

        cursor.execute("""
            SELECT
                test_case_id,
                assessment_id,
                test_number,
                test_name,
                category,
                visibility,
                input_data,
                expected_output,
                marks
            FROM dbo.TestCase
            WHERE assessment_id = ?
            ORDER BY test_number
        """, assessment_id)

        columns = [
            column[0] for column in cursor.description
        ]

        test_cases = []

        for row in cursor.fetchall():
            item = dict(zip(columns, row))

            if item["marks"] is not None:
                item["marks"] = float(item["marks"])

            test_cases.append(item)

        return jsonify({
            "status": "success",
            "assessment_id": assessment_id,
            "total": len(test_cases),
            "test_cases": test_cases
        }), 200

    except Exception:
        from flask import current_app
        current_app.logger.exception(
            "Test Case Retrieval Error"
        )

        return jsonify({
            "status": "error",
            "message": "Unable to fetch test cases"
        }), 500

    finally:
        if conn is not None:
            conn.close()


@test_case_bp.route(
    "/api/assessments/<int:assessment_id>/generation-readiness",
    methods=["GET"]
)
@jwt_required()
def get_generation_readiness_route(assessment_id):
    """Read-only checklist: what is missing before generate / publish."""
    from flask import current_app
    from api_errors import error_response
    from assessment_readiness_service import AssessmentNotFound, get_generation_readiness

    if get_jwt().get("role") != "teacher":
        return error_response("Teacher access required", 403, "TEACHER_ONLY")
    teacher_id = get_jwt_identity()
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        return error_response("Invalid teacher identity", 403, "TEACHER_ONLY")

    try:
        data = get_generation_readiness(assessment_id, teacher_id.strip())
        return jsonify({"status": "success", "data": data}), 200
    except AssessmentNotFound:
        return error_response("Assessment not found", 404, "ASSESSMENT_NOT_FOUND")
    except ValueError as error:
        return error_response(str(error), 400, "INVALID_REQUEST")
    except Exception:
        current_app.logger.exception("Generation readiness check failed")
        return error_response("Unable to check readiness right now", 500,
                              "READINESS_UNAVAILABLE")


@test_case_bp.route(
    "/api/assessments/<int:assessment_id>/generate-test-cases",
    methods=["POST"]
)
@jwt_required()
def generate_assessment_test_cases(assessment_id):
    from flask import request, current_app
    from test_case_builder import StructuredInputError
    from test_case_generation_orchestrator import (
        ExpectedOutputsUnavailable,
        generate_and_save_test_cases
    )

    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    teacher_id = get_jwt_identity()

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        return jsonify({
            "status": "error",
            "message": "Invalid teacher identity"
        }), 403

    if not request.is_json:
        return jsonify({
            "status": "error",
            "message": "JSON request body required"
        }), 415

    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return jsonify({
            "status": "error",
            "message": "Invalid JSON object"
        }), 400

    body_error = _generation_body_error(body, {"additional_inputs"})

    if body_error:
        return jsonify({"status": "error", "message": body_error}), 400

    try:
        result = generate_and_save_test_cases(
            assessment_id=assessment_id,
            teacher_id=teacher_id.strip(),
            additional_inputs=body.get("additional_inputs")
        )

        return jsonify({
            "status": "success",
            "message": "Test cases generated successfully",
            "data": result
        }), 201

    except ExpectedOutputsUnavailable as error:
        # Structured inputs are valid but no approved mechanism can verify
        # expected outputs. Nothing was executed or saved.
        return jsonify({
            "status": "error",
            "code": "EXPECTED_OUTPUT_UNVERIFIABLE",
            "message": ("Inputs are valid, but expected outputs cannot be verified yet; "
                        "the test cases were not finalized or saved"),
            "finalization_status": "NOT_FINALIZED",
            "reason": error.reason,
            "ways_to_finalize": [
                "a verified secure sandbox (secure execution gate)",
                "owner-approved trusted-reference allowlist entry for this reference hash"
            ],
            "validated_inputs": [
                {"test_number": i, "test_name": item["test_name"],
                 "category": item["category"], "input_data": item["input_data"]}
                for i, item in enumerate(error.inputs, start=1)
            ]
        }), 409

    except StructuredInputError as error:
        return jsonify({
            "status": "error",
            "code": "INVALID_TEST_INPUT",
            "message": str(error),
            "errors": error.details
        }), 400

    except ValueError as error:
        coded = _marks_or_rubric_error(error)
        if coded is not None:
            return coded

        message = str(error)

        if "access denied" in message or "Assessment not found" in message:
            status_code = 404
            public_message = "Assessment not found"
        elif "already has test cases" in message:
            status_code = 409
            public_message = "Assessment already has test cases"
        else:
            status_code = 400
            public_message = message

        return jsonify({
            "status": "error",
            "message": public_message
        }), status_code

    except Exception:
        current_app.logger.exception(
            "Test Case Generation API Error"
        )

        return jsonify({
            "status": "error",
            "message": "Unable to generate test cases"
        }), 500


@test_case_bp.route(
    "/api/assessments/<int:assessment_id>/generate-test-cases/oracle",
    methods=["POST"]
)
@jwt_required()
def generate_test_cases_from_oracle(assessment_id):
    """
    Teacher-only. Expected outputs are computed server-side by a reviewed
    oracle bound to the assessment's APPROVED reference hash. The body can
    only NAME an oracle; it cannot carry expected outputs.
    dry_run defaults to true (preview, no database write).
    """
    from flask import request, current_app
    from oracle_test_case_service import generate_from_verified_oracle
    from test_case_builder import StructuredInputError
    from verified_oracles import OracleError

    if get_jwt().get("role") != "teacher":
        return jsonify({"status": "error",
                        "message": "Teacher access required"}), 403

    teacher_id = get_jwt_identity()
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        return jsonify({"status": "error",
                        "message": "Invalid teacher identity"}), 403

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"status": "error",
                        "message": "Invalid JSON object"}), 400

    if "oracle_id" not in body:
        return jsonify({"status": "error",
                        "message": "oracle_id is required"}), 400

    body_error = _generation_body_error(
        body, {"oracle_id", "additional_inputs", "dry_run"})
    if body_error:
        return jsonify({"status": "error", "message": body_error}), 400

    try:
        result = generate_from_verified_oracle(
            assessment_id=assessment_id,
            teacher_id=teacher_id.strip(),
            oracle_id=body["oracle_id"],
            additional_inputs=body.get("additional_inputs"),
            dry_run=body.get("dry_run", True)
        )
        return jsonify({
            "status": "success",
            "message": ("Preview only; nothing saved" if result["dry_run"]
                        else "Test cases saved"),
            "data": result
        }), 200 if result["dry_run"] else 201

    except StructuredInputError as error:
        return jsonify({
            "status": "error",
            "code": "INVALID_TEST_INPUT",
            "message": str(error),
            "errors": error.details
        }), 400

    except (OracleError, ValueError) as error:
        coded = _marks_or_rubric_error(error)
        if coded is not None:
            return coded
        message = str(error)
        if "access denied" in message or "Assessment not found" in message:
            return jsonify({"status": "error",
                            "message": "Assessment not found"}), 404
        if "already has test cases" in message:
            return jsonify({"status": "error",
                            "message": "Assessment already has test cases"}), 409
        return jsonify({"status": "error", "message": message}), 400

    except Exception:
        current_app.logger.exception("Oracle Test Case Generation Error")
        return jsonify({"status": "error",
                        "message": "Unable to generate test cases"}), 500


def _verification_required(message, details=None, readiness=None):
    body = {"status": "error", "code": "VERIFICATION_REQUIRED", "message": message,
            "generated_count": 0, "saved_count": 0}
    if details:
        body["details"] = details
    if readiness is not None:
        body["missing_for_generation"] = readiness.get("missing_for_generation", [])
        body["verification"] = readiness.get("verification")
    return jsonify(body), 409


@test_case_bp.route(
    "/api/assessments/<int:assessment_id>/generate-test-cases/verified",
    methods=["POST"]
)
@jwt_required()
def generate_verified_test_cases(assessment_id):
    """
    Teacher-only, the ONE generation call for the normal workflow. No
    reference solution is needed when the assessment matches an
    owner-reviewed problem spec. The server picks the verified output source
    (reviewed problem spec -> reference-bound oracle -> sandbox / host
    allowlist); with none it returns 409 VERIFICATION_REQUIRED and generates
    nothing. The body can never carry expected outputs or choose a source.
    dry_run defaults to true (preview, nothing saved).
    """
    from flask import request, current_app
    from assessment_readiness_service import AssessmentNotFound, get_generation_readiness
    from oracle_test_case_service import generate_from_verified_oracle
    from reference_free_generation import generate_reference_free
    from test_case_builder import StructuredInputError
    from test_case_generation_orchestrator import (
        ExpectedOutputsUnavailable, generate_and_save_test_cases)
    from verified_oracles import OracleError
    from verified_problem_specs import TEACHER_MESSAGE, VerificationRequired

    if get_jwt().get("role") != "teacher":
        return jsonify({"status": "error", "code": "TEACHER_ONLY",
                        "message": "Teacher access required"}), 403
    teacher_id = get_jwt_identity()
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        return jsonify({"status": "error", "code": "TEACHER_ONLY",
                        "message": "Invalid teacher identity"}), 403
    teacher_id = teacher_id.strip()

    body = request.get_json(silent=True)
    if body is None and not request.get_data():
        body = {}
    if not isinstance(body, dict):
        return jsonify({"status": "error", "code": "INVALID_REQUEST",
                        "message": "Invalid JSON object"}), 400
    body_error = _generation_body_error(body, {"additional_inputs", "dry_run"})
    if body_error:
        return jsonify({"status": "error", "code": "INVALID_REQUEST",
                        "message": body_error}), 400
    dry_run = body.get("dry_run", True)
    if type(dry_run) is not bool:
        return jsonify({"status": "error", "code": "INVALID_REQUEST",
                        "message": "dry_run must be true or false"}), 400
    extra = body.get("additional_inputs")

    try:
        readiness = get_generation_readiness(assessment_id, teacher_id)
        source = readiness["checks"]["expected_output_source"]
        if source is None:
            return _verification_required(TEACHER_MESSAGE, readiness=readiness)

        if source["source"] == "VERIFIED_PROBLEM_SPEC":
            result = generate_reference_free(assessment_id, teacher_id, extra, dry_run)
        elif source["source"] == "VERIFIED_ORACLE":
            result = generate_from_verified_oracle(
                assessment_id=assessment_id, teacher_id=teacher_id,
                oracle_id=source["oracle_id"], additional_inputs=extra, dry_run=dry_run)
            result = {**result, "output_source": "VERIFIED_ORACLE",
                      "reference_required": True}
        else:
            if dry_run:
                return jsonify({"status": "error", "code": "PREVIEW_UNAVAILABLE",
                                "message": ("Preview is not available for this verified "
                                            "source; send dry_run: false to generate and "
                                            "save")}), 409
            result = generate_and_save_test_cases(
                assessment_id=assessment_id, teacher_id=teacher_id, additional_inputs=extra)
            result = {**result, "output_source": source["source"],
                      "reference_required": True, "dry_run": False}

        return jsonify({
            "status": "success",
            "message": ("Preview only; nothing saved" if result.get("dry_run")
                        else "Test cases saved"),
            "data": result
        }), 200 if result.get("dry_run") else 201

    except AssessmentNotFound:
        return jsonify({"status": "error", "code": "ASSESSMENT_NOT_FOUND",
                        "message": "Assessment not found"}), 404
    except VerificationRequired as error:
        return _verification_required(error.message, error.details)
    except ExpectedOutputsUnavailable:
        return _verification_required(TEACHER_MESSAGE)
    except StructuredInputError as error:
        return jsonify({"status": "error", "code": "INVALID_TEST_INPUT",
                        "message": str(error), "errors": error.details}), 400
    except (OracleError, ValueError) as error:
        coded = _marks_or_rubric_error(error)
        if coded is not None:
            return coded
        message = str(error)
        if "access denied" in message or "Assessment not found" in message:
            return jsonify({"status": "error", "code": "ASSESSMENT_NOT_FOUND",
                            "message": "Assessment not found"}), 404
        if "already has test cases" in message:
            return jsonify({"status": "error", "code": "TEST_CASES_ALREADY_EXIST",
                            "message": "Assessment already has test cases"}), 409
        return jsonify({"status": "error", "code": "GENERATION_REFUSED",
                        "message": message}), 400
    except Exception:
        current_app.logger.exception("Verified Test Case Generation Error")
        return jsonify({"status": "error", "code": "GENERATION_FAILED",
                        "message": "Unable to generate test cases"}), 500
