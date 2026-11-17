"""
Teacher-defined rubric storage for assessments.

Storage table: dbo.AssessmentRubricCriterion
(see create_assessment_rubric_criterion.sql — NOT created until approved).

Until that table exists, every function here detects its absence and
raises RubricStorageUnavailable instead of failing with a SQL error, so
existing APIs keep working unchanged.

Fairness policy:
- The owning teacher may freely replace or clear the rubric while the
  assessment has no submissions.
- Once any submission exists the rubric is locked (RubricLockedError).
- Students cannot submit to an assessment whose rubric allocation is
  positive but whose rubric is incomplete (see check_rubric_ready).
"""

from decimal import Decimal

from db import get_connection
from grading_allocation import split_allocation, to_mark
from grading_rubric_allocation_adapter import validate_rubric_allocation


MAX_CRITERIA = 20
MAX_NAME_LENGTH = 150          # mirrors EvaluationCriterion.criterion_name
MAX_DESCRIPTION_LENGTH = 1000
MAX_CRITERION_MARKS = Decimal("999.99")   # DECIMAL(5,2)


class RubricStorageUnavailable(RuntimeError):
    """The AssessmentRubricCriterion table has not been created."""


class RubricLockedError(ValueError):
    """The rubric cannot change because submissions already exist."""


class RubricNotFoundError(ValueError):
    """Assessment not found or not owned by this teacher."""


TABLE_EXISTS_SQL = """
SELECT CASE WHEN OBJECT_ID(N'dbo.AssessmentRubricCriterion', N'U')
            IS NULL THEN 0 ELSE 1 END;
"""

LOAD_RUBRIC_SQL = """
SELECT rubric_criterion_id, criterion_name, max_marks, description
FROM dbo.AssessmentRubricCriterion WITH (HOLDLOCK)
WHERE assessment_id = ?
ORDER BY rubric_criterion_id;
"""

LOCK_OWNED_ASSESSMENT_SQL = """
SELECT total_marks, validation_percentage
FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
WHERE assessment_id = ? AND teacher_id = ?;
"""

READ_OWNED_ASSESSMENT_SQL = """
SELECT total_marks, validation_percentage
FROM dbo.Assessment
WHERE assessment_id = ? AND teacher_id = ?;
"""

COUNT_SUBMISSIONS_SQL = """
SELECT COUNT(*)
FROM dbo.Submission AS s WITH (UPDLOCK, HOLDLOCK)
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
WHERE sec.assessment_id = ?;
"""

COUNT_SUBMISSIONS_READ_SQL = """
SELECT COUNT(*)
FROM dbo.Submission AS s
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
WHERE sec.assessment_id = ?;
"""

DELETE_RUBRIC_SQL = """
DELETE FROM dbo.AssessmentRubricCriterion
WHERE assessment_id = ?;
"""

INSERT_CRITERION_SQL = """
INSERT INTO dbo.AssessmentRubricCriterion
    (assessment_id, criterion_name, max_marks, description)
VALUES (?, ?, ?, ?);
"""


# ------------------------------------------------------------------
# Pure validation (no database)
# ------------------------------------------------------------------

