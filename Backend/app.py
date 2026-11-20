from assessment_constraint_service import get_locked_approved_constraints, link_draft_to_assessment
from assessment_constraint_service import prepare_approved_constraint_fields

import os
from datetime import timedelta

import pyodbc

from flask import Flask, jsonify, request
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt,
    get_jwt_identity
)
from dotenv import load_dotenv
from db import get_connection

# Load environment variables
load_dotenv()

app = Flask(__name__)

from test_case_routes import test_case_bp
app.register_blueprint(test_case_bp)

from test_input_suggestion_routes import test_input_bp
app.register_blueprint(test_input_bp)

# JWT Configuration
secret_key = os.getenv("JWT_SECRET_KEY")

if not secret_key or len(secret_key) < 32:
    raise RuntimeError(
        "Set a secure JWT_SECRET_KEY (at least 32 characters) in .env"
    )

app.config["JWT_SECRET_KEY"] = secret_key
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=2)

jwt = JWTManager(app)

# Uniform JSON errors: {"status": "error", "message": ..., "code": ...}
from api_errors import register_error_handlers
register_error_handlers(app, jwt)

# Teacher Approval API
from teacher_approval_routes import approval_bp
app.register_blueprint(approval_bp)


# AI Constraint Analysis API
from ai_backend_routes import ai_bp
app.register_blueprint(ai_bp)



# --------------------------------
# HOME API
# --------------------------------

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "message": "FYP Auto Test Case Generator Backend",
        "status": "running"
    }), 200


# --------------------------------
# TEACHER LOGIN API
# --------------------------------

@app.route("/api/teacher/login", methods=["POST"])
def teacher_login():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({
            "status": "error",
            "message": "Email and password required"
        }), 400

    email = email.strip()

    if not email or not password:
        return jsonify({
            "status": "error",
            "message": "Email and password required"
        }), 400

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT teacher_id, teacher_name,
                   teacher_email, teacher_password, department
            FROM Teacher_Export
            WHERE teacher_email = ?
        """, (email,))

        teacher = cursor.fetchone()

        if teacher is None or teacher[3] != password:
            return jsonify({
                "status": "error",
                "message": "Invalid email or password"
            }), 401

        teacher_id = str(teacher[0])

        access_token = create_access_token(
            identity=teacher_id,
            additional_claims={"role": "teacher"}
        )

        return jsonify({
            "status": "success",
            "message": "Teacher login successful",
            "access_token": access_token,
            "token_type": "Bearer",
            "teacher": {
                "teacher_id": teacher_id,
                "teacher_name": teacher[1],
                "teacher_email": teacher[2],
                "department": teacher[4]
            }
        }), 200

    except Exception:
        app.logger.exception("Teacher Login Error")
        return jsonify({
            "status": "error",
            "message": "Login failed"
        }), 500

    finally:
        if conn is not None:
            conn.close()


# --------------------------------
# STUDENT LOGIN API
# --------------------------------

@app.route("/api/student/login", methods=["POST"])
def student_login():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({
            "status": "error",
            "message": "Email and password required"
        }), 400

    email = email.strip()

    if not email or not password:
        return jsonify({
            "status": "error",
            "message": "Email and password required"
        }), 400

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT student_id, student_name,
                   student_email, student_password
            FROM Student_Export
            WHERE student_email = ?
        """, (email,))

        student = cursor.fetchone()

        if student is None or student[3] != password:
            return jsonify({
                "status": "error",
                "message": "Invalid email or password"
            }), 401

        student_id = str(student[0])

        access_token = create_access_token(
            identity=student_id,
            additional_claims={"role": "student"}
        )

        return jsonify({
            "status": "success",
            "message": "Student login successful",
            "access_token": access_token,
            "token_type": "Bearer",
            "student": {
                "student_id": student_id,
                "student_name": student[1],
                "student_email": student[2]
            }
        }), 200

    except Exception:
        app.logger.exception("Student Login Error")
        return jsonify({
            "status": "error",
            "message": "Login failed"
        }), 500

    finally:
        if conn is not None:
            conn.close()


# --------------------------------
# PROTECTED USER PROFILE API
# --------------------------------

