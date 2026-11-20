import logging
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
import student_assessment_service as service

student_assessment_bp = Blueprint("student_assessment_bp", __name__)
logger = logging.getLogger(__name__)


@student_assessment_bp.route("/api/student/assessments/<int:assessment_id>", methods=["GET"])
@jwt_required()
def student_assessments(assessment_id=None):
    identity = get_jwt_identity()
    if get_jwt().get("role") != "student" or not isinstance(identity, str) or not identity.strip():
        return jsonify(status="error", code="STUDENT_ONLY", message="Student access required"), 403
    if request.args:
        return jsonify(status="error", code="INVALID_PARAMETER", message="Query parameters are not supported; student identity comes from JWT"), 400
    try:
        result = service.get_student_assessments(identity, assessment_id)
        if assessment_id is not None:
            return jsonify(status="success", assessment=result), 200
        return jsonify(status="success", total=len(result), assessments=result), 200
    except service.StudentAssessmentNotFound:
        return jsonify(status="error", code="ASSESSMENT_NOT_FOUND", message="Assessment not found"), 404
    except ValueError:
        return jsonify(status="error", code="INVALID_REQUEST", message="Invalid assessment request"), 400
    except Exception:
        logger.exception("Student assessment retrieval failed")
        return jsonify(status="error", code="ASSESSMENTS_UNAVAILABLE", message="Unable to load assessments"), 500
