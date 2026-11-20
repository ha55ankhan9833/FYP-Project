"""
GET /api/teacher/sections/<section_id>/students: service + route.
In-memory fake database only; synthetic student data (no real records).
"""

import unittest
from unittest.mock import patch

from tests.fakes import FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module
import teacher_section_service as svc

QS = "?course_code=CSC-101&semester_no=1&academic_session=2025SM"

COURSES = {"CSC-101": ("CSC-101", "PROGRAMMING FUNDAMENTALS")}
# section_id -> (name, program, semester_no, session)
SECTIONS = {"SEC-C": ("C", "BCS", "1", "2025SM"),
            "SEC-B": ("B", "BSSE", "1", "2025SM"),
            "SEC-X": ("X", "BCS", "1", "2025SM"),     # another teacher's section
            "SEC-OLD": ("A", "BCS", "1", "2024FM"),
            "SEC-EMPTY": ("E", "BCS", "1", "2025SM")}
ALLOC = {("SEC-C", "T1", "CSC-101"), ("SEC-B", "T1", "CSC-101"), ("SEC-OLD", "T1", "CSC-101"),
         ("SEC-EMPTY", "T1", "CSC-101"), ("SEC-X", "T2", "CSC-101")}
STUDENTS = {f"2025-TEST-{i:04d}": (f"STUDENT {i}", "ENROLLED", "secret-pw", f"s{i}@example.invalid")
            for i in range(1, 50)}
# Enrollment rows (student, section, course), with a deliberate duplicate
# and an enrollment in a different course.
ENROLL = ([(f"2025-TEST-{i:04d}", "SEC-C", "CSC-101") for i in range(1, 34)]
          + [("2025-TEST-0001", "SEC-C", "CSC-101")]
          + [(f"2025-TEST-{i:04d}", "SEC-B", "CSC-101") for i in range(34, 44)]
          + [("2025-TEST-0044", "SEC-C", "MTH-101")]
          + [(f"2025-TEST-{i:04d}", "SEC-X", "CSC-101") for i in range(45, 48)])


def responder(sql, params):
    if "FROM dbo.Course_Export" in sql:
        return ([COURSES[params[0]]] if params[0] in COURSES else []), -1
    if "FROM dbo.Section_Export AS se WHERE se.section_id = ?" in sql:
        teacher, course, section = params
        if section not in SECTIONS:
            return [], -1
        name, program, sem, sess = SECTIONS[section]
        return [(section, name, program, sem, sess,
                 1 if (section, teacher, course) in ALLOC else 0)], -1
    if "FROM dbo.Enrollment_Export AS en" in sql:
        section, course = params
        ids = [s for s, sec, c in ENROLL if sec == section and c == course]
        if "SELECT DISTINCT en.student_id" in sql:       # emulate SQL DISTINCT
            ids = sorted(set(ids))
        return [(sid, STUDENTS[sid][0], STUDENTS[sid][1]) for sid in ids], -1
    raise AssertionError(f"unexpected SQL: {sql}")


def roster(teacher="T1", section="SEC-C", course="CSC-101", semester="1",
           session="2025SM", resp=responder):
    conn = FakeConnection(resp)
    result = svc.list_section_students(teacher, section, course, semester, session,
                                       connection_factory=lambda: conn)
    return result, conn


