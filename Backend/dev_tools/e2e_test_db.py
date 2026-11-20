"""
DEVELOPMENT-ONLY isolated database for the end-to-end student test.

    python dev_tools/e2e_test_db.py plan                         read-only
    python dev_tools/e2e_test_db.py fingerprint <out.json>       read-only
    python dev_tools/e2e_test_db.py create --confirm FYP_E2E_Test
    python dev_tools/e2e_test_db.py seed   --confirm FYP_E2E_Test
    python dev_tools/e2e_test_db.py verify [--baseline <out.json>]   read-only
    python dev_tools/e2e_test_db.py drop   --confirm FYP_E2E_Test

FYP_E2E_Test is a schema-only copy of FYP_Database (DBCC CLONEDATABASE ...
WITH NO_STATISTICS, NO_QUERYSTORE: same tables, keys, foreign keys, checks,
defaults, identities and collations; no rows, no statistics histograms).
It is then seeded with ONLY:
  - the reference rows Assessment 5 needs (teacher BIIT235 with a TEST-ONLY
    password, course CSC-101, section SEC000001, its SectionCourseTeacher
    row, Assessment 5, its rubric and its 10 test cases), copied from
    FYP_Database by INSERT ... SELECT (values never pass through Python),
  - three test students E2E-TEST-0001..0003 enrolled in SEC000001/CSC-101.

FYP_Database receives no writes at any step: it is only read (three-part
SELECTs and the clone's internal snapshot). No real student exists in the
test database, so none of the 47 SEC000001 students can see or reach the
test assessment. Passwords are typed at hidden prompts, sent as SQL
parameters, and never printed, logged or stored in files.
"""

import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402

SOURCE = db.DEFAULT_DATABASE
TARGET = db.E2E_TEST_DATABASE
TEACHER_ID, COURSE, SECTION, ASSESSMENT_ID = "BIIT235", "CSC-101", "SEC000001", 5

TEST_STUDENTS = [
    {"student_id": f"E2E-TEST-000{n}", "student_name": f"E2E Test Student {n}",
     "student_email": f"e2e.test{n}@e2e.test", "enrollment_id": f"E2E-ENR-000{n}"}
    for n in (1, 2, 3)]

# Tables FYP_Database must keep unchanged (fingerprinted before/after).
FINGERPRINT_SQL = {
    "Assessment": "SELECT * FROM dbo.Assessment ORDER BY assessment_id",
    "AssessmentSection": "SELECT * FROM dbo.AssessmentSection ORDER BY assessment_section_id",
    "AssessmentRubricCriterion": "SELECT * FROM dbo.AssessmentRubricCriterion ORDER BY rubric_criterion_id",
    "TestCase": "SELECT * FROM dbo.TestCase ORDER BY test_case_id",
    "ReferenceSolution": "SELECT * FROM dbo.ReferenceSolution ORDER BY reference_solution_id",
    "Submission": "SELECT * FROM dbo.Submission ORDER BY submission_id",
    "GradingJob": "SELECT * FROM dbo.GradingJob ORDER BY job_id",
    "Evaluation": "SELECT * FROM dbo.Evaluation ORDER BY submission_id",
    "EvaluationCriterion": "SELECT * FROM dbo.EvaluationCriterion ORDER BY evaluation_criterion_id",
    "Notification": "SELECT * FROM dbo.Notification ORDER BY notification_id",
    "SectionCourseTeacher": "SELECT * FROM dbo.SectionCourseTeacher ORDER BY allocation_id",
    "Section_Export": "SELECT * FROM dbo.Section_Export ORDER BY section_id",
    "Course_Export": "SELECT course_no, title FROM dbo.Course_Export ORDER BY course_no",
    "Enrollment_Export": "SELECT * FROM dbo.Enrollment_Export ORDER BY enrollment_id",
    # Hash includes passwords so a change is detected; values never leave
    # this process and only the digest is written.
    "Student_Export": "SELECT * FROM dbo.Student_Export ORDER BY student_id",
    "Teacher_Export": "SELECT * FROM dbo.Teacher_Export ORDER BY teacher_id",
}

# ----------------------------------------------------------------------
# Exact SQL (shown by `plan`; executed only by `create` / `seed` / `drop`)
# ----------------------------------------------------------------------

CREATE_STATEMENTS = [
    f"DBCC CLONEDATABASE ({SOURCE}, {TARGET}) WITH NO_STATISTICS, NO_QUERYSTORE;",
    f"ALTER DATABASE [{TARGET}] SET READ_WRITE WITH ROLLBACK IMMEDIATE;",
    f"ALTER DATABASE [{TARGET}] SET RECOVERY SIMPLE;",
]