@app.route("/api/auth/me", methods=["GET"])
@jwt_required()
def current_user():
    user_id = get_jwt_identity()
    claims = get_jwt()

    return jsonify({
        "status": "success",
        "user_id": user_id,
        "role": claims.get("role")
    }), 200


# --------------------------------
# STUDENTS API - TEACHER ONLY
# --------------------------------

@app.route("/api/students", methods=["GET"])
@jwt_required()
def get_students():

    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT TOP 10
                student_id,
                student_name,
                student_email,
                program,
                section,
                section_id,
                semester,
                enrollment_status
            FROM Student_Export
            ORDER BY student_id
        """)

        columns = [column[0] for column in cursor.description]

        students = [
            dict(zip(columns, row))
            for row in cursor.fetchall()
        ]

        return jsonify({
            "status": "success",
            "total": len(students),
            "students": students
        }), 200

    except Exception:
        app.logger.exception("Students API Error")
        return jsonify({
            "status": "error",
            "message": "Unable to fetch students"
        }), 500

    finally:
        if conn is not None:
            conn.close()


# --------------------------------
# CREATE ASSESSMENT API
# TEACHER ONLY
# --------------------------------

from marks_allocation import MarksAllocationError, compute_allocation


def _marks_allocation_response(error):
    """400 when total marks x percentage cannot give 10 exact equal shares."""
    return jsonify({
        "status": "error",
        "code": "MARKS_ALLOCATION_INVALID",
        "message": error.message,
        "details": {"reason": error.code, **error.details}
    }), 400


def _assessment_for_draft(conn, draft_id, teacher_id):
    """Owned assessment already linked to this teacher's draft, or None."""
    if conn is None:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT d.assessment_id
            FROM dbo.ConstraintReviewDraft AS d
            INNER JOIN dbo.Assessment AS a
                ON a.assessment_id = d.assessment_id
            WHERE d.draft_id = ?
              AND d.teacher_id = ?
              AND a.teacher_id = ?
        """, draft_id, teacher_id, teacher_id)
        row = cursor.fetchone()
        conn.rollback()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        app.logger.exception("Draft lookup failed")
        return None


@app.route("/api/assessments", methods=["POST"])
@jwt_required()
def create_assessment():

    # Only teachers can create assessments
    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    required_fields = [
        "course_code",
        "assessment_type",
        "title",
        "problem_statement",
        "input_format",
        "output_format",
        "total_marks",
        "deadline_at",
        "validation_percentage"
    ]

    for field in required_fields:
        if field not in data or data[field] is None:
            return jsonify({
                "status": "error",
                "message": f"{field} is required"
            }), 400

        if isinstance(data[field], str) and not data[field].strip():
            return jsonify({
                "status": "error",
                "message": f"{field} cannot be empty"
            }), 400

    from datetime import datetime
    from decimal import Decimal, InvalidOperation

    try:
        total_marks = Decimal(str(data["total_marks"]))
        validation_percentage = Decimal(
            str(data["validation_percentage"])
        )
        late_penalty = Decimal(
            str(data.get("late_penalty_marks", 0))
        )

        if not all(
            value.is_finite()
            for value in [
                total_marks,
                validation_percentage,
                late_penalty
            ]
        ):
            raise ValueError("Values must be finite")

        if total_marks <= 0:
            raise ValueError("Total marks must be greater than 0")

        if not 0 <= validation_percentage <= 100:
            raise ValueError(
                "Validation percentage must be between 0 and 100"
            )

        if late_penalty < 0 or late_penalty > total_marks:
            raise ValueError("Invalid late penalty marks")

        allow_late = data.get("allow_late", False)

        if not isinstance(allow_late, bool):
            raise ValueError("allow_late must be true or false")

        if not allow_late and late_penalty != 0:
            raise ValueError(
                "Late penalty requires allow_late to be true"
            )

        deadline = datetime.fromisoformat(
            data["deadline_at"]
        )

        if deadline.tzinfo is not None:
            raise ValueError(
                "Use local deadline without timezone offset"
            )

        if deadline <= datetime.now():
            raise ValueError("Deadline must be in the future")

        for field in [
            "course_code",
            "assessment_type",
            "title",
            "problem_statement",
            "input_format",
            "output_format"
        ]:
            if not isinstance(data[field], str):
                raise ValueError(f"{field} must be text")

        optional_fields = [
            "value_constraints",
            "code_constraints",
            "additional_constraints"
        ]

        for field in optional_fields:
            if data.get(field) is not None and not isinstance(
                data[field], str
            ):
                raise ValueError(f"{field} must be text")

        # Exact equal share for each of the 10 test cases (no rounding).
        compute_allocation(total_marks, validation_percentage)

    except MarksAllocationError as error:
        return _marks_allocation_response(error)

    except (ValueError, TypeError, InvalidOperation) as error:
        return jsonify({
            "status": "error",
            "message": str(error)
        }), 400

    # Teacher ID is obtained from verified JWT
    teacher_id = get_jwt_identity()



    if "value_constraints" in data or "code_constraints" in data:
        return jsonify({
            "status": "error",
            "message": "Direct constraint submission is not allowed"
        }), 400

    draft_id = data.get("draft_id")

    if not isinstance(draft_id, str) or not draft_id.strip():
        return jsonify({
            "status": "error",
            "message": "Finalized draft_id required"
        }), 400

    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        approved_fields = get_locked_approved_constraints(
            cursor, draft_id, teacher_id
        )

        cursor.execute("""
            INSERT INTO Assessment (
                teacher_id,
                course_code,
                assessment_type,
                title,
                problem_statement,
                input_format,
                output_format,
                total_marks,
                deadline_at,
                allow_late,
                late_penalty_marks,
                value_constraints,
                code_constraints,
                additional_constraints,
                validation_percentage
            )
            OUTPUT INSERTED.assessment_id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            teacher_id,
            data["course_code"].strip(),
            data["assessment_type"].strip(),
            data["title"].strip(),
            data["problem_statement"].strip(),
            data["input_format"].strip(),
            data["output_format"].strip(),
            total_marks,
            deadline,
            int(allow_late),
            late_penalty,
            approved_fields["value_constraints"],
            approved_fields["code_constraints"],
            data.get("additional_constraints"),
            validation_percentage
        ))

        assessment_id = cursor.fetchone()[0]

        link_draft_to_assessment(
            cursor, draft_id, teacher_id, assessment_id
        )

        conn.commit()
        cursor.close()

        return jsonify({
            "status": "success",
            "message": "Assessment created successfully",
            "assessment_id": assessment_id,
            "teacher_id": teacher_id
        }), 201

    except ValueError as error:
        if conn is not None:
            conn.rollback()

        if str(error) == "Draft already linked to an assessment":
            # Repeated "Generate" click / retry: point the client at the
            # assessment this teacher already created from this draft
            # instead of creating a duplicate.
            existing = _assessment_for_draft(conn, draft_id, teacher_id)
            if existing is not None:
                return jsonify({
                    "status": "error",
                    "code": "DRAFT_ALREADY_USED",
                    "message": "An assessment was already created from this draft",
                    "assessment_id": existing
                }), 409

        return jsonify({
            "status": "error",
            "message": str(error)
        }), 400

    except Exception:
        if conn is not None:
            conn.rollback()

        app.logger.exception("Assessment Creation Error")

        return jsonify({
            "status": "error",
            "message": "Unable to create assessment"
        }), 500

    finally:
        if conn is not None:
            conn.close()


