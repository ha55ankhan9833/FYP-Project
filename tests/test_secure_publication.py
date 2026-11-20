"""Publication and student visibility regression tests; in-memory DB only."""
import unittest
from datetime import datetime
from unittest.mock import patch

from flask_jwt_extended import create_access_token
from tests.fakes import FakeConnection
from tests.test_assessment_section_service import responder_factory
import app as app_module
import assessment_section_service as publish
import student_assessment_service as student


class PublicationPolicyTests(unittest.TestCase):
    def assign(self, mutate=None, fail_second=False):
        base = responder_factory()
        inserts = []
        def respond(sql, params):
            if fail_second and sql.startswith("INSERT INTO dbo.AssessmentSection"):
                inserts.append(params)
                if len(inserts) == 2:
                    raise RuntimeError("fixture insert failure")
            rows, count = base(sql, params)
            return (mutate(sql, rows) if mutate else rows), count
        conn = FakeConnection(respond)
        try:
            return conn, publish.assign_sections(7, "T1", ["SEC-A", "SEC-B"], lambda: conn)
        except Exception as error:
            return conn, error

    def test_success_response_is_confirmed_and_type_preserved(self):
        for kind in ("Assignment", "Quiz"):
            def mutate(sql, rows):
                if sql.startswith("SELECT assessment_id, course_code, deadline_at"):
                    return [(*rows[0][:7], kind, rows[0][8])]
                return rows
            conn, result = self.assign(mutate)
            self.assertEqual(result["assessment_type"], kind)
            self.assertEqual(result["published_section_ids"], ["SEC-A", "SEC-B"])
            self.assertEqual(result["publishing_status"], "PUBLISHED")
            self.assertEqual(result["title"], "Publish fixture")
            self.assertEqual(result["total_marks"], "10.00")
            self.assertEqual(conn.commits, 1)

    def test_bad_cases_or_approval_fail_closed(self):
        changes = (
            lambda rows: [(r[0], r[1], r[2], "0.51") if i == 0 else r for i, r in enumerate(rows)],
            lambda rows: [(r[0], r[1], "", r[3]) for r in rows],
            lambda rows: [(1, r[1], r[2], r[3]) for r in rows],
            lambda rows: [(r[0], "99999", r[2], r[3]) for r in rows],
        )
        for change in changes:
            conn, result = self.assign(lambda sql, rows: change(rows) if sql.startswith("SELECT test_number") else rows)
            self.assertIsInstance(result, publish.AssessmentNotReadyError)
            self.assertEqual(conn.commits, 0)
            self.assertFalse(conn.statements("INSERT"))

    def test_missing_approval_or_rubric_storage_blocks(self):
        for fragment in ("FROM dbo.ConstraintReviewDraft", "OBJECT_ID(N'dbo.AssessmentRubricCriterion'"):
            conn, result = self.assign(lambda sql, rows: [] if fragment in sql else rows)
            self.assertIsInstance(result, publish.AssessmentNotReadyError)
            self.assertFalse(conn.statements("INSERT"))

    def test_unknown_type_and_inexact_allocation_block(self):
        for index, value in ((7, "Exam"), (5, "33.33")):
            def mutate(sql, rows):
                if sql.startswith("SELECT assessment_id, course_code, deadline_at"):
                    row = list(rows[0]); row[index] = value
                    return [tuple(row)]
                return rows
            conn, result = self.assign(mutate)
            self.assertIsInstance(result, publish.AssessmentNotReadyError)
            self.assertFalse(conn.statements("INSERT"))

    def test_second_insert_failure_rolls_back_whole_request(self):
        conn, result = self.assign(fail_second=True)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)


class StudentVisibilityTests(unittest.TestCase):
    def connection(self):
        # Synthetic unit fixtures, never inserted into any SQL database.
        assessments = {7: ("Assignment", ["SEC-A"]), 8: ("Quiz", ["SEC-B"]), 9: ("Assignment", [])}
        enrolled = {"S1": [("SEC-A", "CSC-101")], "S2": [("SEC-B", "CSC-101")], "S3": [("SEC-A", "OTHER")]}
        def respond(sql, params):
            # Fail the test if identity/course/teacher joins or EXISTS are lost.
            for clause in ("FROM dbo.AssessmentSection", "dbo.Enrollment_Export", "dbo.SectionCourseTeacher", "dbo.Student_Export", "e.course_no", "a.teacher_id", "EXISTS", "e.student_id"):
                self.assertIn(clause, sql)
            for forbidden in ("dbo.TestCase", "dbo.ReferenceSolution", "AssessmentRubricCriterion", "expected_output", "source_code", "draft_json"):
                self.assertNotIn(forbidden, sql)
            allowed = []
            for aid, (kind, sections) in assessments.items():
                if len(params) == 2 and params[1] != aid:
                    continue
                if any((section, "CSC-101") in enrolled.get(params[0], []) for section in sections):
                    allowed.append((aid, kind, "Published fixture", "CSC-101", "Course", "20.00", datetime(2030, 1, 1), False, "0.00", "Statement", "Input format", "Output format"))
            return allowed, -1
        return FakeConnection(respond)

    def test_enrolled_students_see_only_own_section_and_course(self):
        for sid, ids in (("S1", [7]), ("S2", [8]), ("S3", []), ("REMOVED", [])):
            conn = self.connection()
            result = student.get_student_assessments(sid, connection_factory=lambda: conn)
            self.assertEqual([item["assessment_id"] for item in result], ids)
            self.assertEqual(conn.commits, 0)
            self.assertTrue(conn.closed)
            self.assertFalse(conn.statements("INSERT"))

    def test_guessed_unpublished_and_nonexistent_ids_same_not_found(self):
        for aid in (8, 9, 999):
            conn = self.connection()
            with self.assertRaises(student.StudentAssessmentNotFound):
                student.get_student_assessments("S1", aid, lambda: conn)

    def test_public_payload_is_an_allowlist_with_no_test_answers(self):
        conn = self.connection()
        result = student.get_student_assessments("S2", 8, lambda: conn)
        self.assertEqual(set(result), set(student.PUBLIC_FIELDS) | {"publishing_status"})
        self.assertEqual(result["assessment_type"], "Quiz")
        self.assertNotIn("test_cases", result)
        self.assertNotIn("rubric", result)

    def test_routes_require_student_jwt_and_reject_identity_override(self):
        app = app_module.app
        client = app.test_client()
        for path in ("/api/student/assessments", "/api/student/assessments/7"):
            self.assertEqual(client.get(path).status_code, 401)
            with app.app_context():
                teacher = create_access_token(identity="T1", additional_claims={"role": "teacher"})
                token = create_access_token(identity="S1", additional_claims={"role": "student"})
            self.assertEqual(client.get(path, headers={"Authorization": f"Bearer {teacher}"}).status_code, 403)
            self.assertEqual(client.get(path+"?student_id=S2", headers={"Authorization": f"Bearer {token}"}).status_code, 400)
            if path.endswith("/7"):
                with patch.object(student, "get_student_assessments", side_effect=student.StudentAssessmentNotFound()):
                    self.assertEqual(client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code, 404)

    def test_student_cannot_access_teacher_answers_or_reference(self):
        app = app_module.app
        with app.app_context():
            token = create_access_token(identity="S1", additional_claims={"role": "student"})
        for path in ("/api/assessments/7", "/api/assessments/7/test-cases", "/api/assessments/7/rubric", "/api/assessments/7/reference-solution"):
            self.assertEqual(app.test_client().get(path, headers={"Authorization": f"Bearer {token}"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