DROP_STATEMENTS = [
    f"ALTER DATABASE [{TARGET}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;",
    f"DROP DATABASE [{TARGET}];",
]

S = f"{SOURCE}.dbo"
# (label, sql, params-key, expected rowcount, identity table or None).
# "?" values are bound parameters; the teacher's test-only password is
# bound from a hidden prompt. Identity tables are wrapped in separate
# SET IDENTITY_INSERT ON / OFF statements on the same session so that
# the INSERT's row count is exact and the original ids are preserved.
SEED_STEPS = [
    ("Teacher_Export BIIT235 (test-only password)",
     f"INSERT INTO dbo.Teacher_Export (teacher_id, teacher_name, teacher_email, "
     f"teacher_password, department) SELECT teacher_id, teacher_name, "
     f"teacher_email, ?, department FROM {S}.Teacher_Export WHERE teacher_id = ?;",
     "teacher", 1, None),
    ("Course_Export CSC-101",
     f"INSERT INTO dbo.Course_Export (course_no, course_desc, title) SELECT "
     f"course_no, course_desc, title FROM {S}.Course_Export WHERE course_no = ?;",
     "course", 1, None),
    ("Section_Export SEC000001",
     f"INSERT INTO dbo.Section_Export (section_id, section_name, semester_no, "
     f"program, academic_session) SELECT section_id, section_name, semester_no, "
     f"program, academic_session FROM {S}.Section_Export WHERE section_id = ?;",
     "section", 1, None),
    ("SectionCourseTeacher (SEC000001, BIIT235, CSC-101)",
     f"INSERT INTO dbo.SectionCourseTeacher (section_id, teacher_id, course_no, "
     f"actual_semester) SELECT section_id, teacher_id, course_no, actual_semester "
     f"FROM {S}.SectionCourseTeacher WHERE section_id = ? AND teacher_id = ? "
     f"AND course_no = ?;",
     "sct", 1, None),
    ("Assessment 5 (same assessment_id)",
     f"INSERT INTO dbo.Assessment (assessment_id, teacher_id, course_code, "
     f"assessment_type, title, problem_statement, input_format, output_format, "
     f"total_marks, deadline_at, allow_late, late_penalty_marks, value_constraints, "
     f"code_constraints, additional_constraints, validation_percentage) SELECT "
     f"assessment_id, teacher_id, course_code, assessment_type, title, "
     f"problem_statement, input_format, output_format, total_marks, deadline_at, "
     f"allow_late, late_penalty_marks, value_constraints, code_constraints, "
     f"additional_constraints, validation_percentage FROM {S}.Assessment "
     f"WHERE assessment_id = ?;",
     "assessment", 1, "Assessment"),
    ("AssessmentRubricCriterion (2 rows, same ids)",
     f"INSERT INTO dbo.AssessmentRubricCriterion (rubric_criterion_id, "
     f"assessment_id, criterion_name, max_marks, description, created_at) SELECT "
     f"rubric_criterion_id, assessment_id, criterion_name, max_marks, description, "
     f"created_at FROM {S}.AssessmentRubricCriterion WHERE assessment_id = ?;",
     "assessment", 2, "AssessmentRubricCriterion"),
    ("TestCase (10 hidden rows, same ids)",
     f"INSERT INTO dbo.TestCase (test_case_id, assessment_id, test_number, "
     f"test_name, category, visibility, input_data, expected_output, marks) SELECT "
     f"test_case_id, assessment_id, test_number, test_name, category, visibility, "
     f"input_data, expected_output, marks FROM {S}.TestCase WHERE assessment_id = ?;",
     "assessment", 10, "TestCase"),
]

INSERT_STUDENT_SQL = (
    "INSERT INTO dbo.Student_Export (student_id, student_name, student_email, "
    "program, semester, section, enrollment_status, student_password, section_id) "
    "VALUES (?, ?, ?, 'BSCS', '2025SM', 'A', 'ENROLLED', ?, ?);")
INSERT_ENROLLMENT_SQL = (
    "INSERT INTO dbo.Enrollment_Export (enrollment_id, student_id, section_id, "
    "course_no, enrollment_year, teacher_id) VALUES (?, ?, ?, ?, 2025, ?);")

EXPECTED_SEEDED_COUNTS = {
    "Teacher_Export": 1, "Course_Export": 1, "Section_Export": 1,
    "SectionCourseTeacher": 1, "Assessment": 1, "AssessmentRubricCriterion": 2,
    "TestCase": 10, "Student_Export": 3, "Enrollment_Export": 3}

