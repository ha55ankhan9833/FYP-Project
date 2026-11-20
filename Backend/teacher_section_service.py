"""
Read-only: the authenticated teacher's sections for one course, semester
and intake (Instructor Dashboard "My Sections" and Select Section screen).

Relationships used (verified read-only against FYP_Database 2026-11-19):
- dbo.SectionCourseTeacher(section_id, teacher_id, course_no) = which
  teacher teaches which course in which section. It has no uniqueness
  constraint, so it is only queried with EXISTS (no duplicate rows).
- dbo.Section_Export(section_id PK, section_name, semester_no varchar,
  program, academic_session) holds the section facts.
- dbo.Enrollment_Export(student_id, section_id, course_no) gives the
  course-enrolled students; counted with COUNT(DISTINCT student_id).
  Its teacher_id column is a section-level export value, not the course
  teacher, so it is never used here.
- dbo.Course_Export(course_no PK, title), dbo.Teacher_Export(teacher_id PK).

The tables use different collations, so every cross-table comparison is
COLLATE DATABASE_DEFAULT (same pattern as assessment_section_service).
No student identifiers are selected; only aggregate counts.
"""

import re

from db import get_connection


COURSE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.]{0,99}$")
SESSION_PATTERN = re.compile(r"^[0-9]{4}[A-Za-z]{2}$")
YEAR_PREFIX = re.compile(r"^([0-9]{4})")
MAX_SEMESTER = 20


class CourseNotFoundError(ValueError):
    """The requested course code does not exist in Course_Export."""


class TeacherNotFoundError(ValueError):
    """The token's teacher_id is not in Teacher_Export."""


class SectionNotFoundError(ValueError):
    """Section missing, or not in the requested semester/session."""


class SectionAccessDeniedError(PermissionError):
    """The teacher is not allocated to this section for this course."""


TEACHER_SQL = """
SELECT teacher_id, teacher_name, department
FROM dbo.Teacher_Export
WHERE teacher_id = ?;
"""

COURSE_SQL = """
SELECT course_no, title
FROM dbo.Course_Export
WHERE course_no = ?;
"""

TEACHER_SECTIONS_SQL = """
SELECT
    se.section_id,
    se.section_name,
    se.program,
    se.semester_no,
    se.academic_session,
    (SELECT COUNT(DISTINCT e.student_id)
       FROM dbo.Enrollment_Export AS e
      WHERE e.section_id COLLATE DATABASE_DEFAULT
            = se.section_id COLLATE DATABASE_DEFAULT
        AND e.course_no COLLATE DATABASE_DEFAULT
            = ? COLLATE DATABASE_DEFAULT) AS enrolled_students
FROM dbo.Section_Export AS se
WHERE se.semester_no = ?
  AND se.academic_session = ?
  AND EXISTS (
      SELECT 1
      FROM dbo.SectionCourseTeacher AS sct
      WHERE sct.section_id COLLATE DATABASE_DEFAULT
            = se.section_id COLLATE DATABASE_DEFAULT
        AND sct.teacher_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
        AND sct.course_no  COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT)
ORDER BY se.program, se.section_name, se.section_id;
"""


SECTION_ACCESS_SQL = """
SELECT
    se.section_id,
    se.section_name,
    se.program,
    se.semester_no,
    se.academic_session,
    CASE WHEN EXISTS (
        SELECT 1
        FROM dbo.SectionCourseTeacher AS sct
        WHERE sct.section_id COLLATE DATABASE_DEFAULT
              = se.section_id COLLATE DATABASE_DEFAULT
          AND sct.teacher_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
          AND sct.course_no  COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
    ) THEN 1 ELSE 0 END AS teaches
FROM dbo.Section_Export AS se
WHERE se.section_id = ?;
"""

# One row per enrolled student: DISTINCT student_id first, then a join on
# the Student_Export primary key. Only the roster fields are selected.
SECTION_ROSTER_SQL = """
SELECT
    e.student_id,
    s.student_name,
    s.enrollment_status
FROM (
    SELECT DISTINCT en.student_id COLLATE DATABASE_DEFAULT AS student_id
    FROM dbo.Enrollment_Export AS en
    WHERE en.section_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
      AND en.course_no  COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
) AS e
LEFT JOIN dbo.Student_Export AS s
    ON s.student_id COLLATE DATABASE_DEFAULT = e.student_id
ORDER BY e.student_id;
"""

SECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-./]{0,19}$")


def _text(value):
    return value.strip() if isinstance(value, str) else value


def normalize_filters(course_code, semester_no, academic_session):
    """Validate query parameters; raises ValueError with a safe message."""
    if not isinstance(course_code, str) or not COURSE_CODE_PATTERN.match(course_code.strip()):
        raise ValueError("course_code is required (letters, digits, '-', '_', '.')")

    if not isinstance(semester_no, str) or not semester_no.strip().isdigit():
        raise ValueError("semester_no is required and must be a whole number")
    semester = int(semester_no.strip())
    if not 1 <= semester <= MAX_SEMESTER:
        raise ValueError(f"semester_no must be between 1 and {MAX_SEMESTER}")

    if not isinstance(academic_session, str) or not SESSION_PATTERN.match(academic_session.strip()):
        raise ValueError("academic_session is required, e.g. 2025SM")

    return course_code.strip(), semester, academic_session.strip()


