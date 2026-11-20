"""Read-only student portal. Visibility is the published section/course enrollment.

Never select TestCase, ReferenceSolution, rubric or generation metadata.
An inaccessible ID and an unpublished/nonexistent ID have the same 404.
"""
from db import get_connection

ACCESS_SQL = """
EXISTS (
 SELECT 1 FROM dbo.AssessmentSection asec
 INNER JOIN dbo.Enrollment_Export e
   ON e.section_id COLLATE DATABASE_DEFAULT = asec.section_id COLLATE DATABASE_DEFAULT
 INNER JOIN dbo.Student_Export student
   ON student.student_id COLLATE DATABASE_DEFAULT = e.student_id COLLATE DATABASE_DEFAULT
 INNER JOIN dbo.Section_Export section ON section.section_id COLLATE DATABASE_DEFAULT = asec.section_id COLLATE DATABASE_DEFAULT
 WHERE asec.assessment_id = a.assessment_id
 AND e.student_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
 AND e.course_no COLLATE DATABASE_DEFAULT = a.course_code COLLATE DATABASE_DEFAULT
 AND EXISTS (
   SELECT 1 FROM dbo.SectionCourseTeacher sct
   WHERE sct.section_id COLLATE DATABASE_DEFAULT = asec.section_id COLLATE DATABASE_DEFAULT
   AND sct.teacher_id COLLATE DATABASE_DEFAULT = a.teacher_id COLLATE DATABASE_DEFAULT
   AND sct.course_no COLLATE DATABASE_DEFAULT = a.course_code COLLATE DATABASE_DEFAULT
 )
)
"""

PUBLIC_FIELDS = ("assessment_id", "assessment_type", "title", "course_code",
                 "course_name", "total_marks", "deadline_at", "allow_late",
                 "late_penalty_marks", "problem_statement", "input_format",
                 "output_format")
PUBLIC_SQL = """
SELECT a.assessment_id, a.assessment_type, a.title, a.course_code, c.title,
       a.total_marks, a.deadline_at, a.allow_late, a.late_penalty_marks,
       a.problem_statement, a.input_format, a.output_format
FROM dbo.Assessment a
LEFT JOIN dbo.Course_Export c ON c.course_no COLLATE DATABASE_DEFAULT = a.course_code COLLATE DATABASE_DEFAULT
WHERE a.assessment_type IN ('Assignment', 'Quiz') AND
""" + ACCESS_SQL


class StudentAssessmentNotFound(ValueError):
    pass


def get_student_assessments(student_id, assessment_id=None, connection_factory=get_connection):
    if not isinstance(student_id, str) or not student_id.strip():
        raise ValueError("Invalid student identity")
    if assessment_id is not None and (type(assessment_id) is not int or assessment_id <= 0):
        raise ValueError("Invalid assessment ID")
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        params = (student_id.strip(),)
        sql = PUBLIC_SQL
        if assessment_id is not None:
            sql += " AND a.assessment_id = ?"
            params += (assessment_id,)
        cursor.execute(sql + " ORDER BY a.assessment_id DESC", params)
        result = []
        for row in cursor.fetchall():
            item = dict(zip(PUBLIC_FIELDS, row))
            item["total_marks"] = str(item["total_marks"])
            item["late_penalty_marks"] = str(item["late_penalty_marks"])
            item["deadline_at"] = item["deadline_at"].isoformat() if item["deadline_at"] else None
            item["allow_late"] = bool(item["allow_late"])
            item["publishing_status"] = "PUBLISHED"
            result.append(item)
        if assessment_id is not None:
            if not result:
                raise StudentAssessmentNotFound("Assessment not found")
            return result[0]
        return result
    finally:
        conn.rollback()
        conn.close()