# Same predicates as student_submission_service (minus AssessmentSection,
# which the teacher creates through the API during the run).
AUTHORIZATION_SQL = """
SELECT s.student_id,
       CASE WHEN s.student_email IS NOT NULL AND LEN(s.student_password) > 0
            THEN 1 ELSE 0 END,
       CASE WHEN EXISTS (
           SELECT 1 FROM dbo.Enrollment_Export e
           WHERE e.student_id COLLATE DATABASE_DEFAULT = s.student_id COLLATE DATABASE_DEFAULT
             AND e.section_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
             AND e.course_no COLLATE DATABASE_DEFAULT = a.course_code COLLATE DATABASE_DEFAULT)
            THEN 1 ELSE 0 END,
       CASE WHEN EXISTS (
           SELECT 1 FROM dbo.SectionCourseTeacher sct
           WHERE sct.section_id COLLATE DATABASE_DEFAULT = ? COLLATE DATABASE_DEFAULT
             AND sct.course_no COLLATE DATABASE_DEFAULT = a.course_code COLLATE DATABASE_DEFAULT
             AND sct.teacher_id COLLATE DATABASE_DEFAULT = a.teacher_id COLLATE DATABASE_DEFAULT)
            THEN 1 ELSE 0 END
FROM dbo.Student_Export s
CROSS JOIN dbo.Assessment a
WHERE a.assessment_id = ? AND s.student_id LIKE 'E2E-TEST-%'
ORDER BY s.student_id;
"""


class Refused(RuntimeError):
    pass


def connect(database, autocommit=False):
    conn = pyodbc.connect(db.connection_string(database))
    conn.autocommit = autocommit
    return conn


def require_confirm(value):
    if value != TARGET:
        raise Refused(f"--confirm {TARGET} is required")


# ----------------------------------------------------------------------
# Read-only helpers
# ----------------------------------------------------------------------

def database_exists(cursor, name=TARGET):
    cursor.execute("SELECT DB_ID(?)", (name,))
    return cursor.fetchone()[0] is not None


def table_counts(cursor):
    """
    Real row counts (COUNT_BIG per table). sys.partitions is NOT used: a
    DBCC CLONEDATABASE copy keeps the source's row-count metadata there
    even though its tables contain no rows.
    """
    cursor.execute("SELECT s.name, t.name FROM sys.tables t JOIN sys.schemas s "
                   "ON s.schema_id = t.schema_id WHERE t.is_ms_shipped = 0 "
                   "ORDER BY t.name")
    tables = [(r[0], r[1]) for r in cursor.fetchall()]
    counts = {}
    for schema, name in tables:
        cursor.execute(f"SELECT COUNT_BIG(*) FROM [{schema}].[{name}]")
        counts[name] = int(cursor.fetchone()[0])
    return counts


def fingerprint(cursor):
    """sha256 per table over every row of the protected tables."""
    result = {}
    for table, sql in FINGERPRINT_SQL.items():
        cursor.execute(sql)
        digest = hashlib.sha256()
        count = 0
        for row in cursor.fetchall():
            digest.update(repr(tuple(row)).encode("utf-8"))
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def source_preconditions(cursor):
    checks = {
        "teacher BIIT235": ("SELECT COUNT(*) FROM dbo.Teacher_Export WHERE teacher_id = ?", (TEACHER_ID,), 1),
        "course CSC-101": ("SELECT COUNT(*) FROM dbo.Course_Export WHERE course_no = ?", (COURSE,), 1),
        "section SEC000001": ("SELECT COUNT(*) FROM dbo.Section_Export WHERE section_id = ?", (SECTION,), 1),
        "SCT row": ("SELECT COUNT(*) FROM dbo.SectionCourseTeacher WHERE section_id = ? "
                    "AND teacher_id = ? AND course_no = ?", (SECTION, TEACHER_ID, COURSE), 1),
        "assessment 5 owned by BIIT235/CSC-101": (
            "SELECT COUNT(*) FROM dbo.Assessment WHERE assessment_id = ? AND teacher_id = ? "
            "AND course_code = ?", (ASSESSMENT_ID, TEACHER_ID, COURSE), 1),
        "rubric rows": ("SELECT COUNT(*) FROM dbo.AssessmentRubricCriterion WHERE assessment_id = ?",
                        (ASSESSMENT_ID,), 2),
        "test cases": ("SELECT COUNT(*) FROM dbo.TestCase WHERE assessment_id = ?", (ASSESSMENT_ID,), 10),
        "no E2E students in real DB": ("SELECT COUNT(*) FROM dbo.Student_Export "
                                       "WHERE student_id LIKE 'E2E-%'", (), 0),
    }
    results = {}
    for name, (sql, params, expected) in checks.items():
        cursor.execute(sql, params)
        actual = int(cursor.fetchone()[0])
        results[name] = {"expected": expected, "actual": actual, "ok": actual == expected}
    return results


