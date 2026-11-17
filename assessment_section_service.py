"""
Assessment-to-section assignment (dbo.AssessmentSection).

Real relationships used (verified against FYP_Database 2026-11-17):
- dbo.AssessmentSection(assessment_section_id IDENTITY PK,
  assessment_id FK -> Assessment, section_id FK -> Section_Export,
  UNIQUE (assessment_id, section_id) = UQ_AssessmentSection)
- dbo.SectionCourseTeacher(section_id, teacher_id, course_no) says which
  teacher teaches which course in which section. It contains duplicate
  rows, so it is only ever queried with EXISTS.
- dbo.Enrollment_Export(student_id, section_id, course_no) gives students.

A teacher may assign an assessment to a section only when:
  they own the assessment AND the section exists in Section_Export AND
  SectionCourseTeacher has (section_id, teacher_id, assessment.course_code).
This is the same rule student_submission_service applies at submit time.

Assignment never touches existing Submission rows. Removal is refused
while any submission exists for that assessment-section.
"""

import re

from db import get_connection


MAX_SECTIONS_PER_REQUEST = 50
SECTION_ID_MAX_LENGTH = 20          # AssessmentSection.section_id varchar(20)
SECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-./]*$")
UNIQUE_VIOLATION_CODES = ("2601", "2627")
FK_VIOLATION_CODE = "547"


class AssessmentNotFoundError(ValueError):
    """Assessment missing or not owned by the caller (no existence leak)."""


class AssignmentConflictError(ValueError):
    pass


class SectionAssignmentError(ValueError):
    """One or more requested sections failed validation; nothing saved."""

    PRIORITY = {"SECTION_NOT_FOUND": 404,
                "TEACHER_NOT_ASSIGNED_TO_SECTION": 403,
                "ALREADY_ASSIGNED": 409}

    def __init__(self, errors):
        self.errors = errors
        reasons = {e["reason"] for e in errors}
        for reason, code in self.PRIORITY.items():
            if reason in reasons:
                self.status_code = code
                break
        else:
            self.status_code = 400
        super().__init__("One or more sections cannot be assigned")


# ----------------------------------------------------------------------
# SQL
# ----------------------------------------------------------------------

LOCK_OWNED_ASSESSMENT_SQL = """
SELECT assessment_id, course_code, deadline_at, allow_late
FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_id = ? AND teacher_id = ?;
"""

READ_OWNED_ASSESSMENT_SQL = """
SELECT assessment_id, course_code, deadline_at, allow_late
FROM dbo.Assessment
WHERE assessment_id = ? AND teacher_id = ?;
"""

SECTION_EXISTS_SQL = """
SELECT section_id
FROM dbo.Section_Export
WHERE section_id = ?;
"""

TEACHES_SECTION_SQL = """
SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM dbo.SectionCourseTeacher AS sct
    WHERE sct.section_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
      AND sct.teacher_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
      AND sct.course_no  COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
) THEN 1 ELSE 0 END;
"""

EXISTING_ASSIGNMENT_SQL = """
SELECT assessment_section_id
FROM dbo.AssessmentSection WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_id = ?
  AND section_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT;
"""

INSERT_ASSIGNMENT_SQL = """
INSERT INTO dbo.AssessmentSection (assessment_id, section_id)
OUTPUT INSERTED.assessment_section_id
VALUES (?, ?);
"""

COUNT_SECTION_SUBMISSIONS_SQL = """
SELECT COUNT(*)
FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_section_id = ?;
"""

DELETE_ASSIGNMENT_SQL = """
DELETE FROM dbo.AssessmentSection
WHERE assessment_section_id = ? AND assessment_id = ?;
"""

LIST_ASSIGNED_SQL = """
SELECT
    sec.assessment_section_id,
    sec.section_id,
    se.section_name,
    se.semester_no,
    se.program,
    se.academic_session,
    (SELECT COUNT(DISTINCT e.student_id)
       FROM dbo.Enrollment_Export AS e
      WHERE e.section_id COLLATE DATABASE_DEFAULT
            = sec.section_id COLLATE DATABASE_DEFAULT
        AND e.course_no COLLATE DATABASE_DEFAULT
            = ? COLLATE DATABASE_DEFAULT) AS enrolled_students,
    (SELECT COUNT(*) FROM dbo.Submission AS s
      WHERE s.assessment_section_id = sec.assessment_section_id)
        AS submission_count
FROM dbo.AssessmentSection AS sec
LEFT JOIN dbo.Section_Export AS se
    ON se.section_id COLLATE DATABASE_DEFAULT
       = sec.section_id COLLATE DATABASE_DEFAULT
WHERE sec.assessment_id = ?
ORDER BY sec.section_id;
"""