def _semester_value(raw):
    raw = _text(raw)
    return int(raw) if isinstance(raw, str) and raw.isdigit() else raw


def _intake_year(session_code):
    """First four digits of the stored session code (e.g. 2025SM -> 2025)."""
    match = YEAR_PREFIX.match(session_code or "")
    return int(match.group(1)) if match else None


def list_teacher_sections(teacher_id, course_code, semester_no, academic_session,
                          connection_factory=get_connection):
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")
    teacher_id = teacher_id.strip()
    course_code, semester, session = normalize_filters(
        course_code, semester_no, academic_session)

    conn = connection_factory()
    try:
        cursor = conn.cursor()

        cursor.execute(TEACHER_SQL, (teacher_id,))
        teacher_row = cursor.fetchone()
        if teacher_row is None:
            raise TeacherNotFoundError("Teacher account not found")

        cursor.execute(COURSE_SQL, (course_code,))
        course_row = cursor.fetchone()
        if course_row is None:
            raise CourseNotFoundError("Course not found")

        stored_code, course_name = _text(course_row[0]), _text(course_row[1])

        cursor.execute(TEACHER_SECTIONS_SQL,
                       (stored_code, str(semester), session, teacher_id, stored_code))
        rows = cursor.fetchall()
    finally:
        conn.rollback()
        conn.close()

    teacher = {"teacher_id": _text(teacher_row[0]),
               "teacher_name": _text(teacher_row[1]),
               "department": _text(teacher_row[2])}

    sections = [{
        "section_id": _text(r[0]),
        "section_name": _text(r[1]),
        "program": _text(r[2]),
        "course_code": stored_code,
        "course_name": course_name,
        "semester_no": _semester_value(r[3]),
        "academic_session": _text(r[4]),
        "intake_year": _intake_year(_text(r[4])),
        "enrolled_students": int(r[5] or 0),
        "teacher_id": teacher["teacher_id"],
        "teacher_name": teacher["teacher_name"],
    } for r in rows]

    return {
        "teacher": teacher,
        "course": {"course_code": stored_code, "course_name": course_name},
        "filters": {"course_code": stored_code, "semester_no": semester,
                    "academic_session": session},
        "total": len(sections),
        "total_enrolled_students": sum(s["enrolled_students"] for s in sections),
        "sections": sections,
    }


def normalize_section_id(section_id):
    if not isinstance(section_id, str) or not SECTION_ID_PATTERN.match(section_id.strip()):
        raise ValueError("section_id is invalid")
    return section_id.strip()


def list_section_students(teacher_id, section_id, course_code, semester_no,
                          academic_session, connection_factory=get_connection):
    """
    Course-enrolled students of one section the teacher teaches.

    Order of checks: course exists (404) -> section exists (404) -> teacher
    allocated to section for this course (403) -> section is in the
    requested semester/session (404) -> roster. A teacher who is not
    allocated learns nothing about the section's term or students.
    """
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")
    teacher_id = teacher_id.strip()
    section_id = normalize_section_id(section_id)
    course_code, semester, session = normalize_filters(
        course_code, semester_no, academic_session)

    conn = connection_factory()
    try:
        cursor = conn.cursor()

        cursor.execute(COURSE_SQL, (course_code,))
        course_row = cursor.fetchone()
        if course_row is None:
            raise CourseNotFoundError("Course not found")
        stored_code, course_name = _text(course_row[0]), _text(course_row[1])

        cursor.execute(SECTION_ACCESS_SQL, (teacher_id, stored_code, section_id))
        section_row = cursor.fetchone()
        if section_row is None:
            raise SectionNotFoundError("Section not found")
        if not section_row[5]:
            raise SectionAccessDeniedError(
                "You are not assigned to this section for this course")

        stored_semester = _semester_value(section_row[3])
        stored_session = _text(section_row[4])
        if stored_semester != semester or (stored_session or "").casefold() != session.casefold():
            raise SectionNotFoundError(
                "Section not found for this semester and academic session")

        cursor.execute(SECTION_ROSTER_SQL, (_text(section_row[0]), stored_code))
        rows = cursor.fetchall()
    finally:
        conn.rollback()
        conn.close()

    stored_section_id = _text(section_row[0])
    students = [{
        "student_id": _text(r[0]),
        "registration_no": _text(r[0]),
        "student_name": _text(r[1]),
        "section_id": stored_section_id,
        "enrollment_status": _text(r[2]),
    } for r in rows]

    return {
        "section": {
            "section_id": stored_section_id,
            "section_name": _text(section_row[1]),
            "program": _text(section_row[2]),
            "semester_no": stored_semester,
            "academic_session": stored_session,
            "course_code": stored_code,
            "course_name": course_name,
        },
        "total": len(students),
        "students": students,
    }