class RosterServiceTests(unittest.TestCase):

    def test_authorized_teacher_gets_exact_unique_roster(self):
        result, conn = roster()
        ids = [s["student_id"] for s in result["students"]]
        self.assertEqual(result["total"], 33)
        self.assertEqual(len(ids), len(set(ids)))           # duplicate enrollment removed
        self.assertNotIn("2025-TEST-0044", ids)             # other course excluded
        self.assertEqual(result["students"][0], {
            "student_id": "2025-TEST-0001", "registration_no": "2025-TEST-0001",
            "student_name": "STUDENT 1", "section_id": "SEC-C",
            "enrollment_status": "ENROLLED"})
        self.assertEqual(result["section"], {
            "section_id": "SEC-C", "section_name": "C", "program": "BCS", "semester_no": 1,
            "academic_session": "2025SM", "course_code": "CSC-101",
            "course_name": "PROGRAMMING FUNDAMENTALS"})
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_second_section_count(self):
        self.assertEqual(roster(section="SEC-B")[0]["total"], 10)

    def test_other_teachers_section_denied(self):
        with self.assertRaises(svc.SectionAccessDeniedError):
            roster(section="SEC-X")
        with self.assertRaises(svc.SectionAccessDeniedError):
            roster(teacher="T2", section="SEC-C")

    def test_denied_before_roster_query(self):
        conn = FakeConnection(responder)
        with self.assertRaises(svc.SectionAccessDeniedError):
            svc.list_section_students("T1", "SEC-X", "CSC-101", "1", "2025SM",
                                      connection_factory=lambda: conn)
        self.assertEqual(conn.statements("Enrollment_Export"), [])

    def test_unknown_section_course_and_wrong_term(self):
        with self.assertRaises(svc.SectionNotFoundError):
            roster(section="SEC-NOPE")
        with self.assertRaises(svc.CourseNotFoundError):
            roster(course="CSC-999")
        with self.assertRaises(svc.SectionNotFoundError):
            roster(section="SEC-OLD")                       # stored 2024FM, asked 2025SM
        with self.assertRaises(svc.SectionNotFoundError):
            roster(semester="2")

    def test_session_match_is_case_insensitive(self):
        self.assertEqual(roster(session="2025sm")[0]["total"], 33)

    def test_genuinely_empty_section(self):
        result, _ = roster(section="SEC-EMPTY")
        self.assertEqual((result["total"], result["students"]), (0, []))

    def test_invalid_parameters(self):
        for kwargs in (dict(section=""), dict(section="SEC'; DROP--"), dict(section=None),
                       dict(section="X" * 21), dict(course=""), dict(semester="x"),
                       dict(session="2025")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                roster(**kwargs)

    def test_sql_parameterized_read_only_and_minimal_columns(self):
        _, conn = roster()
        sql = " ".join(s for s, _ in conn.executed)
        for word in ("INSERT", "UPDATE ", "DELETE", "MERGE", "ALTER", "DROP"):
            self.assertNotIn(word, sql.upper())
        for value in ("SEC-C", "CSC-101", "T1"):
            self.assertNotIn(value, sql)
        for column in ("student_password", "student_email", "phone"):
            self.assertNotIn(column, sql)
        self.assertIn("SELECT DISTINCT en.student_id", sql)
        self.assertIn("COLLATE DATABASE_DEFAULT", sql)

    def test_database_error_cleans_up(self):
        def broken(sql, params):
            raise RuntimeError("SQL Server error 4060 at host X")
        conn = FakeConnection(broken)
        with self.assertRaises(RuntimeError):
            svc.list_section_students("T1", "SEC-C", "CSC-101", "1", "2025SM",
                                      connection_factory=lambda: conn)
        self.assertTrue(conn.closed)
        self.assertEqual(conn.commits, 0)


# ---------------------------------------------------------------- routes

flask_app = app_module.app
flask_app.config["TESTING"] = True


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity, additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class RosterRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()
        p = patch.object(svc.list_section_students, "__defaults__",
                         (lambda: FakeConnection(responder),))
        p.start()
        self.addCleanup(p.stop)

    def get(self, section="SEC-C", qs=QS, **kw):
        return self.client.get(f"/api/teacher/sections/{section}/students{qs}", **kw)

    def test_missing_jwt_401(self):
        r = self.get()
        self.assertEqual((r.status_code, r.get_json()["code"]), (401, "AUTH_REQUIRED"))

    def test_student_jwt_403(self):
        r = self.get(headers=headers(role="student", identity="2025-TEST-0001"))
        self.assertEqual((r.status_code, r.get_json()["code"]), (403, "TEACHER_ONLY"))

    def test_authorized_200(self):
        r = self.get(headers=headers())
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["total"], 33)
        self.assertEqual(len(body["students"]), 33)

    def test_other_teachers_section_403(self):
        r = self.get(section="SEC-X", headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (403, "SECTION_ACCESS_DENIED"))
        self.assertNotIn("students", r.get_json())

    def test_client_teacher_id_rejected(self):
        r = self.get(qs=QS + "&teacher_id=T2", headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "INVALID_PARAMETER"))

    def test_invalid_section_404(self):
        r = self.get(section="SEC-NOPE", headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "SECTION_NOT_FOUND"))

    def test_invalid_course_and_session(self):
        r = self.get(qs="?course_code=CSC-999&semester_no=1&academic_session=2025SM",
                     headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "COURSE_NOT_FOUND"))
        r = self.get(qs="?course_code=CSC-101&semester_no=1&academic_session=2024FM",
                     headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "SECTION_NOT_FOUND"))
        r = self.get(qs="?course_code=CSC-101&semester_no=1&academic_session=bad",
                     headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "INVALID_PARAMETER"))
        r = self.get(qs="", headers=headers())
        self.assertEqual(r.status_code, 400)

    def test_empty_section_200(self):
        r = self.get(section="SEC-EMPTY", headers=headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["total"], r.get_json()["students"]), (0, []))

    def test_no_sensitive_data_in_response(self):
        text = self.get(headers=headers()).get_data(as_text=True).lower()
        for leak in ("secret-pw", "example.invalid", "password", "email", "phone"):
            self.assertNotIn(leak, text)
        student = self.get(headers=headers()).get_json()["students"][0]
        self.assertEqual(set(student), {"student_id", "registration_no", "student_name",
                                        "section_id", "enrollment_status"})

    def test_database_failure_500_without_internals(self):
        with patch.object(svc, "list_section_students",
                          side_effect=RuntimeError("SQL Server error 4060 at host X")):
            r = self.get(headers=headers())
        body = r.get_json()
        self.assertEqual((r.status_code, body["code"]), (500, "ROSTER_UNAVAILABLE"))
        self.assertNotIn("4060", body["message"])
        self.assertNotIn("SQL", body["message"])

    def test_sections_list_route_unchanged(self):
        r = self.client.get("/api/teacher/sections" + QS)
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