LIST_ASSIGNABLE_SQL = """
SELECT
    t.section_id,
    se.section_name,
    se.semester_no,
    se.program,
    se.academic_session,
    (SELECT COUNT(DISTINCT e.student_id)
       FROM dbo.Enrollment_Export AS e
      WHERE e.section_id COLLATE DATABASE_DEFAULT
            = t.section_id COLLATE DATABASE_DEFAULT
        AND e.course_no COLLATE DATABASE_DEFAULT
            = ? COLLATE DATABASE_DEFAULT) AS enrolled_students,
    CASE WHEN EXISTS (
        SELECT 1 FROM dbo.AssessmentSection AS sec
        WHERE sec.assessment_id = ?
          AND sec.section_id COLLATE DATABASE_DEFAULT
              = t.section_id COLLATE DATABASE_DEFAULT
    ) THEN 1 ELSE 0 END AS already_assigned
FROM (
    SELECT DISTINCT sct.section_id
    FROM dbo.SectionCourseTeacher AS sct
    WHERE sct.teacher_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
      AND sct.course_no  COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
) AS t
INNER JOIN dbo.Section_Export AS se
    ON se.section_id COLLATE DATABASE_DEFAULT
       = t.section_id COLLATE DATABASE_DEFAULT
ORDER BY t.section_id;
"""


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------

def _require_owner_args(assessment_id, teacher_id):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")
    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")
    return teacher_id.strip()


def normalize_section_id(section_id):
    if not isinstance(section_id, str):
        raise ValueError("section_id must be text")
    value = section_id.strip()
    if not value:
        raise ValueError("section_id cannot be empty")
    if len(value) > SECTION_ID_MAX_LENGTH:
        raise ValueError("section_id is too long")
    if not SECTION_ID_PATTERN.match(value):
        raise ValueError("section_id contains invalid characters")
    return value


def normalize_section_ids(section_ids):
    if not isinstance(section_ids, list) or not section_ids:
        raise ValueError("section_ids must be a non-empty list")
    if len(section_ids) > MAX_SECTIONS_PER_REQUEST:
        raise ValueError(
            f"At most {MAX_SECTIONS_PER_REQUEST} sections per request")

    normalized, seen = [], set()
    for raw in section_ids:
        value = normalize_section_id(raw)
        key = value.casefold()          # DB collations are case-insensitive
        if key in seen:
            raise ValueError(f"Duplicate section_id in request: {value}")
        seen.add(key)
        normalized.append(value)
    return normalized


def _sql_error_codes(error):
    return " ".join(str(arg) for arg in getattr(error, "args", ()))


def _is_unique_violation(error):
    details = _sql_error_codes(error)
    return (any(re.search(rf"\b{code}\b", details)
                for code in UNIQUE_VIOLATION_CODES)
            and "UQ_AssessmentSection" in details)


def _is_fk_violation(error):
    return bool(re.search(rf"\b{FK_VIOLATION_CODE}\b", _sql_error_codes(error)))


def _iso(value):
    return value.isoformat() if value is not None else None


# ----------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------