# ----------------------------------------------------------------------
# Write operations (each requires --confirm FYP_E2E_Test)
# ----------------------------------------------------------------------

def create(confirm, connect_fn=connect):
    require_confirm(confirm)
    master = connect_fn("master", autocommit=True)
    try:
        cur = master.cursor()
        if database_exists(cur):
            raise Refused(f"{TARGET} already exists; drop it first")
        for statement in CREATE_STATEMENTS:
            cur.execute(statement)
            while cur.nextset():              # drain DBCC informational sets
                pass
    finally:
        master.close()
    return check_clone(connect_fn)


def check_clone(connect_fn=connect):
    """Read-only: the clone exists, is writable and every table is empty."""
    target = connect_fn(TARGET)
    try:
        cur = target.cursor()
        cur.execute("SELECT DB_NAME(), DATABASEPROPERTYEX(DB_NAME(), 'Updateability')")
        name, updateability = cur.fetchone()
        counts = table_counts(cur)
    finally:
        target.rollback()
        target.close()
    if name != TARGET or updateability != "READ_WRITE":
        raise Refused(f"Clone is not usable: {name} {updateability}")
    non_empty = {t: n for t, n in counts.items() if n}
    if non_empty:
        raise Refused(f"Clone unexpectedly contains rows: {non_empty}")
    return {"database": name, "updateability": updateability,
            "tables": len(counts), "all_tables_empty": True}


def prompt_password(label, max_length, prompt=getpass.getpass):
    first = prompt(f"{label} password (hidden): ")
    second = prompt(f"Repeat {label} password (hidden): ")
    if first != second:
        raise Refused(f"{label}: passwords do not match")
    if not first or len(first) > max_length:
        raise Refused(f"{label}: password must be 1-{max_length} characters")
    return first


def collect_passwords(prompt=getpass.getpass):
    return {"teacher": prompt_password(f"Test-database password for {TEACHER_ID}",
                                       255, prompt),
            **{s["student_id"]: prompt_password(s["student_id"], 100, prompt)
               for s in TEST_STUDENTS}}


def seed(confirm, passwords, connect_fn=connect):
    """One transaction; any unexpected row count rolls everything back."""
    require_confirm(confirm)
    params = {"teacher": None, "course": (COURSE,), "section": (SECTION,),
              "sct": (SECTION, TEACHER_ID, COURSE), "assessment": (ASSESSMENT_ID,)}
    conn = connect_fn(TARGET)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DB_NAME()")
        if cur.fetchone()[0] != TARGET:
            raise Refused("Seed connection is not the test database")
        counts = table_counts(cur)
        if any(counts.values()):
            raise Refused("Test database is not empty; drop and recreate it")

        applied = []
        for label, sql, key, expected, identity_table in SEED_STEPS:
            bound = ((passwords["teacher"], TEACHER_ID) if key == "teacher"
                     else params[key])
            if identity_table:
                cur.execute(f"SET IDENTITY_INSERT dbo.{identity_table} ON;")
            cur.execute(sql, bound)
            rowcount = cur.rowcount
            if identity_table:
                cur.execute(f"SET IDENTITY_INSERT dbo.{identity_table} OFF;")
            if rowcount != expected:
                raise Refused(f"{label}: expected {expected} row(s), got {rowcount}")
            applied.append((label, rowcount))

        for s in TEST_STUDENTS:
            cur.execute(INSERT_STUDENT_SQL, (s["student_id"], s["student_name"],
                                             s["student_email"],
                                             passwords[s["student_id"]], SECTION))
            if cur.rowcount != 1:
                raise Refused(f"Student {s['student_id']} not inserted")
            cur.execute(INSERT_ENROLLMENT_SQL, (s["enrollment_id"], s["student_id"],
                                                SECTION, COURSE, TEACHER_ID))
            if cur.rowcount != 1:
                raise Refused(f"Enrollment {s['enrollment_id']} not inserted")
            applied.append((f"{s['student_id']} + {s['enrollment_id']}", 2))

        conn.commit()
        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Copied rows that must be byte-identical to FYP_Database.
