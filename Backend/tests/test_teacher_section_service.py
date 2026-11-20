"""
GET /api/teacher/sections: service + route. All database access goes to an
in-memory fake; nothing reaches SQL Server. Tokens are test tokens for
fake identities, signed by the test app only.
"""

import unittest
from unittest.mock import patch

from tests.fakes import FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module
import teacher_section_service as svc

URL = "/api/teacher/sections"
QS = "?course_code=CSC-101&semester_no=1&academic_session=2025SM"

TEACHERS = {"T1": ("T1", "TEACHER ONE", "CS"), "T9": ("T9", "TEACHER NINE", "SE")}
COURSES = {"CSC-101": ("CSC-101", "PROGRAMMING FUNDAMENTALS")}
# (section_id, name, program, semester_no, session, enrolled, teacher)
SECTIONS = [
    ("SEC-A", "A", "BCS", "1", "2025SM", 47, "T1"),
    ("SEC-D", "D", "BCS", "1", "2025SM", 42, "T1"),
    ("SEC-B", "B", "BCS", "1", "2025SM", 44, "T2"),
    ("SEC-OLD", "A", "BCS", "1", "2024FM", 3, "T1"),
    ("SEC-S2", "A", "BCS", "2", "2025SM", 9, "T1"),
]


def responder(sql, params):
    if "FROM dbo.Teacher_Export" in sql:
        return ([TEACHERS[params[0]]] if params[0] in TEACHERS else []), -1
    if "FROM dbo.Course_Export" in sql:
        return ([COURSES[params[0]]] if params[0] in COURSES else []), -1
    if "FROM dbo.Section_Export AS se" in sql:
        course, semester, session, teacher, course2 = params
        assert course == course2
        rows = [(s[0], s[1], s[2], s[3], s[4], s[5]) for s in SECTIONS
                if s[3] == semester and s[4] == session and s[6] == teacher
                and course == "CSC-101"]
        return rows, -1
    raise AssertionError(f"unexpected SQL: {sql}")


def call(teacher="T1", course="CSC-101", semester="1", session="2025SM", resp=responder):
    conn = FakeConnection(resp)
    result = svc.list_teacher_sections(teacher, course, semester, session,
                                       connection_factory=lambda: conn)
    return result, conn