def assign_sections(assessment_id, teacher_id, section_ids,
                    connection_factory=get_connection):
    """
    All-or-nothing: either every requested section is assigned, or
    nothing is written and SectionAssignmentError lists each problem.
    """

    teacher_id = _require_owner_args(assessment_id, teacher_id)
    requested = normalize_section_ids(section_ids)

    conn = connection_factory()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        # Serializes concurrent assignment/removal for this assessment.
        cursor.execute(LOCK_OWNED_ASSESSMENT_SQL, (assessment_id, teacher_id))
        assessment = cursor.fetchone()
        if assessment is None:
            raise AssessmentNotFoundError("Assessment not found")

        course_code = assessment[1]
        errors = []

        for section_id in requested:
            cursor.execute(SECTION_EXISTS_SQL, (section_id,))
            row = cursor.fetchone()
            if row is None:
                errors.append({"section_id": section_id,
                               "reason": "SECTION_NOT_FOUND"})
                continue

            cursor.execute(TEACHES_SECTION_SQL,
                           (section_id, teacher_id, course_code))
            if not cursor.fetchone()[0]:
                errors.append({"section_id": section_id,
                               "reason": "TEACHER_NOT_ASSIGNED_TO_SECTION"})
                continue

            cursor.execute(EXISTING_ASSIGNMENT_SQL, (assessment_id, section_id))
            if cursor.fetchone() is not None:
                errors.append({"section_id": section_id,
                               "reason": "ALREADY_ASSIGNED"})

        if errors:
            raise SectionAssignmentError(errors)

        created = []
        for section_id in requested:
            cursor.execute(INSERT_ASSIGNMENT_SQL, (assessment_id, section_id))
            created.append({
                "assessment_section_id": int(cursor.fetchone()[0]),
                "section_id": section_id
            })

        conn.commit()

        return {
            "assessment_id": assessment_id,
            "course_code": course_code,
            "assigned": created,
            "deadline_at": _iso(assessment[2])
        }

    except Exception as error:
        conn.rollback()
        if _is_unique_violation(error):
            # Lost a race the lock should already prevent; still safe.
            raise AssignmentConflictError(
                "Section is already assigned to this assessment") from error
        raise

    finally:
        conn.close()


def remove_section(assessment_id, teacher_id, section_id,
                   connection_factory=get_connection):
    teacher_id = _require_owner_args(assessment_id, teacher_id)
    section_id = normalize_section_id(section_id)

    conn = connection_factory()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute(LOCK_OWNED_ASSESSMENT_SQL, (assessment_id, teacher_id))
        if cursor.fetchone() is None:
            raise AssessmentNotFoundError("Assessment not found")

        cursor.execute(EXISTING_ASSIGNMENT_SQL, (assessment_id, section_id))
        row = cursor.fetchone()
        if row is None:
            raise AssessmentNotFoundError(
                "Section is not assigned to this assessment")

        assessment_section_id = int(row[0])

        # Range lock also blocks a concurrent submission insert.
        cursor.execute(COUNT_SECTION_SUBMISSIONS_SQL, (assessment_section_id,))
        if int(cursor.fetchone()[0]) > 0:
            raise AssignmentConflictError(
                "Cannot remove section because students have submitted")

        cursor.execute(DELETE_ASSIGNMENT_SQL,
                       (assessment_section_id, assessment_id))
        if cursor.rowcount != 1:
            raise AssignmentConflictError("Assignment changed concurrently")

        conn.commit()

        return {"assessment_id": assessment_id,
                "section_id": section_id,
                "assessment_section_id": assessment_section_id,
                "removed": True}

    except Exception as error:
        conn.rollback()
        if _is_fk_violation(error):
            raise AssignmentConflictError(
                "Cannot remove section because related records exist"
            ) from error
        raise

    finally:
        conn.close()


def _owned_course(cursor, assessment_id, teacher_id):
    cursor.execute(READ_OWNED_ASSESSMENT_SQL, (assessment_id, teacher_id))
    row = cursor.fetchone()
    if row is None:
        raise AssessmentNotFoundError("Assessment not found")
    return row[1]


def list_assigned_sections(assessment_id, teacher_id,
                           connection_factory=get_connection):
    teacher_id = _require_owner_args(assessment_id, teacher_id)
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        course_code = _owned_course(cursor, assessment_id, teacher_id)
        cursor.execute(LIST_ASSIGNED_SQL, (course_code, assessment_id))
        return [{
            "assessment_section_id": int(r[0]),
            "section_id": r[1],
            "section_name": r[2],
            "semester_no": r[3],
            "program": r[4],
            "academic_session": r[5],
            "enrolled_students": int(r[6] or 0),
            "submission_count": int(r[7] or 0),
            "removable": int(r[7] or 0) == 0
        } for r in cursor.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def list_assignable_sections(assessment_id, teacher_id,
                             connection_factory=get_connection):
    teacher_id = _require_owner_args(assessment_id, teacher_id)
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        course_code = _owned_course(cursor, assessment_id, teacher_id)
        cursor.execute(LIST_ASSIGNABLE_SQL,
                       (course_code, assessment_id, teacher_id, course_code))
        return {
            "course_code": course_code,
            "sections": [{
                "section_id": r[0],
                "section_name": r[1],
                "semester_no": r[2],
                "program": r[3],
                "academic_session": r[4],
                "enrolled_students": int(r[5] or 0),
                "already_assigned": bool(r[6])
            } for r in cursor.fetchall()]
        }
    finally:
        conn.rollback()
        conn.close()
