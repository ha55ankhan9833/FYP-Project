from datetime import datetime
from db import get_connection
from assessment_rubric_service import check_rubric_ready


MAX_SOURCE_LENGTH = 100_000


def save_student_submission(
    assessment_id,
    student_id,
    source_code,
    file_name=None
):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(student_id, str) or not student_id.strip():
        raise ValueError("Invalid student ID")

    student_id = student_id.strip()

    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("Source code cannot be empty")

    if len(source_code) > MAX_SOURCE_LENGTH:
        raise ValueError("Source code exceeds maximum length")

    if file_name is not None:
        if not isinstance(file_name, str):
            raise ValueError("Invalid file name")
        if len(file_name) > 255:
            raise ValueError("File name too long")

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        # Serialize submission attempts for this student.
        cursor.execute("""
            SELECT student_id
            FROM dbo.Student_Export WITH (UPDLOCK, HOLDLOCK)
            WHERE student_id = ?
        """, student_id)

        if cursor.fetchone() is None:
            raise ValueError("Student not found")

        cursor.execute("""
            SELECT
                assessment_id,
                deadline_at,
                allow_late,
                late_penalty_marks,
                course_code,
                total_marks,
                validation_percentage
            FROM dbo.Assessment WITH (HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        assessment = cursor.fetchone()

        if assessment is None:
            raise ValueError("Assessment not found")

        (
            _, deadline, allow_late, penalty, course_code,
            total_marks, validation_percentage
        ) = assessment

        # Rubric must be complete before grading can be fair.
        # No-op until dbo.AssessmentRubricCriterion is created.
        check_rubric_ready(
            cursor, assessment_id, total_marks, validation_percentage
        )

        cursor.execute("""
            SELECT TOP (1)
                asec.assessment_section_id,
                asec.section_id
            FROM dbo.AssessmentSection asec WITH (HOLDLOCK)
            INNER JOIN dbo.Enrollment_Export e WITH (HOLDLOCK)
              ON e.section_id COLLATE DATABASE_DEFAULT
                 = asec.section_id COLLATE DATABASE_DEFAULT
            WHERE asec.assessment_id = ?
              AND e.student_id COLLATE DATABASE_DEFAULT
                  = ? COLLATE DATABASE_DEFAULT
              AND e.course_no COLLATE DATABASE_DEFAULT
                  = ? COLLATE DATABASE_DEFAULT
              AND EXISTS (
                  SELECT 1
                  FROM dbo.SectionCourseTeacher sct
                  INNER JOIN dbo.Assessment a
                    ON a.assessment_id = asec.assessment_id
                  WHERE sct.section_id COLLATE DATABASE_DEFAULT
                        = asec.section_id COLLATE DATABASE_DEFAULT
                    AND sct.course_no COLLATE DATABASE_DEFAULT
                        = a.course_code COLLATE DATABASE_DEFAULT
                    AND sct.teacher_id COLLATE DATABASE_DEFAULT
                        = a.teacher_id COLLATE DATABASE_DEFAULT
              )
            ORDER BY asec.assessment_section_id
        """, assessment_id, student_id, course_code)

        assignment = cursor.fetchone()

        if assignment is None:
            raise ValueError(
                "Student is not enrolled in an assigned assessment section"
            )

        assessment_section_id, section_id = assignment

        cursor.execute("""
            SELECT COUNT(*)
            FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)
            WHERE student_id = ?
              AND assessment_section_id IN (
                  SELECT assessment_section_id
                  FROM dbo.AssessmentSection
                  WHERE assessment_id = ?
              )
        """, student_id, assessment_id)

        if int(cursor.fetchone()[0]) > 0:
            raise ValueError("Student already has a submission")

        # Compare SQL Server local timestamps consistently.
        cursor.execute("SELECT SYSDATETIME()")
        now = cursor.fetchone()[0]

        is_late = now > deadline

        if is_late and not bool(allow_late):
            raise ValueError("Assessment deadline has passed")

        cursor.execute("""
            INSERT INTO dbo.Submission
                (
                    student_id,
                    assessment_section_id,
                    file_name,
                    source_code,
                    uploaded_at,
                    submitted_at
                )
            OUTPUT INSERTED.submission_id
            VALUES (?, ?, ?, ?, SYSDATETIME(), SYSDATETIME())
        """,
            student_id,
            assessment_section_id,
            file_name,
            source_code
        )

        submission_id = int(cursor.fetchone()[0])

        # Create the grading job in the same SQL transaction.
        # The Submission and GradingJob must commit or rollback together.
        cursor.execute('''
            INSERT INTO dbo.GradingJob (submission_id)
            VALUES (?)
        ''', submission_id)

        conn.commit()

        return {
            "submission_id": submission_id,
            "assessment_id": assessment_id,
            "assessment_section_id": assessment_section_id,
            "section_id": section_id,
            "student_id": student_id,
            "is_late": is_late,
            "late_penalty_marks": str(penalty),
            "status": "SUBMITTED"
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
