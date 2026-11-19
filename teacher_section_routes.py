"""
Teacher "My Sections" API (read-only).

GET /api/teacher/sections?course_code=CSC-101&semester_no=1&academic_session=2025SM

Teacher JWT required. The teacher is always the token identity; a
client-supplied teacher_id is rejected, never used.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from api_errors import error_response
import teacher_section_service as service

logger = logging.getLogger(__name__)

teacher_section_bp = Blueprint("teacher_section_bp", __name__)

ALLOWED_PARAMS = {"course_code", "semester_no", "academic_session"}


def _teacher_id():
    if get_jwt().get("role") != "teacher":
        return None
    identity = get_jwt_identity()
    if not isinstance(identity, str) or not identity.strip():
        return None
    return identity.strip()


@teacher_section_bp.route("/api/teacher/sections", methods=["GET"])
@jwt_required()
def get_teacher_sections():
    teacher_id = _teacher_id()
    if teacher_id is None:
        return error_response("Teacher access required", 403, "TEACHER_ONLY")

    unknown = sorted(set(request.args) - ALLOWED_PARAMS)
    if unknown:
        return error_response(f"Unsupported query parameter(s): {', '.join(unknown)}",
                              400, "INVALID_PARAMETER")
    if any(len(request.args.getlist(name)) > 1 for name in ALLOWED_PARAMS):
        return error_response("Each query parameter may appear only once",
                              400, "INVALID_PARAMETER")

    try:
        data = service.list_teacher_sections(
            teacher_id,
            request.args.get("course_code"),
            request.args.get("semester_no"),
            request.args.get("academic_session"))
        return jsonify({"status": "success", **data}), 200
    except service.CourseNotFoundError as exc:
        return error_response(str(exc), 404, "COURSE_NOT_FOUND")
    except service.TeacherNotFoundError as exc:
        return error_response(str(exc), 403, "TEACHER_NOT_FOUND")
    except ValueError as exc:
        return error_response(str(exc), 400, "INVALID_PARAMETER")
    except Exception:
        logger.exception("Teacher sections lookup failed")
        return error_response("Unable to load sections right now", 500,
                              "SECTIONS_UNAVAILABLE")