COPIED_ROWS_SQL = {
    "Assessment 5": "SELECT * FROM dbo.Assessment WHERE assessment_id = 5",
    "rubric": "SELECT * FROM dbo.AssessmentRubricCriterion WHERE assessment_id = 5 "
              "ORDER BY rubric_criterion_id",
    "test cases": "SELECT * FROM dbo.TestCase WHERE assessment_id = 5 ORDER BY test_case_id",
    "course": "SELECT course_no, course_desc, title FROM dbo.Course_Export "
              "WHERE course_no = 'CSC-101'",
    "section": "SELECT * FROM dbo.Section_Export WHERE section_id = 'SEC000001'",
    "teacher (excluding password)": "SELECT teacher_id, teacher_name, teacher_email, "
                                    "department FROM dbo.Teacher_Export "
                                    "WHERE teacher_id = 'BIIT235'",
}


def _digest(cursor, sql):
    cursor.execute(sql)
    return hashlib.sha256(repr([tuple(r) for r in cursor.fetchall()])
                          .encode("utf-8")).hexdigest()


def integrity_checks(cur):
    """Read-only FK, identity, login-readiness and prerequisite checks."""
    out = {}
    cur.execute("SELECT name FROM sys.foreign_keys WHERE is_disabled = 1 "
                "OR is_not_trusted = 1")
    out["untrusted_or_disabled_fks"] = [r[0] for r in cur.fetchall()]

    cur.execute("""
        SELECT fk.name, OBJECT_NAME(fk.parent_object_id),
               COL_NAME(fkc.parent_object_id, fkc.parent_column_id),
               OBJECT_NAME(fk.referenced_object_id),
               COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        ORDER BY fk.name""")
    orphans = {}
    for name, child, ccol, parent, pcol in cur.fetchall():
        cur.execute(f"SELECT COUNT(*) FROM dbo.[{child}] c WHERE c.[{ccol}] IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM dbo.[{parent}] p "
                    f"WHERE p.[{pcol}] = c.[{ccol}])")
        orphans[name] = int(cur.fetchone()[0])
    out["foreign_keys_checked"] = len(orphans)
    out["orphan_rows"] = {k: v for k, v in orphans.items() if v}

    cur.execute("""
        SELECT t.name, c.name, CAST(IDENT_CURRENT('dbo.' + t.name) AS bigint)
        FROM sys.identity_columns c JOIN sys.tables t ON t.object_id = c.object_id
        ORDER BY t.name""")
    identities = {}
    for table, column, current in cur.fetchall():
        cur.execute(f"SELECT MAX([{column}]) FROM dbo.[{table}]")
        maximum = cur.fetchone()[0]
        identities[table] = {"ident_current": current, "max_id": maximum,
                             "next_insert_safe": maximum is None or
                             (current is not None and current >= maximum)}
    out["identities"] = identities

    cur.execute("""
        SELECT s.student_id,
               CASE WHEN LEN(s.student_email) > 0 THEN 1 ELSE 0 END,
               (SELECT COUNT(*) FROM dbo.Student_Export x
                 WHERE x.student_email = s.student_email),
               CASE WHEN LEN(s.student_password) > 0 THEN 1 ELSE 0 END
        FROM dbo.Student_Export s ORDER BY s.student_id""")
    out["student_login_readiness"] = [
        {"student_id": r[0], "email_present": bool(r[1]),
         "email_unique": r[2] == 1, "password_set": bool(r[3])}
        for r in cur.fetchall()]
    cur.execute("""
        SELECT t.teacher_id, CASE WHEN LEN(t.teacher_email) > 0 THEN 1 ELSE 0 END,
               (SELECT COUNT(*) FROM dbo.Teacher_Export x
                 WHERE x.teacher_email = t.teacher_email),
               CASE WHEN LEN(t.teacher_password) > 0 THEN 1 ELSE 0 END
        FROM dbo.Teacher_Export t""")
    out["teacher_login_readiness"] = [
        {"teacher_id": r[0], "email_present": bool(r[1]),
         "email_unique": r[2] == 1, "password_set": bool(r[3])}
        for r in cur.fetchall()]

    cur.execute("""
        SELECT a.teacher_id, a.course_code, a.total_marks, a.validation_percentage,
               CASE WHEN a.deadline_at > SYSDATETIME() THEN 1 ELSE 0 END,
               (SELECT COUNT(*) FROM dbo.Course_Export c
                 WHERE c.course_no COLLATE DATABASE_DEFAULT
                       = a.course_code COLLATE DATABASE_DEFAULT),
               (SELECT SUM(max_marks) FROM dbo.AssessmentRubricCriterion r
                 WHERE r.assessment_id = a.assessment_id),
               (SELECT COUNT(*) FROM dbo.TestCase t WHERE t.assessment_id = a.assessment_id),
               (SELECT COUNT(*) FROM dbo.TestCase t WHERE t.assessment_id = a.assessment_id
                  AND t.visibility = 'hidden'),
               (SELECT MIN(test_number) FROM dbo.TestCase t
                 WHERE t.assessment_id = a.assessment_id),
               (SELECT MAX(test_number) FROM dbo.TestCase t
                 WHERE t.assessment_id = a.assessment_id)
        FROM dbo.Assessment a WHERE a.assessment_id = ?""", (ASSESSMENT_ID,))
    r = cur.fetchone()
    from grading_allocation import split_allocation
    test_alloc, rubric_alloc = split_allocation(r[2], r[3])
    out["assessment_prerequisites"] = {
        "owner_and_course": r[0] == TEACHER_ID and r[1] == COURSE,
        "course_exists": r[5] == 1,
        "deadline_in_future": bool(r[4]),
        "rubric_total_equals_rubric_allocation": str(r[6]) == str(rubric_alloc),
        "rubric_total": str(r[6]), "rubric_allocation": str(rubric_alloc),
        "test_marks_allocation": str(test_alloc),
        "exactly_10_tests": r[7] == 10, "all_hidden": r[8] == 10,
        "test_numbers_1_to_10": (r[9], r[10]) == (1, 10)}
    return out


