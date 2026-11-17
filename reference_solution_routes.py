"""
Teacher reference-solution API (wraps the existing, ownership-checked
reference_solution_service / reference_solution_approval_service).

POST /api/assessments/<id>/reference-solution          {"source_code": "..."}
GET  /api/assessments/<id>/reference-solution
POST /api/assessments/<id>/reference-solution/approve  {"source_hash": "<sha256>"}

Approval requires the teacher to echo back the SHA-256 they reviewed,
so the exact bytes that were reviewed are the bytes that get approved.
Saving or approving never executes the code.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from reference_solution_approval_service import (
    approve_reference_solution,
    review_reference_solution
)
from reference_solution_service import save_reference_solution

logger = logging.getLogger(__name__)

reference_solution_bp = Blueprint("reference_solution_bp", __name__)


def _error(message, status_code):
    return jsonify({"status": "error", "message": message}), status_code


def _teacher_id():
    if get_jwt().get("role") != "teacher":
        return None
    identity = get_jwt_identity()
    if not isinstance(identity, str) or not identity.strip():
        return None
    return identity.strip()


def _handle(exc, action):
    if isinstance(exc, ValueError):
        message = str(exc)
        if "access denied" in message or "not found" in message:
            return _error("Assessment or reference solution not found", 404)
        if "already exists" in message or "Only PENDING" in message \
                or "conflict" in message:
            return _error(message, 409)
        return _error(message, 400)
    logger.exception("Reference solution %s failed", action)
    return _error(f"Unable to {action} reference solution", 500)


@reference_solution_bp.route(
    "/api/assessments/<int:assessment_id>/reference-solution",
    methods=["POST"])
@jwt_required()
def create_reference(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"source_code"}:
        return _error("Body must contain only 'source_code'", 400)

    try:
        result = save_reference_solution(
            assessment_id, teacher_id, data["source_code"])
        return jsonify({"status": "success",
                        "message": "Reference solution saved; review and "
                                   "approve it with its source_hash",
                        "reference_solution": result}), 201
    except Exception as exc:
        return _handle(exc, "save")


@reference_solution_bp.route(
    "/api/assessments/<int:assessment_id>/reference-solution",
    methods=["GET"])
@jwt_required()
def get_reference(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)
    try:
        return jsonify({"status": "success",
                        "reference_solution":
                            review_reference_solution(assessment_id,
                                                      teacher_id)}), 200
    except Exception as exc:
        return _handle(exc, "fetch")


@reference_solution_bp.route(
    "/api/assessments/<int:assessment_id>/reference-solution/approve",
    methods=["POST"])
@jwt_required()
def approve_reference(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"source_hash"}:
        return _error("Body must contain only 'source_hash'", 400)

    try:
        result = approve_reference_solution(
            assessment_id, teacher_id, data["source_hash"])
        return jsonify({"status": "success",
                        "message": "Reference solution approved",
                        "reference_solution": result}), 200
    except Exception as exc:
        return _handle(exc, "approve")
