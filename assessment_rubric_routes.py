"""
Teacher rubric management API.

GET    /api/assessments/<id>/rubric   view rubric + allocation + lock state
PUT    /api/assessments/<id>/rubric   replace all criteria atomically
DELETE /api/assessments/<id>/rubric   clear criteria (only before submissions)

Teacher JWT required; only the owning teacher can read or modify.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

import assessment_rubric_service as rubric_service

logger = logging.getLogger(__name__)

rubric_bp = Blueprint("assessment_rubric_bp", __name__)


def _error(message, status_code):
    return jsonify({"status": "error", "message": message}), status_code


def _teacher_id():
    if get_jwt().get("role") != "teacher":
        return None
    return str(get_jwt_identity())


def _handle(exc, action):
    if isinstance(exc, rubric_service.RubricStorageUnavailable):
        return _error(
            "Rubric storage is not enabled yet "
            "(database table pending approval)", 503
        )
    if isinstance(exc, rubric_service.RubricNotFoundError):
        return _error("Assessment not found", 404)
    if isinstance(exc, rubric_service.RubricLockedError):
        return _error(str(exc), 409)
    if isinstance(exc, ValueError):
        return _error(str(exc), 400)

    logger.exception("Rubric %s failed", action)
    return _error(f"Unable to {action} rubric", 500)


@rubric_bp.route("/api/assessments/<int:assessment_id>/rubric",
                 methods=["GET"])
@jwt_required()
def get_rubric(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)

    try:
        rubric = rubric_service.get_teacher_rubric(assessment_id, teacher_id)
        return jsonify({"status": "success", "rubric": rubric}), 200
    except Exception as exc:
        return _handle(exc, "fetch")


@rubric_bp.route("/api/assessments/<int:assessment_id>/rubric",
                 methods=["PUT"])
@jwt_required()
def put_rubric(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return _error("Valid JSON object required", 400)

    if set(data) - {"criteria"}:
        return _error("Only the 'criteria' field is accepted", 400)

    criteria = data.get("criteria")

    if not isinstance(criteria, list) or not criteria:
        return _error(
            "criteria must be a non-empty list "
            "(use DELETE to clear the rubric)", 400
        )

    try:
        result = rubric_service.replace_assessment_rubric(
            assessment_id, teacher_id, criteria
        )
        return jsonify({
            "status": "success",
            "message": "Rubric saved",
            "rubric": result
        }), 200
    except Exception as exc:
        return _handle(exc, "save")


@rubric_bp.route("/api/assessments/<int:assessment_id>/rubric",
                 methods=["DELETE"])
@jwt_required()
def delete_rubric(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)

    try:
        result = rubric_service.replace_assessment_rubric(
            assessment_id, teacher_id, []
        )
        return jsonify({
            "status": "success",
            "message": "Rubric cleared",
            "rubric": result
        }), 200
    except Exception as exc:
        return _handle(exc, "clear")
