from datetime import datetime
from db import get_connection


def check_student_submission_eligibility(
    assessment_id,
    student_id
):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(student_id, str) or not student_id.strip():
        raise ValueError("Invalid student ID")

    conn = get_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                a.assessment_id,
                a.deadline_at,
                a.allow_late,
                a.late_penalty_marks,
                a.course_code
            FROM dbo.Assessment a
            WHERE a.assessment_id = ?
        """, assessment_id)

        assessment = cursor.fetchone()

        if assessment is None:
            raise ValueError("Assessment not found")

        _, deadline, allow_late, penalty, course_code = assessment

        cursor.execute("""
            SELECT student_id
            FROM dbo.Student_Export
            WHERE student_id = ?
        """, student_id)

        if cursor.fetchone() is None:
            raise ValueError("Student not found")

        cursor.execute("""
            SELECT TOP (1)
                asec.assessment_section_id,
                asec.section_id
            FROM dbo.AssessmentSection asec
            INNER JOIN dbo.Enrollment_Export e
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
            FROM dbo.Submission
            WHERE student_id = ?
              AND assessment_section_id IN (
                  SELECT assessment_section_id
                  FROM dbo.AssessmentSection
                  WHERE assessment_id = ?
              )
        """, student_id, assessment_id)

        if int(cursor.fetchone()[0]) > 0:
            raise ValueError("Student already has a submission")

        # Use the same SQL Server clock as submission service.
        cursor.execute("SELECT SYSDATETIME()")
        now = cursor.fetchone()[0]

        is_late = now > deadline

        if is_late and not bool(allow_late):
            raise ValueError("Assessment deadline has passed")

        return {
            "eligible": True,
            "assessment_id": assessment_id,
            "assessment_section_id": assessment_section_id,
            "section_id": section_id,
            "student_id": student_id,
            "is_late": is_late,
            "late_penalty_marks": str(penalty),
            "deadline_at": deadline.isoformat()
        }

    finally:
        conn.close()
