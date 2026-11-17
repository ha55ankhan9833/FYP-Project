"""
Evaluation review (teacher) and results (student) API.

Teacher:
  GET  /api/assessments/<id>/submissions
  GET  /api/submissions/<id>/evaluation
  POST /api/submissions/<id>/evaluation/finalize
  GET  /api/grading/status
Student:
  GET  /api/student/assessments
  GET  /api/student/assessments/<id>/submission
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

import evaluation_review_service as review

logger = logging.getLogger(__name__)

evaluation_bp = Blueprint("evaluation_bp", __name__)


def _error(message, status_code):
    return jsonify({"status": "error", "message": message}), status_code


def _identity(role):
    if get_jwt().get("role") != role:
        return None
    identity = get_jwt_identity()
    if not isinstance(identity, str) or not identity.strip():
        return None
    return identity.strip()


def _handle(exc, action):
    if isinstance(exc, review.NotFoundError):
        return _error(str(exc), 404)
    if isinstance(exc, review.ConflictError):
        return _error(str(exc), 409)
    if isinstance(exc, ValueError):
        return _error(str(exc), 400)
    logger.exception("%s failed", action)
    return _error(f"Unable to {action}", 500)


# ---------------------------- teacher ---------------------------------

@evaluation_bp.route("/api/assessments/<int:assessment_id>/submissions",
                     methods=["GET"])
@jwt_required()
def teacher_list_submissions(assessment_id):
    teacher_id = _identity("teacher")
    if teacher_id is None:
        return _error("Teacher access required", 403)
    try:
        items = review.list_assessment_submissions(assessment_id, teacher_id)
        return jsonify({"status": "success", "assessment_id": assessment_id,
                        "total": len(items), "submissions": items}), 200
    except Exception as exc:
        return _handle(exc, "fetch submissions")


@evaluation_bp.route("/api/submissions/<int:submission_id>/evaluation",
                     methods=["GET"])
@jwt_required()
def teacher_get_evaluation(submission_id):
    teacher_id = _identity("teacher")
    if teacher_id is None:
        return _error("Teacher access required", 403)
    try:
        data = review.get_teacher_submission_evaluation(
            submission_id, teacher_id)
        return jsonify({"status": "success", "submission": data}), 200
    except Exception as exc:
        return _handle(exc, "fetch evaluation")


@evaluation_bp.route(
    "/api/submissions/<int:submission_id>/evaluation/finalize",
    methods=["POST"])
@jwt_required()
def teacher_finalize_evaluation(submission_id):
    teacher_id = _identity("teacher")
    if teacher_id is None:
        return _error("Teacher access required", 403)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Valid JSON object required", 400)

    unknown = set(data) - {"final_score", "instructor_feedback", "criteria"}
    if unknown:
        return _error("Unknown fields: " + ", ".join(sorted(unknown)), 400)

    if "final_score" not in data:
        return _error("final_score is required", 400)

    try:
        # criteria is optional: teacher-approved criterion marks that must
        # reconcile with final_score (see finalize_evaluation).
        kwargs = {"criteria": data["criteria"]} if "criteria" in data else {}
        result = review.finalize_evaluation(
            submission_id, teacher_id,
            data["final_score"], data.get("instructor_feedback"), **kwargs)
        return jsonify({"status": "success",
                        "message": "Evaluation finalized",
                        "evaluation": result}), 200
    except Exception as exc:
        return _handle(exc, "finalize evaluation")


@evaluation_bp.route("/api/grading/status", methods=["GET"])
@jwt_required()
def grading_status():
    if _identity("teacher") is None:
        return _error("Teacher access required", 403)
    from secure_execution_gate import describe_sandbox_status
    from db import database_name
    # active_database lets development tools refuse to write when the
    # backend is not running against the isolated E2E test database.
    return jsonify({"status": "success", **describe_sandbox_status(),
                    "active_database": database_name()}), 200


# ---------------------------- student ---------------------------------

@evaluation_bp.route("/api/student/assessments", methods=["GET"])
@jwt_required()
def student_assessments():
    student_id = _identity("student")
    if student_id is None:
        return _error("Student access required", 403)
    try:
        items = review.list_student_assessments(student_id)
        return jsonify({"status": "success", "total": len(items),
                        "assessments": items}), 200
    except Exception as exc:
        return _handle(exc, "fetch assessments")


@evaluation_bp.route(
    "/api/student/assessments/<int:assessment_id>/submission",
    methods=["GET"])
@jwt_required()
def student_submission(assessment_id):
    student_id = _identity("student")
    if student_id is None:
        return _error("Student access required", 403)
    try:
        data = review.get_student_submission(assessment_id, student_id)
        return jsonify({"status": "success", "submission": data}), 200
    except Exception as exc:
        return _handle(exc, "fetch submission")