class ServiceTests(unittest.TestCase):

    def test_returns_only_own_sections_for_filters(self):
        result, conn = call()
        self.assertEqual([s["section_id"] for s in result["sections"]], ["SEC-A", "SEC-D"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["total_enrolled_students"], 89)
        first = result["sections"][0]
        self.assertEqual(first, {
            "section_id": "SEC-A", "section_name": "A", "program": "BCS",
            "course_code": "CSC-101", "course_name": "PROGRAMMING FUNDAMENTALS",
            "semester_no": 1, "academic_session": "2025SM", "intake_year": 2025,
            "enrolled_students": 47, "teacher_id": "T1", "teacher_name": "TEACHER ONE"})
        self.assertEqual(result["teacher"], {"teacher_id": "T1", "teacher_name": "TEACHER ONE",
                                             "department": "CS"})
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_sql_is_parameterized_read_only_and_deduplicated(self):
        _, conn = call()
        sql = " ".join(s for s, _ in conn.executed)
        for word in ("INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP"):
            self.assertNotIn(word, sql.upper().replace("UPDLOCK", ""))
        self.assertIn("EXISTS ( SELECT 1 FROM dbo.SectionCourseTeacher", sql)
        self.assertIn("COUNT(DISTINCT e.student_id)", sql)
        self.assertNotIn("CSC-101", sql)          # values only as parameters
        self.assertNotIn("student_name", sql)
        self.assertNotIn("student_email", sql)
        self.assertNotIn("password", sql.lower())
        params = conn.executed[-1][1]
        self.assertEqual(params, ("CSC-101", "1", "2025SM", "T1", "CSC-101"))

    def test_teacher_without_sections_gets_empty_list(self):
        result, _ = call(teacher="T9")
        self.assertEqual(result["sections"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["total_enrolled_students"], 0)
        self.assertEqual(result["teacher"]["teacher_id"], "T9")

    def test_other_session_and_semester_filters(self):
        self.assertEqual([s["section_id"] for s in call(session="2024FM")[0]["sections"]],
                         ["SEC-OLD"])
        self.assertEqual([s["section_id"] for s in call(semester="2")[0]["sections"]],
                         ["SEC-S2"])
        self.assertEqual(call(semester="01")[0]["filters"]["semester_no"], 1)

    def test_unknown_course_and_teacher(self):
        with self.assertRaises(svc.CourseNotFoundError):
            call(course="CSC-999")
        with self.assertRaises(svc.TeacherNotFoundError):
            call(teacher="NOPE")

    def test_parameter_validation(self):
        bad = [dict(course=None), dict(course=""), dict(course="CSC 101;--"),
               dict(semester=None), dict(semester="x"), dict(semester="0"),
               dict(semester="21"), dict(semester="-1"), dict(session=None),
               dict(session="2025"), dict(session="2025SMX"), dict(session="25SM")]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                call(**kwargs)

    def test_database_error_propagates_after_cleanup(self):
        def broken(sql, params):
            raise RuntimeError("connection lost")
        conn = FakeConnection(broken)
        with self.assertRaises(RuntimeError):
            svc.list_teacher_sections("T1", "CSC-101", "1", "2025SM",
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


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()
        # list_teacher_sections binds get_connection as a default argument,
        # so patch the default the route uses.
        self.default_patch = patch.object(
            svc.list_teacher_sections, "__defaults__",
            (lambda: FakeConnection(responder),))
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)

    def get(self, qs=QS, **kw):
        return self.client.get(URL + qs, **kw)

    def test_requires_jwt(self):
        r = self.get()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["code"], "AUTH_REQUIRED")

    def test_students_forbidden(self):
        r = self.get(headers=headers(role="student", identity="T1"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "TEACHER_ONLY")

    def test_success_scoped_to_token_identity(self):
        r = self.get(headers=headers(identity="T1"))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual([s["section_id"] for s in body["sections"]], ["SEC-A", "SEC-D"])
        self.assertEqual(body["course"]["course_name"], "PROGRAMMING FUNDAMENTALS")

    def test_client_teacher_id_rejected(self):
        r = self.get(QS + "&teacher_id=T2", headers=headers(identity="T1"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_PARAMETER")

    def test_duplicate_parameter_rejected(self):
        r = self.get(QS + "&course_code=CSC-102", headers=headers())
        self.assertEqual(r.status_code, 400)

    def test_empty_list_for_teacher_without_sections(self):
        r = self.get(headers=headers(identity="T9"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["sections"], [])

    def test_bad_parameters_400(self):
        for qs in ("", "?course_code=CSC-101&semester_no=1",
                   "?course_code=CSC-101&semester_no=abc&academic_session=2025SM",
                   "?course_code=CSC-101&semester_no=1&academic_session=bad"):
            with self.subTest(qs=qs):
                r = self.get(qs, headers=headers())
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["code"], "INVALID_PARAMETER")

    def test_unknown_course_404(self):
        r = self.get("?course_code=CSC-999&semester_no=1&academic_session=2025SM",
                     headers=headers())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["code"], "COURSE_NOT_FOUND")

    def test_unknown_teacher_403(self):
        r = self.get(headers=headers(identity="NOPE"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "TEACHER_NOT_FOUND")

    def test_database_failure_is_distinct_500(self):
        with patch.object(svc, "list_teacher_sections", side_effect=RuntimeError("db down")):
            r = self.get(headers=headers())
        self.assertEqual(r.status_code, 500)
        body = r.get_json()
        self.assertEqual(body["code"], "SECTIONS_UNAVAILABLE")
        self.assertNotIn("db down", body["message"])

    def test_no_student_identifiers_in_response(self):
        text = self.get(headers=headers()).get_data(as_text=True).lower()
        for word in ("student_id", "student_name", "email", "password"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
