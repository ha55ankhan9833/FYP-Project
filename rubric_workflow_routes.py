"""
Dynamic rubric workflow API (teacher JWT; the teacher ID always comes from
the token, never the request).

GET  /api/marks-allocation?total_marks=50&validation_percentage=60
     exact split preview (no database)
POST /api/assessments/<id>/rubric/suggest   {"validation_percentage"?}
     offline-LLM suggestions; nothing saved, nothing approved
POST /api/assessments/<id>/rubric/approve   {"validation_percentage", "criteria"}
     the teacher's explicit approval: allocation + final criteria, atomically
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

import assessment_rubric_service as rubric_service
import rubric_suggestion_service as suggestion_service
from marks_allocation import MarksAllocationError, compute_allocation

logger = logging.getLogger(__name__)

rubric_workflow_bp = Blueprint("rubric_workflow_bp", __name__)


def _error(code, message, status, details=None):
    body = {"status": "error", "code": code, "message": message}
    if details:
        body["details"] = details
    return jsonify(body), status


def _teacher_id():
    if get_jwt().get("role") != "teacher":
        return None
    identity = get_jwt_identity()
    if not isinstance(identity, str) or not identity.strip():
        return None
    return identity.strip()


def _allocation_error(error, status):
    return _error("MARKS_ALLOCATION_INVALID", error.message, status,
                  {"reason": error.code, **error.details})


@rubric_workflow_bp.route("/api/marks-allocation", methods=["GET"])
@jwt_required()
def preview_allocation():
    if _teacher_id() is None:
        return _error("TEACHER_ONLY", "Teacher access required", 403)
    unknown = set(request.args) - {"total_marks", "validation_percentage"}
    if unknown:
        return _error("INVALID_PARAMETER", "Unknown parameters: " + ", ".join(sorted(unknown)),
                      400)
    for name in ("total_marks", "validation_percentage"):
        if not request.args.get(name, "").strip():
            return _error("INVALID_PARAMETER", f"{name} is required", 400)
    try:
        allocation = compute_allocation(request.args["total_marks"],
                                        request.args["validation_percentage"])
    except MarksAllocationError as error:
        return _allocation_error(error, 422)
    return jsonify({"status": "success", "allocation": allocation}), 200


@rubric_workflow_bp.route("/api/assessments/<int:assessment_id>/rubric/suggest",
                          methods=["POST"])
@jwt_required()
def suggest_rubric(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("TEACHER_ONLY", "Teacher access required", 403)
    data = request.get_json(silent=True)
    if data is None and not request.get_data():
        data = {}
    if not isinstance(data, dict):
        return _error("INVALID_REQUEST", "Valid JSON object required", 400)
    if set(data) - {"validation_percentage"}:
        return _error("INVALID_REQUEST", "Only 'validation_percentage' is accepted", 400)
    try:
        result = suggestion_service.suggest_for_assessment(
            assessment_id, teacher_id, data.get("validation_percentage"))
    except MarksAllocationError as error:
        return _allocation_error(error, 422)
    except suggestion_service.SuggestionContextError as error:
        return _error(error.code, error.message, error.status, error.details)
    except ValueError as error:
        return _error("INVALID_REQUEST", str(error), 400)
    except Exception:
        logger.exception("Rubric suggestion failed")
        return _error("RUBRIC_SUGGESTION_FAILED", "Unable to suggest a rubric right now", 500)
    return jsonify({"status": "success", "suggestion": result}), 200


@rubric_workflow_bp.route("/api/assessments/<int:assessment_id>/rubric/approve",
                          methods=["POST"])
@jwt_required()
def approve_rubric(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("TEACHER_ONLY", "Teacher access required", 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("INVALID_REQUEST", "Valid JSON object required", 400)
    if set(data) != {"validation_percentage", "criteria"}:
        return _error("INVALID_REQUEST",
                      "Body must contain exactly 'validation_percentage' and 'criteria'", 400)
    try:
        result = rubric_service.approve_assessment_rubric(
            assessment_id, teacher_id, data["validation_percentage"], data["criteria"])
    except rubric_service.RubricApprovalError as error:
        return _error(error.code, error.message, error.status, error.details)
    except rubric_service.RubricNotFoundError:
        return _error("ASSESSMENT_NOT_FOUND", "Assessment not found", 404)
    except rubric_service.RubricLockedError as error:
        return _error("RUBRIC_LOCKED", str(error), 409)
    except rubric_service.RubricStorageUnavailable:
        return _error("RUBRIC_STORAGE_UNAVAILABLE", "Rubric storage is not enabled", 503)
    except ValueError as error:
        return _error("INVALID_REQUEST", str(error), 400)
    except Exception:
        logger.exception("Rubric approval failed")
        return _error("RUBRIC_APPROVAL_FAILED", "Unable to approve the rubric right now", 500)
    return jsonify({"status": "success",
                    "message": ("Rubric already approved; nothing changed" if result["unchanged"]
                                else "Rubric approved"),
                    "rubric": result}), 200
