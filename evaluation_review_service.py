"""
Teacher evaluation review / finalization and student result access.

Rules
- Teachers only see submissions for assessments they own.
- Suggested scores are never shown to students; students see
  results only after the teacher finalizes (result_status FINALIZED).
- Hidden test-case inputs/expected outputs are never returned to
  students (only pass counts, after finalization).
- Infrastructure failures (job FAILED, no Evaluation) are graded
  manually by the teacher; they are never auto-zeroed.
- The student result is CONSTRUCTED from permitted fields
  (student_result_builder); internal feedback text is never forwarded.
- AI criterion marks (EvaluationCriterion) are kept as the audit trail
  of the AI recommendation. Teacher-approved criterion marks live in
  dbo.EvaluationCriterionApproval once that table exists; until then
  criterion-level approval requests are refused (409).
"""

import re
from decimal import Decimal, InvalidOperation

from db import get_connection
from diagnostics_sanitizer import sanitize_compiler_diagnostics
from student_result_builder import build_student_result, verified_test_marks


FINALIZED = "FINALIZED"
MAX_FEEDBACK = 20000


class NotFoundError(ValueError):
    pass


class ConflictError(ValueError):
    pass


def _iso(value):
    return value.isoformat() if value is not None else None


def _dec(value):
    return None if value is None else str(Decimal(str(value)))


def grading_status(job_status, evaluation_exists, result_status,
                   auto_status=None):
    if result_status == FINALIZED:
        return "RESULT_PUBLISHED"
    if evaluation_exists:
        if auto_status == "NEEDS_REVIEW":
            return "AUTO_GRADED_NEEDS_REVIEW"
        return "AUTO_GRADED_AWAITING_REVIEW"
    if job_status == "FAILED":
        return "NEEDS_MANUAL_GRADING"
    if job_status == "PROCESSING":
        return "GRADING"
    return "QUEUED"


# ----------------------------------------------------------------------
# Teacher
# ----------------------------------------------------------------------

LIST_SUBMISSIONS_SQL = """
SELECT
    s.submission_id, s.student_id, st.student_name, s.file_name,
    s.uploaded_at, s.submitted_at, a.deadline_at,
    j.job_status, j.attempt_count, j.last_error,
    CASE WHEN e.submission_id IS NULL THEN 0 ELSE 1 END,
    e.suggested_score, e.auto_evaluation_status, e.final_score,
    e.result_status, e.tests_passed, e.tests_run, sec.section_id
FROM dbo.Assessment AS a
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_id = a.assessment_id
INNER JOIN dbo.Submission AS s
    ON s.assessment_section_id = sec.assessment_section_id
LEFT JOIN dbo.Student_Export AS st
    ON st.student_id COLLATE DATABASE_DEFAULT
       = s.student_id COLLATE DATABASE_DEFAULT
LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id
LEFT JOIN dbo.Evaluation AS e ON e.submission_id = s.submission_id
WHERE a.assessment_id = ? AND a.teacher_id = ?
ORDER BY s.submitted_at, s.submission_id;
"""


def list_assessment_submissions(assessment_id, teacher_id,
                                connection_factory=get_connection):
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM dbo.Assessment "
            "WHERE assessment_id = ? AND teacher_id = ?",
            (assessment_id, teacher_id)
        )
        if cursor.fetchone() is None:
            raise NotFoundError("Assessment not found")

        cursor.execute(LIST_SUBMISSIONS_SQL, (assessment_id, teacher_id))
        items = []

        for r in cursor.fetchall():
            submitted = r[5] or r[4]
            items.append({
                "submission_id": int(r[0]),
                "student_id": r[1],
                "student_name": r[2],
                "file_name": r[3],
                "submitted_at": _iso(submitted),
                "is_late": bool(submitted and r[6] and submitted > r[6]),
                "section_id": r[17],
                "job_status": r[7],
                "attempt_count": r[8],
                "last_error": sanitize_compiler_diagnostics(r[9]) if r[9] else r[9],
                "grading_status": grading_status(
                    r[7], bool(r[10]), r[14], r[12]),
                "suggested_score": _dec(r[11]),
                "auto_evaluation_status": r[12],
                "final_score": _dec(r[13]),
                "result_status": r[14],
                "tests_passed": r[15],
                "tests_run": r[16]
            })

        return items
    finally:
        conn.rollback()
        conn.close()


