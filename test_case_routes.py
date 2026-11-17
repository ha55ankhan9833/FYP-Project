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
    "/api/assessments/<int:assessment_id>/generate-test-cases",
    methods=["POST"]
)
@jwt_required()
def generate_assessment_test_cases(assessment_id):
    from flask import request, current_app
    from test_case_generation_orchestrator import (
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

    except ValueError as error:
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

    except (OracleError, ValueError) as error:
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
