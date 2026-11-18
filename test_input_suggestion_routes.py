"""
Teacher-only test-INPUT suggestion and validation endpoints.

    POST /api/test-inputs/suggestions                       ad-hoc problem
    POST /api/assessments/<id>/test-inputs/suggestions      teacher's own assessment
    POST /api/test-inputs/validate                          validate teacher inputs

Nothing is persisted, nothing is executed, no expected outputs are produced.
The only database access is one read-only SELECT of the teacher's own
assessment (ownership check). Saving test cases still goes through the
existing generate endpoints, approval rules and the exactly-10 rule.
"""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from api_errors import error_response
from db import get_connection

test_input_bp = Blueprint("test_input_bp", __name__)

PROBLEM_FIELDS = ("problem_statement", "input_format", "output_format",
                  "value_constraints", "sample_input")
FIELD_LIMIT = 10000


def _teacher():
    if get_jwt().get("role") != "teacher":
        return None, error_response("Teacher access required", 403, "FORBIDDEN")
    teacher_id = get_jwt_identity()
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        return None, error_response("Invalid teacher identity", 403, "FORBIDDEN")
    return teacher_id.strip(), None


def _json_body():
    if not request.is_json:
        return None, error_response("JSON request body required", 415, "UNSUPPORTED_MEDIA_TYPE")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, error_response("Invalid JSON object", 400, "INVALID_REQUEST")
    return body, None


def _check_fields(body, allowed):
    unknown = set(body) - allowed
    if unknown:
        return error_response("Unsupported request fields: " + ", ".join(sorted(unknown)),
                              400, "INVALID_REQUEST")
    return None


def _problem_from(body):
    problem = body.get("problem")
    if not isinstance(problem, dict):
        raise ValueError("problem must be an object")
    unknown = set(problem) - set(PROBLEM_FIELDS)
    if unknown:
        raise ValueError("Unsupported problem fields: " + ", ".join(sorted(unknown)))
    for field in PROBLEM_FIELDS:
        value = problem.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > FIELD_LIMIT):
            raise ValueError(f"problem.{field} must be a string of at most {FIELD_LIMIT} characters")
    return {field: problem.get(field) for field in PROBLEM_FIELDS}


def _options(body):
    return {"input_schema": body.get("input_schema"),
            "method": body.get("method", "deterministic_baseline"),
            "count": body.get("count", 20), "seed": body.get("seed", 42)}


def _run(problem, options, assessment_context=None):
    import test_input_suggestion_service as service
    from ai_test_input_service import ModelUnavailable
    from input_schema import SchemaError

    try:
        data = service.suggest(problem, assessment_context=assessment_context, **options)
    except SchemaError as error:
        return error_response(str(error), 400 if error.code == "INVALID_INPUT_SCHEMA" else 422,
                              error.code)
    except service.UnsupportedFormat as error:
        return error_response(str(error), 422, "UNSUPPORTED_INPUT_FORMAT", reasons=error.reasons)
    except ModelUnavailable as error:
        return error_response(str(error), 503, "AI_MODEL_UNAVAILABLE")
    except ValueError as error:
        return error_response(str(error), 400, "INVALID_REQUEST")
    except Exception:
        current_app.logger.exception("Test input suggestion error")
        return error_response("Unable to suggest test inputs", 500, "INTERNAL_ERROR")
    if assessment_context:
        data["assessment_id"] = assessment_context["assessment_id"]
    return jsonify({"status": "success", "data": data}), 200


@test_input_bp.route("/api/test-inputs/suggestions", methods=["POST"])
@jwt_required()
def suggest_for_problem():
    teacher_id, error = _teacher()
    if error:
        return error
    body, error = _json_body()
    if error:
        return error
    error = _check_fields(body, {"problem", "input_schema", "method", "count", "seed"})
    if error:
        return error
    try:
        problem = _problem_from(body)
    except ValueError as exc:
        return error_response(str(exc), 400, "INVALID_REQUEST")
    if not (problem.get("problem_statement") or "").strip():
        return error_response("problem.problem_statement is required", 400, "INVALID_REQUEST")
    return _run(problem, _options(body))


@test_input_bp.route("/api/assessments/<int:assessment_id>/test-inputs/suggestions",
                     methods=["POST"])
@jwt_required()
def suggest_for_assessment(assessment_id):
    teacher_id, error = _teacher()
    if error:
        return error
    body, error = _json_body()
    if error:
        return error
    error = _check_fields(body, {"sample_input", "input_schema", "method", "count", "seed"})
    if error:
        return error
    sample = body.get("sample_input")
    if sample is not None and (not isinstance(sample, str) or len(sample) > FIELD_LIMIT):
        return error_response(f"sample_input must be a string of at most {FIELD_LIMIT} characters",
                              400, "INVALID_REQUEST")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT problem_statement, input_format, output_format, value_constraints
            FROM dbo.Assessment
            WHERE assessment_id = ? AND teacher_id = ?
        """, assessment_id, teacher_id)
        row = cursor.fetchone()
    except Exception:
        current_app.logger.exception("Test input suggestion: assessment lookup failed")
        return error_response("Unable to load assessment", 500, "INTERNAL_ERROR")
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return error_response("Assessment not found", 404, "NOT_FOUND")

    problem = {"problem_statement": row[0], "input_format": row[1], "output_format": row[2],
               "value_constraints": row[3], "sample_input": sample}
    options = {k: v for k, v in _options(body).items()}
    return _run(problem, options, assessment_context={"assessment_id": assessment_id})


@test_input_bp.route("/api/test-inputs/validate", methods=["POST"])
@jwt_required()
def validate_inputs_route():
    import test_input_suggestion_service as service
    from input_schema import SchemaError

    teacher_id, error = _teacher()
    if error:
        return error
    body, error = _json_body()
    if error:
        return error
    error = _check_fields(body, {"problem", "input_schema", "inputs"})
    if error:
        return error
    try:
        problem = _problem_from(body) if "problem" in body else {}
        data = service.validate_teacher_inputs(problem, body.get("inputs"),
                                               body.get("input_schema"))
    except SchemaError as exc:
        return error_response(str(exc), 400 if exc.code == "INVALID_INPUT_SCHEMA" else 422,
                              exc.code)
    except service.UnsupportedFormat as exc:
        return error_response(str(exc), 422, "UNSUPPORTED_INPUT_FORMAT", reasons=exc.reasons)
    except ValueError as exc:
        return error_response(str(exc), 400, "INVALID_REQUEST")
    return jsonify({"status": "success", "data": data}), 200