TEACHER_SUBMISSION_SQL = """
SELECT
    s.submission_id, s.student_id, s.file_name, s.source_code,
    s.uploaded_at, s.submitted_at,
    a.assessment_id, a.title, a.total_marks, a.validation_percentage,
    a.deadline_at, a.allow_late, a.late_penalty_marks,
    j.job_status, j.attempt_count, j.last_error
FROM dbo.Submission AS s
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a ON a.assessment_id = sec.assessment_id
LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id
WHERE s.submission_id = ? AND a.teacher_id = ?;
"""

EVALUATION_SQL = """
SELECT suggested_score, auto_evaluation_status, auto_evaluation_feedback,
       final_score, result_status, instructor_feedback,
       tests_passed, tests_run
FROM dbo.Evaluation
WHERE submission_id = ?;
"""

CRITERIA_SQL = """
SELECT criterion_name, rating, explanation
FROM dbo.EvaluationCriterion
WHERE submission_id = ?
ORDER BY evaluation_criterion_id;
"""


def _criteria(cursor, submission_id):
    cursor.execute(CRITERIA_SQL, (submission_id,))
    return [
        {"criterion_name": r[0], "rating": _dec(r[1]), "explanation": r[2]}
        for r in cursor.fetchall()
    ]


# ----------------------------------------------------------------------
# Teacher-approved criterion marks (dbo.EvaluationCriterionApproval)
# ----------------------------------------------------------------------

APPROVAL_TABLE = "EvaluationCriterionApproval"

APPROVAL_TABLE_EXISTS_SQL = """
SELECT CASE WHEN OBJECT_ID(N'dbo.EvaluationCriterionApproval', N'U')
            IS NULL THEN 0 ELSE 1 END;
"""

APPROVED_CRITERIA_SQL = """
SELECT criterion_name, approved_marks, teacher_comment, approved_by, approved_at
FROM dbo.EvaluationCriterionApproval
WHERE submission_id = ?
ORDER BY approval_id;
"""

INSERT_APPROVAL_SQL = """
INSERT INTO dbo.EvaluationCriterionApproval
    (submission_id, criterion_name, approved_marks, teacher_comment, approved_by)
VALUES (?, ?, ?, ?, ?);
"""

MAX_CRITERION_COMMENT = 1000


def approval_table_exists(cursor):
    cursor.execute(APPROVAL_TABLE_EXISTS_SQL)
    row = cursor.fetchone()
    return bool(row and row[0])


def _approved_criteria(cursor, submission_id):
    """Teacher-approved criterion marks, or [] (also when no table yet)."""
    if not approval_table_exists(cursor):
        return []
    cursor.execute(APPROVED_CRITERIA_SQL, (submission_id,))
    return [{"criterion_name": r[0], "approved_marks": _dec(r[1]),
             "teacher_comment": r[2], "approved_by": r[3],
             "approved_at": _iso(r[4])} for r in cursor.fetchall()]


def normalize_criterion_approvals(criteria, rubric_rows):
    """
    Validate a teacher's criterion marks against the assessment rubric:
    every rubric criterion exactly once, 0..max_marks, 2 decimal places.
    Returns [(criterion_name, Decimal marks, comment)] in rubric order.
    """
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("criteria must be a non-empty list")
    if not rubric_rows:
        raise ValueError("This assessment has no rubric criteria to approve")
    rubric = {r["criterion_name"].casefold(): r for r in rubric_rows}
    seen = {}
    for item in criteria:
        if not isinstance(item, dict) or \
                set(item) - {"criterion_name", "marks", "comment"}:
            raise ValueError("Each criterion needs only criterion_name, marks "
                             "and an optional comment")
        name = item.get("criterion_name")
        if not isinstance(name, str) or name.strip().casefold() not in rubric:
            raise ValueError(f"Unknown rubric criterion: {name!r}")
        key = name.strip().casefold()
        if key in seen:
            raise ValueError(f"Duplicate criterion: {name}")
        marks = item.get("marks")
        if isinstance(marks, bool) or marks is None:
            raise ValueError(f"{name}: marks must be a number")
        try:
            value = Decimal(str(marks))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{name}: marks must be a number")
        maximum = Decimal(str(rubric[key]["max_marks"]))
        if not value.is_finite() or value != value.quantize(Decimal("0.01")) \
                or not Decimal("0") <= value <= maximum:
            raise ValueError(f"{name}: marks must be between 0 and {maximum} "
                             "with at most 2 decimal places")
        comment = item.get("comment")
        if comment is not None:
            if not isinstance(comment, str):
                raise ValueError(f"{name}: comment must be text")
            comment = comment.strip()[:MAX_CRITERION_COMMENT] or None
        seen[key] = (rubric[key]["criterion_name"], value, comment)
    missing = [r["criterion_name"] for k, r in rubric.items() if k not in seen]
    if missing:
        raise ValueError("Missing rubric criteria: " + ", ".join(missing))
    return [seen[r["criterion_name"].casefold()] for r in rubric_rows]