# --------------------------------
# GET ASSESSMENTS API - TEACHER ONLY
# --------------------------------

@app.route("/api/assessments", methods=["GET"])
@jwt_required()
def get_assessments():
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
            SELECT assessment_id, teacher_id, course_code,
                   assessment_type, title, problem_statement,
                   input_format, output_format, total_marks,
                   deadline_at, allow_late, late_penalty_marks,
                   value_constraints, code_constraints,
                   additional_constraints, validation_percentage
            FROM Assessment
            WHERE teacher_id = ?
            ORDER BY assessment_id DESC
        """, (teacher_id,))

        columns = [column[0] for column in cursor.description]
        assessments = []
        for row in cursor.fetchall():
            item = dict(zip(columns, row))
            if item["deadline_at"] is not None:
                item["deadline_at"] = item["deadline_at"].isoformat()
            item["allow_late"] = bool(item["allow_late"])
            for field in ("total_marks", "late_penalty_marks", "validation_percentage"):
                if item[field] is not None:
                    item[field] = float(item[field])
            assessments.append(item)

        return jsonify({
            "status": "success",
            "total": len(assessments),
            "assessments": assessments
        }), 200

    except Exception:
        app.logger.exception("Assessment Retrieval Error")
        return jsonify({
            "status": "error",
            "message": "Unable to fetch assessments"
        }), 500

    finally:
        if conn is not None:
            conn.close()


# --------------------------------
# GET SINGLE ASSESSMENT API
# TEACHER ONLY
# --------------------------------

@app.route("/api/assessments/<int:assessment_id>", methods=["GET"])
@jwt_required()
def get_single_assessment(assessment_id):

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
            SELECT
                assessment_id,
                teacher_id,
                course_code,
                assessment_type,
                title,
                problem_statement,
                input_format,
                output_format,
                total_marks,
                deadline_at,
                allow_late,
                late_penalty_marks,
                value_constraints,
                code_constraints,
                additional_constraints,
                validation_percentage
            FROM Assessment
            WHERE assessment_id = ?
              AND teacher_id = ?
        """, (assessment_id, teacher_id))

        row = cursor.fetchone()

        if row is None:
            return jsonify({
                "status": "error",
                "message": "Assessment not found"
            }), 404

        columns = [
            column[0] for column in cursor.description
        ]

        assessment = dict(zip(columns, row))

        for field in [
            "total_marks",
            "late_penalty_marks",
            "validation_percentage"
        ]:
            if assessment[field] is not None:
                assessment[field] = float(assessment[field])

        assessment["allow_late"] = bool(
            assessment["allow_late"]
        )

        assessment["deadline_at"] = (
            assessment["deadline_at"].isoformat()
        )

        return jsonify({
            "status": "success",
            "assessment": assessment
        }), 200

    except Exception:
        app.logger.exception("Single Assessment API Error")

        return jsonify({
            "status": "error",
            "message": "Unable to fetch assessment"
        }), 500

    finally:
        if conn is not None:
            conn.close()

