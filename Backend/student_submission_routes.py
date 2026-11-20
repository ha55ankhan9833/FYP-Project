
import logging
import re
import pyodbc

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from student_submission_service import save_student_submission

logger = logging.getLogger(__name__)

student_submission_bp = Blueprint(
    "student_submission_bp", __name__
)


@student_submission_bp.route(
    "/api/assessments/<int:assessment_id>/submissions",
    methods=["POST"]
)
@jwt_required()
def submit_student_code(assessment_id):
    if get_jwt().get("role") != "student":
        return jsonify({
            "status": "error",
            "message": "Student access required"
        }), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    if "student_id" in data:
        return jsonify({
            "status": "error",
            "message": "Student ID must come from JWT"
        }), 400

    try:
        result = save_student_submission(
            assessment_id=assessment_id,
            student_id=get_jwt_identity(),
            source_code=data.get("source_code"),
            file_name=data.get("file_name")
        )

        return jsonify({
            "status": "success",
            "message": "Submission saved successfully",
            "submission": result
        }), 201

    except ValueError as error:
        message = str(error)

        if message in ("Student not found", "Assessment not found"):
            status_code = 404
            message = "Assessment or student not found"
        elif message == "Student already has a submission":
            status_code = 409
        elif message in (
            "Student is not enrolled in an assigned assessment section",
            "Assessment deadline has passed",
            "Assessment is not open for submissions yet"
        ):
            status_code = 403
        else:
            status_code = 400

        return jsonify({
            "status": "error",
            "message": message
        }), status_code

    except pyodbc.IntegrityError as error:
        # SQL Server duplicate-key errors: 2601 and 2627.
        # Match the numeric error code, not arbitrary message text.
        details = " ".join(str(arg) for arg in error.args)

        duplicate_error = bool(
            re.search(r"\b(?:2601|2627)\b", details)
        )

        constraint_name = (
            "UQ_Submission_Student_AssessmentSection"
        )

        if duplicate_error and constraint_name in details:
            return jsonify({
                "status": "error",
                "message": "Student already has a submission"
            }), 409

        logger.exception("Submission integrity error")

        return jsonify({
            "status": "error",
            "message": "Submission failed"
        }), 500

    except Exception:
        logger.exception("Student submission failed")

        return jsonify({
            "status": "error",
            "message": "Submission failed"
        }), 500