def find_hidden_values(text, hidden_rows):
    """Hidden inputs (3+ chars) / expected outputs (6+ chars) found in text."""
    if not text:
        return []
    found = []
    for row in hidden_rows:
        for value, minimum in ((row[0], 3), (row[1] if len(row) > 1 else None,
                                             MIN_REDACTED_OUTPUT_CHARS)):
            value = " ".join(str(value or "").split())
            if len(value) >= minimum and re.search(
                    r"(?<![\w.])" + r"\s+".join(map(re.escape, value.split()))
                    + r"(?!\w|\.\d)", text):
                found.append(value)
    return found


def get_teacher_submission_evaluation(submission_id, teacher_id,
                                      connection_factory=get_connection):
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(TEACHER_SUBMISSION_SQL, (submission_id, teacher_id))
        s = cursor.fetchone()

        if s is None:
            raise NotFoundError("Submission not found")

        cursor.execute(EVALUATION_SQL, (submission_id,))
        e = cursor.fetchone()
        submitted = s[5] or s[4]

        evaluation = None
        if e is not None:
            evaluation = {
                "suggested_score": _dec(e[0]),
                "auto_evaluation_status": e[1],
                "auto_evaluation_feedback": sanitize_compiler_diagnostics(e[2])
                if e[2] else e[2],
                "final_score": _dec(e[3]),
                "result_status": e[4],
                "instructor_feedback": e[5],
                "tests_passed": e[6],
                "tests_run": e[7],
                # Backward-compatible alias of ai_criteria.
                "criteria": _criteria(cursor, submission_id)
            }
            from manual_grading_service import parse_manual_record
            # Teacher-only audit record of a manual-grading fallback.
            evaluation["manual_grading"] = parse_manual_record(e[2])
            evaluation["ai_criteria"] = evaluation["criteria"]
            evaluation["teacher_criteria"] = _approved_criteria(cursor,
                                                                submission_id)
            evaluation["criterion_approval_available"] = \
                approval_table_exists(cursor)
            test_marks, _ = verified_test_marks(
                e[1], e[6], e[7], s[8], s[9])
            evaluation["verified_test_marks"] = (None if test_marks is None
                                                 else str(test_marks))

        return {
            "submission_id": int(s[0]),
            "student_id": s[1],
            "file_name": s[2],
            "source_code": s[3],
            "submitted_at": _iso(submitted),
            "is_late": bool(submitted and s[10] and submitted > s[10]),
            "assessment": {
                "assessment_id": int(s[6]),
                "title": s[7],
                "total_marks": _dec(s[8]),
                "validation_percentage": _dec(s[9]),
                "allow_late": bool(s[11]),
                "late_penalty_marks": _dec(s[12])
            },
            "job": {
                "job_status": s[13],
                "attempt_count": s[14],
                "last_error": sanitize_compiler_diagnostics(s[15]) if s[15] else s[15]
            },
            "grading_status": grading_status(
                s[13], e is not None, e[4] if e else None,
                e[1] if e else None),
            "evaluation": evaluation
        }
    finally:
        conn.rollback()
        conn.close()


LOCK_FOR_FINALIZE_SQL = """
SELECT a.total_marks, j.job_status, a.assessment_id, a.validation_percentage
FROM dbo.Submission AS s WITH (UPDLOCK, HOLDLOCK)
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a ON a.assessment_id = sec.assessment_id
LEFT JOIN dbo.GradingJob AS j WITH (UPDLOCK, HOLDLOCK)
    ON j.submission_id = s.submission_id
WHERE s.submission_id = ? AND a.teacher_id = ?;
"""

LOCK_EVALUATION_SQL = """
SELECT result_status, auto_evaluation_status, tests_passed, tests_run
FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)
WHERE submission_id = ?;
"""

FINALIZE_SQL = """
UPDATE dbo.Evaluation
SET final_score = ?, instructor_feedback = ?, result_status = 'FINALIZED'
WHERE submission_id = ?
  AND (result_status IS NULL OR result_status <> 'FINALIZED');
"""

