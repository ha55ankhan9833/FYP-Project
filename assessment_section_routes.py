"""
Teacher assessment-to-section assignment API.

GET    /api/assessments/<id>/sections                 assigned sections
GET    /api/assessments/<id>/assignable-sections      sections the teacher teaches for this course
POST   /api/assessments/<id>/sections                 {"section_ids": ["..."]}
DELETE /api/assessments/<id>/sections/<section_id>    only when no submissions exist

Teacher JWT required. Teacher identity comes only from the token and
ownership is enforced in every SQL statement.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

import assessment_section_service as service

logger = logging.getLogger(__name__)

assessment_section_bp = Blueprint("assessment_section_bp", __name__)


def _error(message, status_code, **extra):
    return jsonify({"status": "error", "message": message, **extra}), status_code


def _teacher_id():
    if get_jwt().get("role") != "teacher":
        return None
    identity = get_jwt_identity()
    if not isinstance(identity, str) or not identity.strip():
        return None
    return identity.strip()


def _handle(exc, action):
    if isinstance(exc, service.AssessmentNotFoundError):
        return _error(str(exc), 404, code="ASSESSMENT_NOT_FOUND")
    if isinstance(exc, service.AssessmentNotReadyError):
        return _error(str(exc), 409, code="ASSESSMENT_NOT_READY", missing=exc.missing)
    if isinstance(exc, service.SectionAssignmentError):
        return _error(str(exc), exc.status_code, code="SECTION_ASSIGNMENT_REJECTED", errors=exc.errors)
    if isinstance(exc, service.AssignmentConflictError):
        return _error(str(exc), 409, code="PUBLISH_CONFLICT")
    if isinstance(exc, ValueError):
        return _error(str(exc), 400, code="INVALID_REQUEST")
    logger.exception("Section assignment %s failed", action)
    return _error(f"Unable to {action}", 500, code="PUBLISH_UNAVAILABLE")


@assessment_section_bp.route(
    "/api/assessments/<int:assessment_id>/sections", methods=["GET"])
@jwt_required()
def get_assigned_sections(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403, code="TEACHER_ONLY")
    try:
        sections = service.list_assigned_sections(assessment_id, teacher_id)
        return jsonify({"status": "success", "assessment_id": assessment_id,
                        "total": len(sections), "sections": sections}), 200
    except Exception as exc:
        return _handle(exc, "fetch assigned sections")


@assessment_section_bp.route(
    "/api/assessments/<int:assessment_id>/assignable-sections",
    methods=["GET"])
@jwt_required()
def get_assignable_sections(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403, code="TEACHER_ONLY")
    try:
        data = service.list_assignable_sections(assessment_id, teacher_id)
        return jsonify({"status": "success", "assessment_id": assessment_id,
                        "total": len(data["sections"]), **data}), 200
    except Exception as exc:
        return _handle(exc, "fetch assignable sections")


@assessment_section_bp.route(
    "/api/assessments/<int:assessment_id>/sections", methods=["POST"])
@jwt_required()
def post_sections(assessment_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403, code="TEACHER_ONLY")

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Valid JSON object required", 400, code="INVALID_REQUEST")
    if set(data) != {"section_ids"}:
        return _error("Body must contain only 'section_ids'", 400, code="INVALID_REQUEST")

    try:
        result = service.assign_sections(
            assessment_id, teacher_id, data["section_ids"])
        return jsonify({"status": "success",
                        "message": "Sections already published" if result.get("replayed") else "Sections published",
                        **result}), 200 if result.get("replayed") else 201
    except Exception as exc:
        return _handle(exc, "assign sections")


@assessment_section_bp.route(
    "/api/assessments/<int:assessment_id>/sections/<section_id>",
    methods=["DELETE"])
@jwt_required()
def delete_section(assessment_id, section_id):
    teacher_id = _teacher_id()
    if teacher_id is None:
        return _error("Teacher access required", 403, code="TEACHER_ONLY")
    try:
        result = service.remove_section(assessment_id, teacher_id, section_id)
        return jsonify({"status": "success",
                        "message": "Section assignment removed",
                        **result}), 200
    except Exception as exc:
        return _handle(exc, "remove section")