def normalize_rubric_input(criteria, rubric_marks_allocated):
    """
    Validate teacher-supplied criteria against the rubric allocation.

    Returns a list of {criterion_name, max_marks (Decimal), description}.
    """

    allocation = to_mark(rubric_marks_allocated, "rubric allocation")

    if criteria is None:
        criteria = []

    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")

    if allocation == 0:
        if criteria:
            raise ValueError(
                "Validation percentage is 100%, so no rubric "
                "criteria are allowed"
            )
        return []

    if not criteria:
        raise ValueError("At least one rubric criterion is required")

    if len(criteria) > MAX_CRITERIA:
        raise ValueError(f"At most {MAX_CRITERIA} criteria are allowed")

    normalized = []

    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("Each criterion must be an object")

        unknown = set(item) - {"criterion_name", "max_marks", "description"}
        if unknown:
            raise ValueError(
                "Unknown criterion fields: " + ", ".join(sorted(unknown))
            )

        name = item.get("criterion_name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("criterion_name must be non-empty text")

        name = name.strip()

        if len(name) > MAX_NAME_LENGTH:
            raise ValueError("criterion_name is too long")

        max_marks = to_mark(item.get("max_marks"), "max_marks")

        if max_marks <= 0:
            raise ValueError("max_marks must be greater than 0")

        if max_marks > MAX_CRITERION_MARKS:
            raise ValueError("max_marks is too large")

        if max_marks != max_marks.quantize(Decimal("0.01")):
            raise ValueError("max_marks allows at most 2 decimal places")

        description = item.get("description")

        if description is not None:
            if not isinstance(description, str):
                raise ValueError("description must be text")
            description = description.strip() or None
            if description and len(description) > MAX_DESCRIPTION_LENGTH:
                raise ValueError("description is too long")

        normalized.append({
            "criterion_name": name,
            "max_marks": max_marks,
            "description": description
        })

    # Duplicate names (case-insensitive) and exact sum == allocation.
    try:
        validate_rubric_allocation(normalized, allocation)
    except ValueError as error:
        if str(error) == "Rubric maximum does not match allocation":
            total = sum((c["max_marks"] for c in normalized), Decimal("0"))
            raise ValueError(
                f"Criterion max_marks total {total} must equal the "
                f"rubric allocation {allocation}"
            )
        raise

    return normalized


def serialize_criterion(row_or_dict):
    if isinstance(row_or_dict, dict):
        item = dict(row_or_dict)
    else:
        item = {
            "rubric_criterion_id": int(row_or_dict[0]),
            "criterion_name": row_or_dict[1],
            "max_marks": Decimal(str(row_or_dict[2])),
            "description": row_or_dict[3]
        }
    item["max_marks"] = str(item["max_marks"])
    return item


# ------------------------------------------------------------------
# Cursor-level helpers (usable inside other transactions)
# ------------------------------------------------------------------

def rubric_table_exists(cursor):
    cursor.execute(TABLE_EXISTS_SQL)
    row = cursor.fetchone()
    return bool(row and row[0])


def load_rubric_rows(cursor, assessment_id):
    """Return rubric criteria for grading (Decimal max_marks)."""

    if not rubric_table_exists(cursor):
        raise RubricStorageUnavailable(
            "AssessmentRubricCriterion table has not been created"
        )

    cursor.execute(LOAD_RUBRIC_SQL, (assessment_id,))

    return [
        {
            "rubric_criterion_id": int(row[0]),
            "criterion_name": row[1],
            "max_marks": Decimal(str(row[2])),
            "description": row[3]
        }
        for row in cursor.fetchall()
    ]


def check_rubric_ready(cursor, assessment_id, total_marks,
                       validation_percentage):
    """
    Raise ValueError when the assessment needs a rubric that is missing
    or inconsistent. Used by the submission transaction.

    Returns False (no enforcement) when rubric storage does not exist yet,
    preserving pre-rubric behaviour.
    """

    if not rubric_table_exists(cursor):
        return False

    _, rubric_marks = split_allocation(total_marks, validation_percentage)

    if rubric_marks == 0:
        return True

    criteria = load_rubric_rows(cursor, assessment_id)

    try:
        validate_rubric_allocation(criteria, rubric_marks)
    except ValueError:
        raise ValueError("Assessment is not open for submissions yet")

    return True


# ------------------------------------------------------------------
# Connection-level operations
# ------------------------------------------------------------------

def _require_identity(assessment_id, teacher_id):
    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    return teacher_id.strip()


def load_assessment_rubric(assessment_id, connection_factory=get_connection):
    """Read-only rubric load for the grading worker."""

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    conn = connection_factory()

    try:
        return load_rubric_rows(conn.cursor(), assessment_id)
    finally:
        conn.rollback()
        conn.close()


def get_teacher_rubric(assessment_id, teacher_id,
                       connection_factory=get_connection):
    teacher_id = _require_identity(assessment_id, teacher_id)
    conn = connection_factory()

    try:
        cursor = conn.cursor()
        cursor.execute(READ_OWNED_ASSESSMENT_SQL, (assessment_id, teacher_id))
        assessment = cursor.fetchone()

        if assessment is None:
            raise RubricNotFoundError("Assessment not found")

        test_marks, rubric_marks = split_allocation(
            assessment[0], assessment[1]
        )

        storage_available = rubric_table_exists(cursor)
        criteria = (
            load_rubric_rows(cursor, assessment_id)
            if storage_available else []
        )

        cursor.execute(COUNT_SUBMISSIONS_READ_SQL, (assessment_id,))
        submission_count = int(cursor.fetchone()[0])

        defined_total = sum(
            (c["max_marks"] for c in criteria), Decimal("0")
        )

        return {
            "assessment_id": assessment_id,
            "total_marks": str(Decimal(str(assessment[0]))),
            "validation_percentage": str(Decimal(str(assessment[1]))),
            "test_marks_allocated": str(test_marks),
            "rubric_marks_allocated": str(rubric_marks),
            "rubric_required": rubric_marks > 0,
            "rubric_complete": (
                rubric_marks == 0 and not criteria
            ) or (bool(criteria) and defined_total == rubric_marks),
            "storage_available": storage_available,
            "locked": submission_count > 0,
            "criteria": [serialize_criterion(c) for c in criteria]
        }

    finally:
        conn.rollback()
        conn.close()


def replace_assessment_rubric(assessment_id, teacher_id, criteria,
                              connection_factory=get_connection):
    """
    Atomically replace all rubric criteria for an owned assessment.
    Passing an empty list clears the rubric.
    """

    teacher_id = _require_identity(assessment_id, teacher_id)
    conn = connection_factory()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        if not rubric_table_exists(cursor):
            raise RubricStorageUnavailable(
                "AssessmentRubricCriterion table has not been created"
            )

        cursor.execute(LOCK_OWNED_ASSESSMENT_SQL, (assessment_id, teacher_id))
        assessment = cursor.fetchone()

        if assessment is None:
            raise RubricNotFoundError("Assessment not found")

        _, rubric_marks = split_allocation(assessment[0], assessment[1])

        if criteria:
            normalized = normalize_rubric_input(criteria, rubric_marks)
        else:
            normalized = []

        cursor.execute(COUNT_SUBMISSIONS_SQL, (assessment_id,))

        if int(cursor.fetchone()[0]) > 0:
            raise RubricLockedError(
                "Rubric cannot change after students have submitted"
            )

        cursor.execute(DELETE_RUBRIC_SQL, (assessment_id,))

        for item in normalized:
            cursor.execute(INSERT_CRITERION_SQL, (
                assessment_id,
                item["criterion_name"],
                item["max_marks"],
                item["description"]
            ))

        conn.commit()

        return {
            "assessment_id": assessment_id,
            "rubric_marks_allocated": str(rubric_marks),
            "criteria_saved": len(normalized),
            "criteria": [serialize_criterion(c) for c in normalized]
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def rubric_allows_allocation_change(cursor, assessment_id,
                                    new_total, new_percentage):
    """
    Used by PUT /api/assessments/<id>: reject mark changes that would
    make an existing rubric inconsistent. Returns None when OK, or an
    error message.
    """

    if not rubric_table_exists(cursor):
        return None

    criteria = load_rubric_rows(cursor, assessment_id)

    if not criteria:
        return None

    _, rubric_marks = split_allocation(new_total, new_percentage)
    defined = sum((c["max_marks"] for c in criteria), Decimal("0"))

    if defined != rubric_marks:
        return (
            "This change alters the rubric allocation; clear the rubric "
            "first (DELETE /api/assessments/<id>/rubric), then redefine it"
        )

    return None