MANUAL_INSERT_SQL = """
INSERT INTO dbo.Evaluation (
    submission_id, suggested_score, auto_evaluation_status,
    auto_evaluation_feedback, final_score, result_status,
    instructor_feedback, tests_passed, tests_run
)
VALUES (?, NULL, 'MANUAL_GRADING', ?, ?, 'FINALIZED', ?, 0, 0);
"""


def validate_final_score(value, total_marks):
    if isinstance(value, bool) or value is None:
        raise ValueError("final_score must be a number")
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("final_score must be a number")
    if not score.is_finite():
        raise ValueError("final_score must be finite")
    if score != score.quantize(Decimal("0.01")):
        raise ValueError("final_score allows at most 2 decimal places")
    total = Decimal(str(total_marks))
    if not Decimal("0") <= score <= total:
        raise ValueError(f"final_score must be between 0 and {total}")
    return score


def finalize_evaluation(submission_id, teacher_id, final_score,
                        instructor_feedback=None,
                        connection_factory=get_connection, criteria=None):
    """
    Teacher finalization (one transaction, never repeated).

    criteria (optional): teacher-approved criterion marks. They must cover
    every rubric criterion, respect each maximum, and
        verified test marks + sum(criteria) == final_score.
    The AI's EvaluationCriterion rows are never modified (audit trail).
    Requests without criteria behave exactly as before.
    """
    if instructor_feedback is not None:
        if not isinstance(instructor_feedback, str):
            raise ValueError("instructor_feedback must be text")
        instructor_feedback = instructor_feedback.strip()[:MAX_FEEDBACK] or None

    conn = connection_factory()
    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute(LOCK_FOR_FINALIZE_SQL, (submission_id, teacher_id))
        row = cursor.fetchone()
        if row is None:
            raise NotFoundError("Submission not found")

        total_marks, job_status, assessment_id, validation = row
        score = validate_final_score(final_score, total_marks)

        cursor.execute(LOCK_EVALUATION_SQL, (submission_id,))
        existing = cursor.fetchone()
        if existing is not None and existing[0] == FINALIZED:
            raise ConflictError("Evaluation is already finalized")
        if existing is None and job_status != "FAILED":
            raise ConflictError(
                "Automatic grading has not finished for this submission")

        # Teacher text reaches the student: it must not quote hidden tests.
        if instructor_feedback:
            cursor.execute(HIDDEN_INPUTS_SQL, (assessment_id,))
            if find_hidden_values(instructor_feedback, cursor.fetchall()):
                raise ValueError("instructor_feedback must not contain hidden "
                                 "test inputs or expected outputs")

        approvals = []
        if criteria is not None:
            if not approval_table_exists(cursor):
                raise ConflictError(
                    "Criterion-level approval is not available until the "
                    "EvaluationCriterionApproval migration is applied")
            from assessment_rubric_service import load_rubric_rows
            approvals = normalize_criterion_approvals(
                criteria, load_rubric_rows(cursor, assessment_id))
            auto_status = existing[1] if existing else "MANUAL_GRADING"
            test_marks, _ = verified_test_marks(
                auto_status, existing[2] if existing else 0,
                existing[3] if existing else 0, total_marks, validation)
            test_marks = test_marks or Decimal("0.00")
            expected = test_marks + sum(a[1] for a in approvals)
            if expected != score:
                raise ValueError(
                    f"final_score {score} must equal verified test marks "
                    f"{test_marks} + approved rubric marks "
                    f"{sum(a[1] for a in approvals)} = {expected}")

        if existing is not None:
            cursor.execute(FINALIZE_SQL,
                           (score, instructor_feedback, submission_id))
            if cursor.rowcount != 1:
                raise ConflictError("Evaluation changed concurrently")
            mode = "REVIEWED_AUTOMATIC_GRADE"
        else:
            cursor.execute(MANUAL_INSERT_SQL, (
                submission_id,
                "Automatic grading failed due to an infrastructure "
                "problem; graded manually by the teacher.",
                score, instructor_feedback
            ))
            mode = "MANUAL_GRADE"

        for name, marks, comment in approvals:
            cursor.execute(INSERT_APPROVAL_SQL,
                           (submission_id, name, marks, comment, teacher_id))
            if cursor.rowcount != 1:
                raise ConflictError("Criterion approval was not saved")

        conn.commit()
        return {
            "submission_id": submission_id,
            "final_score": str(score),
            "result_status": FINALIZED,
            "mode": mode,
            "criteria_approved": len(approvals)
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Student
# ----------------------------------------------------------------------

STUDENT_ASSESSMENTS_SQL = """
SELECT DISTINCT
    a.assessment_id, a.course_code, a.assessment_type, a.title,
    a.problem_statement, a.input_format, a.output_format,
    a.total_marks, a.deadline_at, a.allow_late, a.late_penalty_marks,
    a.validation_percentage
FROM dbo.Assessment AS a
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_id = a.assessment_id
INNER JOIN dbo.Enrollment_Export AS e
    ON e.section_id COLLATE DATABASE_DEFAULT
       = sec.section_id COLLATE DATABASE_DEFAULT
   AND e.course_no COLLATE DATABASE_DEFAULT
       = a.course_code COLLATE DATABASE_DEFAULT
WHERE e.student_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
  -- Same rule as student_submission_service: the assessment's teacher
  -- must teach this course in this section.
  AND EXISTS (
      SELECT 1 FROM dbo.SectionCourseTeacher AS sct
      WHERE sct.section_id COLLATE DATABASE_DEFAULT
            = sec.section_id COLLATE DATABASE_DEFAULT
        AND sct.course_no COLLATE DATABASE_DEFAULT
            = a.course_code COLLATE DATABASE_DEFAULT
        AND sct.teacher_id COLLATE DATABASE_DEFAULT
            = a.teacher_id COLLATE DATABASE_DEFAULT
  )
ORDER BY a.deadline_at;
"""

RUBRIC_TOTALS_SQL = """
SELECT assessment_id, SUM(max_marks)
FROM dbo.AssessmentRubricCriterion
GROUP BY assessment_id;
"""

STUDENT_SUBMISSION_SQL = """
SELECT s.submission_id, s.file_name, s.uploaded_at, s.submitted_at,
       j.job_status,
       CASE WHEN e.submission_id IS NULL THEN 0 ELSE 1 END,
       e.result_status, e.final_score, e.instructor_feedback,
       e.auto_evaluation_feedback, e.tests_passed, e.tests_run,
       a.total_marks, e.auto_evaluation_status, a.validation_percentage,
       a.assessment_id
FROM dbo.Submission AS s
INNER JOIN dbo.AssessmentSection AS sec
    ON sec.assessment_section_id = s.assessment_section_id
INNER JOIN dbo.Assessment AS a ON a.assessment_id = sec.assessment_id
LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id
LEFT JOIN dbo.Evaluation AS e ON e.submission_id = s.submission_id
WHERE sec.assessment_id = ? AND s.student_id = ?;
"""


HIDDEN_INPUTS_SQL = """
SELECT input_data, expected_output
FROM dbo.TestCase
WHERE assessment_id = ? AND visibility = 'hidden';
"""

REDACTED = "[hidden test input]"
AI_SECTION_MARKER = "=== AI rubric assessment"
TEACHER_ONLY_MARKER = "=== Python verification report (teacher only) ==="
# Expected outputs shorter than this (YES/NO, single numbers) are part of
# the public output format; redacting them would destroy ordinary words.
MIN_REDACTED_OUTPUT_CHARS = 6


def hidden_values(rows):
    """Hidden inputs, plus hidden expected outputs long enough to matter."""
    values = []
    for row in rows:
        values.append(row[0])
        if len(row) > 1 and row[1] and len(str(row[1]).strip()) >= \
                MIN_REDACTED_OUTPUT_CHARS:
            values.append(str(row[1]).strip())
    return values


def student_safe_feedback(feedback, hidden):
    """
    Drop the teacher-only verification section, strip host paths (stored
    text is already sanitized; this protects older rows), redact the AI
    section.
    """
    if not feedback:
        return feedback
    feedback = feedback.split(TEACHER_ONLY_MARKER, 1)[0].rstrip()
    feedback = sanitize_compiler_diagnostics(feedback)
    return redact_ai_section(feedback, hidden)


def redact_hidden_inputs(text, hidden_inputs):
    """
    Replace whole-value occurrences of hidden test inputs. The AI grader
    sees hidden inputs, so its wording may quote them; students must not.
    Numbers that are part of a larger token (2.00, 994009x) are kept.
    """
    if not text:
        return text
    for value in sorted({" ".join(str(v).split()) for v in hidden_inputs
                         if v and str(v).strip()}, key=len, reverse=True):
        pattern = r"(?<![\w.])" + r"\s+".join(map(re.escape, value.split())) \
            + r"(?!\w|\.\d)"
        text = re.sub(pattern, REDACTED, text)
    return text


def redact_ai_section(feedback, hidden_inputs):
    """Redact only the AI section; deterministic counts stay readable."""
    if not feedback or AI_SECTION_MARKER not in feedback:
        return feedback
    head, _, tail = feedback.partition(AI_SECTION_MARKER)
    return head + AI_SECTION_MARKER + redact_hidden_inputs(tail, hidden_inputs)


def list_student_assessments(student_id, connection_factory=get_connection):
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(STUDENT_ASSESSMENTS_SQL, (student_id,))
        rows = cursor.fetchall()

        cursor.execute("""
            SELECT sec.assessment_id
            FROM dbo.Submission AS s
            INNER JOIN dbo.AssessmentSection AS sec
                ON sec.assessment_section_id = s.assessment_section_id
            WHERE s.student_id = ?
        """, (student_id,))
        submitted = {int(r[0]) for r in cursor.fetchall()}

        from assessment_rubric_service import rubric_table_exists
        from grading_allocation import split_allocation

        rubric_enforced = rubric_table_exists(cursor)
        rubric_totals = {}
        if rubric_enforced:
            cursor.execute(RUBRIC_TOTALS_SQL)
            rubric_totals = {int(r[0]): Decimal(str(r[1]))
                             for r in cursor.fetchall()}

        cursor.execute("SELECT SYSDATETIME()")
        now = cursor.fetchone()[0]

        items = []
        for r in rows:
            assessment_id = int(r[0])
            _, rubric_marks = split_allocation(r[7], r[11])
            rubric_ready = (
                not rubric_enforced
                or rubric_marks == 0
                or rubric_totals.get(assessment_id) == rubric_marks
            )
            deadline_open = r[8] is None or now <= r[8] or bool(r[9])
            has_submitted = assessment_id in submitted

            items.append({
                "assessment_id": assessment_id,
                "course_code": r[1],
                "assessment_type": r[2],
                "title": r[3],
                "problem_statement": r[4],
                "input_format": r[5],
                "output_format": r[6],
                "total_marks": _dec(r[7]),
                "deadline_at": _iso(r[8]),
                "allow_late": bool(r[9]),
                "late_penalty_marks": _dec(r[10]),
                "has_submitted": has_submitted,
                "open_for_submissions": (
                    rubric_ready and deadline_open and not has_submitted
                )
            })
        return items
    finally:
        conn.rollback()
        conn.close()


def get_student_submission(assessment_id, student_id,
                           connection_factory=get_connection):
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute(STUDENT_SUBMISSION_SQL, (assessment_id, student_id))
        r = cursor.fetchone()

        if r is None:
            raise NotFoundError("No submission found")

        status = grading_status(r[4], bool(r[5]), r[6], r[13])

        # Students never see suggested scores or AI-only output.
        # Pending/in-review states collapse to one student-facing value.
        student_status = {
            "QUEUED": "SUBMITTED",
            "GRADING": "GRADING",
            "AUTO_GRADED_AWAITING_REVIEW": "UNDER_REVIEW",
            "AUTO_GRADED_NEEDS_REVIEW": "UNDER_REVIEW",
            "NEEDS_MANUAL_GRADING": "UNDER_REVIEW",
            "RESULT_PUBLISHED": "RESULT_PUBLISHED"
        }[status]

        result = None
        if status == "RESULT_PUBLISHED":
            # Built ONLY from permitted fields; the stored automatic
            # feedback (AI text, suggested total, reliability flags,
            # teacher-only report, provenance) is never forwarded.
            from assessment_rubric_service import (RubricStorageUnavailable,
                                                   load_rubric_rows)
            try:
                rubric_rows = load_rubric_rows(cursor, int(r[15]))
            except RubricStorageUnavailable:
                rubric_rows = []
            result = build_student_result(
                final_score=r[7], total_marks=r[12], validation_percentage=r[14],
                auto_status=r[13], tests_passed=r[10], tests_run=r[11],
                instructor_feedback=r[8], stored_feedback=r[9],
                rubric_rows=rubric_rows,
                ai_criteria=_criteria(cursor, int(r[0])),
                approved_criteria=_approved_criteria(cursor, int(r[0])))

        return {
            "submission_id": int(r[0]),
            "assessment_id": assessment_id,
            "file_name": r[1],
            "submitted_at": _iso(r[3] or r[2]),
            "status": student_status,
            "result": result
        }
    finally:
        conn.rollback()
        conn.close()