def verify(baseline=None, connect_fn=connect):
    """Read-only checks of both databases."""
    report = {}
    src = connect_fn(SOURCE)
    try:
        cur = src.cursor()
        report["source_fingerprint"] = fingerprint(cur)
        cur.execute("SELECT test_number, input_data, expected_output FROM dbo.TestCase "
                    "WHERE assessment_id = ? ORDER BY test_number", (ASSESSMENT_ID,))
        source_tests = hashlib.sha256(repr([tuple(r) for r in cur.fetchall()])
                                      .encode()).hexdigest()
        source_copies = {k: _digest(cur, sql) for k, sql in COPIED_ROWS_SQL.items()}
    finally:
        src.rollback()
        src.close()
    if baseline is not None:
        report["source_unchanged"] = report["source_fingerprint"] == baseline

    tgt = connect_fn(TARGET)
    try:
        cur = tgt.cursor()
        counts = table_counts(cur)
        report["target_counts"] = {t: n for t, n in counts.items() if n}
        report["target_counts_as_expected"] = all(
            counts.get(t, 0) == n for t, n in EXPECTED_SEEDED_COUNTS.items()) and all(
            n == 0 for t, n in counts.items() if t not in EXPECTED_SEEDED_COUNTS)
        cur.execute("SELECT test_number, input_data, expected_output FROM dbo.TestCase "
                    "WHERE assessment_id = ? ORDER BY test_number", (ASSESSMENT_ID,))
        report["tests_identical_to_source"] = hashlib.sha256(
            repr([tuple(r) for r in cur.fetchall()]).encode()).hexdigest() == source_tests
        cur.execute(AUTHORIZATION_SQL, (SECTION, SECTION, ASSESSMENT_ID))
        report["test_students"] = [
            {"student_id": r[0], "can_log_in": bool(r[1]),
             "enrolled_in_SEC000001_CSC-101": bool(r[2]),
             "assessment_teacher_teaches_section": bool(r[3])}
            for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM dbo.Student_Export WHERE student_id NOT LIKE 'E2E-TEST-%'")
        report["real_students_in_test_db"] = int(cur.fetchone()[0])
        report["copied_rows_identical_to_source"] = {
            k: _digest(cur, sql) == source_copies[k]
            for k, sql in COPIED_ROWS_SQL.items()}
        report["integrity"] = integrity_checks(cur)
    finally:
        tgt.rollback()
        tgt.close()
    return report


def drop(confirm, connect_fn=connect):
    require_confirm(confirm)
    master = connect_fn("master", autocommit=True)
    try:
        cur = master.cursor()
        if not database_exists(cur):
            return {"dropped": False, "reason": "does not exist"}
        for statement in DROP_STATEMENTS:
            cur.execute(statement)
        return {"dropped": True}
    finally:
        master.close()


def plan():
    src = connect(SOURCE)
    try:
        cur = src.cursor()
        pre = source_preconditions(cur)
        exists = database_exists(cur)
    finally:
        src.rollback()
        src.close()
    return {"source": SOURCE, "target": TARGET, "target_exists": exists,
            "source_preconditions": pre,
            "create_statements": CREATE_STATEMENTS,
            "seed_statements": [{"step": label, "sql": sql, "expected_rows": n,
                                 "identity_insert": identity}
                                for label, sql, _, n, identity in SEED_STEPS]
            + [{"step": f"{s['student_id']} / {s['enrollment_id']}",
                "sql": [INSERT_STUDENT_SQL, INSERT_ENROLLMENT_SQL],
                "values": {**{k: v for k, v in s.items()}, "student_password": "<hidden prompt>"},
                "expected_rows": 2} for s in TEST_STUDENTS],
            "drop_statements": DROP_STATEMENTS}


# ----------------------------------------------------------------------
# Fourth test student (criterion-override scenario) and scoped cleanup
# ----------------------------------------------------------------------

FOURTH_STUDENT = {"student_id": "E2E-TEST-0004", "student_name": "E2E Test Student 4",
                  "student_email": "e2e.test4@e2e.test", "enrollment_id": "E2E-ENR-0004"}
# G3 records that cleanup must never touch.
PROTECTED_SUBMISSIONS = (2, 3, 4)

ADD_STUDENT_PRECHECK_SQL = """
SELECT DB_NAME(),
       (SELECT COUNT(*) FROM dbo.Student_Export WHERE student_id = ?),
       (SELECT COUNT(*) FROM dbo.Student_Export WHERE student_email = ?),
       (SELECT COUNT(*) FROM dbo.Enrollment_Export WHERE enrollment_id = ?),
       (SELECT COUNT(*) FROM dbo.AssessmentSection
         WHERE assessment_id = ? AND section_id = ?),
       (SELECT COUNT(*) FROM dbo.SectionCourseTeacher
         WHERE section_id = ? AND teacher_id = ? AND course_no = ?);
"""


def add_student(confirm, password, connect_fn=connect, student=FOURTH_STUDENT):
    """One student + one enrollment in FYP_E2E_Test, one transaction."""
    require_confirm(confirm)
    s = student
    conn = connect_fn(TARGET)
    try:
        cur = conn.cursor()
        cur.execute(ADD_STUDENT_PRECHECK_SQL, (
            s["student_id"], s["student_email"], s["enrollment_id"],
            ASSESSMENT_ID, SECTION, SECTION, TEACHER_ID, COURSE))
        name, sid, email, enr, assigned, teaches = cur.fetchone()
        if name != TARGET:
            raise Refused(f"Connected to {name}, not {TARGET}")
        if sid or email or enr:
            raise Refused(f"{s['student_id']} / its email / {s['enrollment_id']} "
                          "already exists; nothing written")
        if assigned != 1 or teaches < 1:
            raise Refused("Assessment 5 is not assigned to SEC000001 for BIIT235")
        cur.execute(INSERT_STUDENT_SQL, (s["student_id"], s["student_name"],
                                         s["student_email"], password, SECTION))
        if cur.rowcount != 1:
            raise Refused("Student row not inserted")
        cur.execute(INSERT_ENROLLMENT_SQL, (s["enrollment_id"], s["student_id"],
                                            SECTION, COURSE, TEACHER_ID))
        if cur.rowcount != 1:
            raise Refused("Enrollment row not inserted")
        conn.commit()
        return [(f"Student_Export {s['student_id']}", 1),
                (f"Enrollment_Export {s['enrollment_id']}", 1)]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


CLEANUP_STEPS = [
    ("EvaluationCriterionApproval",
     "DELETE a FROM dbo.EvaluationCriterionApproval a JOIN dbo.Submission s "
     "ON s.submission_id = a.submission_id WHERE s.student_id = ?;"),
    ("EvaluationCriterion",
     "DELETE c FROM dbo.EvaluationCriterion c JOIN dbo.Submission s "
     "ON s.submission_id = c.submission_id WHERE s.student_id = ?;"),
    ("Evaluation",
     "DELETE e FROM dbo.Evaluation e JOIN dbo.Submission s "
     "ON s.submission_id = e.submission_id WHERE s.student_id = ?;"),
    ("GradingJob",
     "DELETE j FROM dbo.GradingJob j JOIN dbo.Submission s "
     "ON s.submission_id = j.submission_id WHERE s.student_id = ?;"),
    ("Submission", "DELETE FROM dbo.Submission WHERE student_id = ?;"),
    ("Enrollment_Export", "DELETE FROM dbo.Enrollment_Export "
                          "WHERE enrollment_id = ? AND student_id = ?;"),
    ("Student_Export", "DELETE FROM dbo.Student_Export WHERE student_id = ?;"),
]

CLEANUP_COUNT_SQL = """
SELECT DB_NAME(),
  (SELECT COUNT(*) FROM dbo.EvaluationCriterionApproval a JOIN dbo.Submission s
     ON s.submission_id = a.submission_id WHERE s.student_id = ?),
  (SELECT COUNT(*) FROM dbo.EvaluationCriterion c JOIN dbo.Submission s
     ON s.submission_id = c.submission_id WHERE s.student_id = ?),
  (SELECT COUNT(*) FROM dbo.Evaluation e JOIN dbo.Submission s
     ON s.submission_id = e.submission_id WHERE s.student_id = ?),
  (SELECT COUNT(*) FROM dbo.GradingJob j JOIN dbo.Submission s
     ON s.submission_id = j.submission_id WHERE s.student_id = ?),
  (SELECT COUNT(*) FROM dbo.Submission WHERE student_id = ?),
  (SELECT COUNT(*) FROM dbo.Enrollment_Export WHERE enrollment_id = ? AND student_id = ?),
  (SELECT COUNT(*) FROM dbo.Student_Export WHERE student_id = ?);
"""


def cleanup_student(confirm, connect_fn=connect, student=FOURTH_STUDENT,
                    dry_run=True):
    """
    Remove ONLY the fourth test student's records, children first, in one
    transaction. Refuses if any affected submission is a protected G3 one.
    dry_run=True (default) only counts what would be deleted.
    """
    require_confirm(confirm)
    sid, enr = student["student_id"], student["enrollment_id"]
    if sid in {s["student_id"] for s in TEST_STUDENTS}:
        raise Refused("Cleanup is only for the fourth test student")
    conn = connect_fn(TARGET)
    try:
        cur = conn.cursor()
        cur.execute(CLEANUP_COUNT_SQL, (sid, sid, sid, sid, sid, enr, sid, sid))
        row = cur.fetchone()
        if row[0] != TARGET:
            raise Refused(f"Connected to {row[0]}, not {TARGET}")
        expected = dict(zip([t for t, _ in CLEANUP_STEPS], [int(x) for x in row[1:]]))
        cur.execute("SELECT submission_id FROM dbo.Submission WHERE student_id = ?", (sid,))
        affected = [int(r[0]) for r in cur.fetchall()]
        if set(affected) & set(PROTECTED_SUBMISSIONS):
            raise Refused(f"Would touch protected submissions {affected}")
        if dry_run:
            return {"would_delete": expected, "submissions": affected}
        deleted = {}
        for table, sql in CLEANUP_STEPS:
            params = (enr, sid) if table == "Enrollment_Export" else (sid,)
            cur.execute(sql, params)
            if cur.rowcount != expected[table]:
                raise Refused(f"{table}: expected {expected[table]} row(s), "
                              f"deleted {cur.rowcount}; rolled back")
            deleted[table] = cur.rowcount
        conn.commit()
        return {"deleted": deleted, "submissions": affected}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Isolated E2E test database")
    parser.add_argument("action", choices=["plan", "fingerprint", "create",
                                           "check-clone", "seed", "verify", "drop",
                                           "add-student", "cleanup-student"])
    parser.add_argument("--execute", action="store_true",
                        help="cleanup-student: actually delete (default: dry run)")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--confirm")
    parser.add_argument("--baseline")
    args = parser.parse_args()

    try:
        if args.action == "plan":
            print(json.dumps(plan(), indent=2))
        elif args.action == "fingerprint":
            conn = connect(SOURCE)
            try:
                data = fingerprint(conn.cursor())
            finally:
                conn.rollback()
                conn.close()
            Path(args.path).write_text(json.dumps(data, indent=1), encoding="utf-8")
            print("fingerprinted:", {t: v["rows"] for t, v in data.items()})
        elif args.action == "create":
            print(json.dumps(create(args.confirm), indent=2))
        elif args.action == "check-clone":
            print(json.dumps(check_clone(), indent=2))
        elif args.action == "seed":
            require_confirm(args.confirm)
            passwords = collect_passwords()
            for label, rows in seed(args.confirm, passwords):
                print(f"  {label}: {rows} row(s)")
            passwords = None
            print("Seed committed.")
        elif args.action == "verify":
            baseline = (json.loads(Path(args.baseline).read_text(encoding="utf-8"))
                        if args.baseline else None)
            report = verify(baseline)
            report.pop("source_fingerprint")
            print(json.dumps(report, indent=2))
        elif args.action == "drop":
            print(json.dumps(drop(args.confirm), indent=2))
        elif args.action == "add-student":
            require_confirm(args.confirm)
            password = prompt_password(FOURTH_STUDENT["student_id"], 100)
            for label, rows in add_student(args.confirm, password):
                print(f"  {label}: {rows} row(s)")
            password = None
            print("Committed.")
        elif args.action == "cleanup-student":
            print(json.dumps(cleanup_student(args.confirm,
                                             dry_run=not args.execute), indent=2))
    except Refused as error:
        sys.exit(f"REFUSED: {error}")


if __name__ == "__main__":
    main()