# --------------------------------
# UPDATE ASSESSMENT API
# TEACHER ONLY
# --------------------------------

@app.route("/api/assessments/<int:assessment_id>", methods=["PUT"])
@jwt_required()
def update_assessment(assessment_id):

    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    allowed_fields = [
        "course_code",
        "assessment_type",
        "title",
        "problem_statement",
        "input_format",
        "output_format",
        "total_marks",
        "deadline_at",
        "allow_late",
        "late_penalty_marks",
        "additional_constraints",
        "validation_percentage"
    ]

    if not data:
        return jsonify({
            "status": "error",
            "message": "At least one field is required"
        }), 400

    unknown_fields = set(data) - set(allowed_fields)

    if unknown_fields:
        return jsonify({
            "status": "error",
            "message": "Unknown fields: " + ", ".join(sorted(unknown_fields))
        }), 400

    from datetime import datetime
    from decimal import Decimal, InvalidOperation

    text_fields = [
        "course_code", "assessment_type", "title",
        "problem_statement", "input_format", "output_format"
    ]

    optional_text_fields = [
        "additional_constraints"
    ]

    try:
        for field in text_fields:
            if field in data:
                if not isinstance(data[field], str) or not data[field].strip():
                    raise ValueError(f"{field} must be non-empty text")
                data[field] = data[field].strip()

        for field in optional_text_fields:
            if field in data and data[field] is not None:
                if not isinstance(data[field], str):
                    raise ValueError(f"{field} must be text")

        for field in [
            "total_marks", "late_penalty_marks",
            "validation_percentage"
        ]:
            if field in data:
                if isinstance(data[field], bool) or data[field] is None:
                    raise ValueError(f"{field} must be a number")
                value = Decimal(str(data[field]))
                if not value.is_finite():
                    raise ValueError(f"{field} must be finite")
                data[field] = value

        if "allow_late" in data:
            if not isinstance(data["allow_late"], bool):
                raise ValueError("allow_late must be true or false")
            data["allow_late"] = int(data["allow_late"])

        if "deadline_at" in data:
            if not isinstance(data["deadline_at"], str):
                raise ValueError("deadline_at must be text")

            deadline = datetime.fromisoformat(data["deadline_at"])

            if deadline.tzinfo is not None:
                raise ValueError("Use local deadline without timezone offset")

            if deadline <= datetime.now():
                raise ValueError("Deadline must be in the future")

            data["deadline_at"] = deadline

    except (ValueError, TypeError, InvalidOperation) as error:
        return jsonify({
            "status": "error",
            "message": str(error)
        }), 400

    teacher_id = get_jwt_identity()
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT total_marks, late_penalty_marks, allow_late,
                   validation_percentage
            FROM Assessment
            WHERE assessment_id = ? AND teacher_id = ?
        """, (assessment_id, teacher_id))

        existing = cursor.fetchone()

        if existing is None:
            return jsonify({
                "status": "error",
                "message": "Assessment not found"
            }), 404

        total_marks = data.get("total_marks", existing[0])
        late_penalty = data.get("late_penalty_marks", existing[1])
        allow_late = data.get("allow_late", existing[2])
        validation = data.get("validation_percentage", existing[3])

        if total_marks <= 0:
            raise ValueError("Total marks must be greater than 0")

        if not 0 <= validation <= 100:
            raise ValueError(
                "Validation percentage must be between 0 and 100"
            )

        if late_penalty < 0 or late_penalty > total_marks:
            raise ValueError("Invalid late penalty marks")

        if not allow_late and late_penalty != 0:
            raise ValueError(
                "Late penalty requires allow_late to be true"
            )

        # Grading fairness: marks allocation cannot change once
        # students have submitted, and must stay consistent with
        # any teacher-defined rubric.
        if "total_marks" in data or "validation_percentage" in data:
            try:
                compute_allocation(total_marks, validation)
            except MarksAllocationError as error:
                conn.rollback()
                return _marks_allocation_response(error)

            # Saved cases store total x percentage / 10 each; changing
            # the allocation afterwards would leave them inconsistent.
            cursor.execute(
                "SELECT COUNT(*) FROM dbo.TestCase WHERE assessment_id = ?",
                (assessment_id,))

            if int(cursor.fetchone()[0]) > 0 and (
                Decimal(str(total_marks)) != Decimal(str(existing[0]))
                or Decimal(str(validation)) != Decimal(str(existing[3]))
            ):
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "code": "TEST_CASES_ALREADY_SAVED",
                    "message": (
                        "Marks allocation cannot change after test cases "
                        "have been saved with equal marks"
                    )
                }), 409

            cursor.execute("""
                SELECT COUNT(*)
                FROM dbo.Submission AS s
                INNER JOIN dbo.AssessmentSection AS sec
                    ON sec.assessment_section_id = s.assessment_section_id
                WHERE sec.assessment_id = ?
            """, (assessment_id,))

            if int(cursor.fetchone()[0]) > 0:
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "message": (
                        "Marks allocation cannot change after "
                        "students have submitted"
                    )
                }), 409

            from assessment_rubric_service import (
                rubric_allows_allocation_change
            )

            rubric_conflict = rubric_allows_allocation_change(
                cursor, assessment_id, total_marks, validation
            )

            if rubric_conflict is not None:
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "message": rubric_conflict
                }), 409

        # Column names come only from the server-side allowlist.
        set_clause = ", ".join(
            f"{field} = ?" for field in data
        )

        values = list(data.values())

        cursor.execute(
            f"""
            UPDATE Assessment
            SET {set_clause}
            WHERE assessment_id = ? AND teacher_id = ?
            """,
            (*values, assessment_id, teacher_id)
        )

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Assessment updated successfully",
            "assessment_id": assessment_id
        }), 200

    except ValueError as error:
        if conn is not None:
            conn.rollback()

        return jsonify({
            "status": "error",
            "message": str(error)
        }), 400

    except Exception:
        if conn is not None:
            conn.rollback()

        app.logger.exception("Assessment Update Error")

        return jsonify({
            "status": "error",
            "message": "Unable to update assessment"
        }), 500

    finally:
        if conn is not None:
            conn.close()

# --------------------------------
# DELETE ASSESSMENT API
# TEACHER ONLY - SAFE DELETE
# --------------------------------

@app.route("/api/assessments/<int:assessment_id>", methods=["DELETE"])
@jwt_required()
def delete_assessment(assessment_id):

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

        # Start transaction and lock the assessment row
        # to avoid concurrent deletion-related changes.
        cursor.execute("""
            SELECT assessment_id
            FROM Assessment WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
              AND teacher_id = ?
        """, (assessment_id, teacher_id))

        if cursor.fetchone() is None:
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": "Assessment not found"
            }), 404

        # Block deletion if related records exist.
        cursor.execute("""
            SELECT
                CASE WHEN EXISTS (
                    SELECT 1 FROM AssessmentSection
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM Notification
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM TestCase
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END
        """, (assessment_id, assessment_id, assessment_id))

        related = cursor.fetchone()

        if any(related):
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": (
                    "Cannot delete assessment because related "
                    "sections, notifications, or test cases exist"
                )
            }), 409

        # Teacher rubric criteria reference Assessment via FK.
        # Existence is checked first so this keeps working if the
        # rubric table is ever absent (e.g. restored older database).
        cursor.execute("""
            SELECT OBJECT_ID(N'dbo.AssessmentRubricCriterion', N'U')
        """)

        if cursor.fetchone()[0] is not None:
            cursor.execute("""
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM dbo.AssessmentRubricCriterion
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END
            """, (assessment_id,))

            if cursor.fetchone()[0]:
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "message": (
                        "Cannot delete assessment because rubric "
                        "criteria exist; clear the rubric first"
                    )
                }), 409

        # ReferenceSolution and ConstraintReviewDraft both reference
        # Assessment with ON DELETE NO ACTION, so SQL Server would
        # reject the DELETE; report it as a conflict instead of a 500.
        cursor.execute("""
            SELECT
                CASE WHEN EXISTS (
                    SELECT 1 FROM dbo.ReferenceSolution
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM dbo.ConstraintReviewDraft
                    WHERE assessment_id = ?
                ) THEN 1 ELSE 0 END
        """, (assessment_id, assessment_id))

        has_reference, has_draft = cursor.fetchone()

        if has_reference or has_draft:
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": (
                    "Cannot delete assessment because a reference "
                    "solution or constraint review draft is linked to it"
                )
            }), 409

        try:
            cursor.execute("""
                DELETE FROM Assessment
                WHERE assessment_id = ?
                  AND teacher_id = ?
            """, (assessment_id, teacher_id))
        except pyodbc.IntegrityError as error:
            # Safety net: any other table referencing Assessment
            # (SQL Server error 547 = FK constraint conflict).
            if "547" not in " ".join(str(arg) for arg in error.args):
                raise
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": (
                    "Cannot delete assessment because related "
                    "records exist"
                )
            }), 409

        if cursor.rowcount != 1:
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": "Assessment could not be deleted"
            }), 409

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Assessment deleted successfully",
            "assessment_id": assessment_id
        }), 200

    except Exception:
        if conn is not None:
            conn.rollback()

        app.logger.exception("Assessment Delete Error")

        return jsonify({
            "status": "error",
            "message": "Unable to delete assessment"
        }), 500

    finally:
        if conn is not None:
            conn.close()

# --------------------------------
# RUN FLASK APPLICATION
# --------------------------------


# Student Submission API
from student_submission_routes import student_submission_bp
app.register_blueprint(student_submission_bp)

from student_assessment_routes import student_assessment_bp
app.register_blueprint(student_assessment_bp)

# Teacher rubric management API
from assessment_rubric_routes import rubric_bp
app.register_blueprint(rubric_bp)

# Teacher evaluation review + student results API
from evaluation_routes import evaluation_bp
app.register_blueprint(evaluation_bp)

# Teacher assessment-to-section assignment API
from assessment_section_routes import assessment_section_bp
app.register_blueprint(assessment_section_bp)

# Teacher reference solution API (needed before test-case generation)
from reference_solution_routes import reference_solution_bp
app.register_blueprint(reference_solution_bp)

# Teacher "My Sections" API (read-only, Instructor Dashboard / Select Section)
from teacher_section_routes import teacher_section_bp
app.register_blueprint(teacher_section_bp)

# Teacher dynamic rubric: marks allocation, offline-LLM suggestion, approval
from rubric_workflow_routes import rubric_workflow_bp
app.register_blueprint(rubric_workflow_bp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
