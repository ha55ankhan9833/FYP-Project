"""
Student access after section assignment: exercises the EXISTING
save_student_submission transaction with an in-memory fake database.
No SQL Server writes, no C++ execution.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from tests.fakes import FakeConnection

import student_submission_service as sub
import evaluation_review_service as review

NOW = datetime(2026, 11, 18, 12, 0)
FUTURE = datetime(2026, 11, 19, 21, 34)
PAST = datetime(2026, 11, 17, 9, 0)


def responder_factory(student=True, assessment=True, enrolled=True,
                      existing=0, deadline=FUTURE, allow_late=False,
                      validation="50.00", rubric_rows=None):
    if rubric_rows is None:
        rubric_rows = [(1, "Logic", "3.00", None), (2, "Style", "2.00", None)]

    def responder(sql, params):
        if "FROM dbo.Student_Export WITH (UPDLOCK, HOLDLOCK)" in sql:
            return ([("S1",)] if student else []), -1
        if "FROM dbo.Assessment WITH (HOLDLOCK)" in sql:
            return ([(4, deadline, allow_late, "0.00", "CAI-252", "10.00",
                      validation)] if assessment else []), -1
        if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
            return [(1,)], -1
        if "FROM dbo.AssessmentRubricCriterion" in sql:
            return list(rubric_rows), -1
        if "FROM dbo.AssessmentSection asec WITH (HOLDLOCK)" in sql:
            return ([(31, "SEC-A")] if enrolled else []), -1
        if "FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)" in sql:
            return [(existing,)], -1
        if sql == "SELECT SYSDATETIME()":
            return [(NOW,)], -1
        if sql.startswith("INSERT INTO dbo.Submission"):
            return [(501,)], 1
        if sql.startswith("INSERT INTO dbo.GradingJob"):
            return [], 1
        raise AssertionError(f"unexpected SQL: {sql}")
    return responder


def submit(conn, student_id="S1"):
    with patch.object(sub, "get_connection", return_value=conn):
        try:
            return sub.save_student_submission(4, student_id,
                                               "int main(){return 0;}",
                                               "a.cpp")
        except Exception as exc:
            return exc


class SubmissionFlowTests(unittest.TestCase):
    def test_success_creates_exactly_one_grading_job_same_transaction(self):
        conn = FakeConnection(responder_factory())
        r = submit(conn)
        self.assertEqual(r["submission_id"], 501)
        self.assertEqual(r["assessment_section_id"], 31)
        self.assertEqual(len(conn.statements("INSERT INTO dbo.Submission")), 1)
        jobs = [p for s, p in conn.executed
                if s.startswith("INSERT INTO dbo.GradingJob")]
        self.assertEqual(jobs, [(501,)])
        self.assertEqual(conn.commits, 1)       # one commit covers both
        self.assertFalse(conn.autocommit)

    def test_not_enrolled_in_assigned_section_rejected(self):
        conn = FakeConnection(responder_factory(enrolled=False))
        exc = submit(conn)
        self.assertIn("not enrolled", str(exc))
        self.assertFalse(conn.statements("INSERT"))
        self.assertEqual(conn.commits, 0)

    def test_section_lookup_scoped_to_student_course_and_teacher(self):
        conn = FakeConnection(responder_factory())
        submit(conn, student_id="S1")
        sql, params = next((s, p) for s, p in conn.executed
                           if "FROM dbo.AssessmentSection asec" in s)
        self.assertEqual(params, (4, "S1", "CAI-252"))
        self.assertIn("dbo.Enrollment_Export", sql)
        self.assertIn("dbo.SectionCourseTeacher", sql)

    def test_deadline_enforced(self):
        conn = FakeConnection(responder_factory(deadline=PAST))
        self.assertEqual(str(submit(conn)), "Assessment deadline has passed")
        self.assertFalse(conn.statements("INSERT"))

    def test_late_allowed_when_permitted(self):
        conn = FakeConnection(responder_factory(deadline=PAST, allow_late=True))
        self.assertTrue(submit(conn)["is_late"])

    def test_duplicate_submission_rejected(self):
        conn = FakeConnection(responder_factory(existing=1))
        self.assertEqual(str(submit(conn)), "Student already has a submission")
        self.assertFalse(conn.statements("INSERT"))

    def test_incomplete_rubric_blocks_submission(self):
        # Assessment 4 shape: 10 marks, 50% tests -> rubric needs 5.00
        for rows in ([], [(1, "Logic", "3.00", None)]):
            with self.subTest(rows=rows):
                conn = FakeConnection(responder_factory(rubric_rows=rows))
                self.assertEqual(str(submit(conn)),
                                 "Assessment is not open for submissions yet")
                self.assertFalse(conn.statements("INSERT"))

    def test_full_validation_needs_no_rubric(self):
        conn = FakeConnection(responder_factory(validation="100.00",
                                                rubric_rows=[]))
        self.assertEqual(submit(conn)["submission_id"], 501)

    def test_grading_job_failure_rolls_back_submission(self):
        base = responder_factory()

        def responder(sql, params):
            if sql.startswith("INSERT INTO dbo.GradingJob"):
                raise RuntimeError("insert failed")
            return base(sql, params)

        conn = FakeConnection(responder)
        self.assertIsInstance(submit(conn), RuntimeError)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)


class StudentAssessmentListTests(unittest.TestCase):
    def make_conn(self, rubric_total="5.00", deadline=FUTURE):
        def responder(sql, params):
            if "FROM dbo.Assessment AS a" in sql:
                return [(4, "CAI-252", "Quiz", "Factorial", "p", "i", "o",
                         "10.00", deadline, False, "0.00", "50.00")], -1
            if sql.startswith("SELECT sec.assessment_id FROM dbo.Submission"):
                return [], -1
            if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
                return [(1,)], -1
            if "FROM dbo.AssessmentRubricCriterion" in sql:
                return ([(4, rubric_total)] if rubric_total else []), -1
            if sql == "SELECT SYSDATETIME()":
                return [(NOW,)], -1
            raise AssertionError(sql)
        return FakeConnection(responder)

    def listing(self, conn):
        return review.list_student_assessments("S1",
                                               connection_factory=lambda: conn)

    def test_list_query_requires_enrollment_and_teacher_link(self):
        conn = self.make_conn()
        self.listing(conn)
        sql, params = conn.executed[0]
        self.assertIn("dbo.Enrollment_Export", sql)
        self.assertIn("dbo.SectionCourseTeacher", sql)
        self.assertEqual(params, ("S1",))

    def test_open_when_rubric_complete(self):
        self.assertTrue(self.listing(self.make_conn())[0]["open_for_submissions"])

    def test_closed_when_rubric_incomplete_or_deadline_passed(self):
        self.assertFalse(self.listing(self.make_conn(rubric_total=None))[0]
                         ["open_for_submissions"])
        self.assertFalse(self.listing(self.make_conn(rubric_total="3.00"))[0]
                         ["open_for_submissions"])
        self.assertFalse(self.listing(self.make_conn(deadline=PAST))[0]
                         ["open_for_submissions"])

    def test_no_test_case_data_in_listing(self):
        item = self.listing(self.make_conn())[0]
        for forbidden in ("test_cases", "expected_output", "input_data",
                          "validation_percentage"):
            self.assertNotIn(forbidden, item)


class HiddenTestCaseAccessTests(unittest.TestCase):
    def test_students_cannot_read_test_cases(self):
        from flask_jwt_extended import create_access_token
        import app as app_module
        flask_app = app_module.app
        with flask_app.app_context():
            tok = create_access_token(identity="S1",
                                      additional_claims={"role": "student"})
        client = flask_app.test_client()
        with patch.object(app_module, "get_connection",
                          side_effect=AssertionError("DB must not be hit")):
            r = client.get("/api/assessments/4/test-cases",
                           headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
