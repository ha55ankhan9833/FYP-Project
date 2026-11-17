"""
Regression tests for DELETE /api/assessments/<id>.
The database connection is replaced with an in-memory fake; nothing
reaches SQL Server.
"""

import unittest
from unittest.mock import patch

from tests.fakes import FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module

flask_app = app_module.app
flask_app.config["TESTING"] = True


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity,
                                  additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class FakeIntegrityError(app_module.pyodbc.IntegrityError):
    pass


def responder_factory(owned=True, related=(0, 0, 0), table_exists=True,
                      rubric_rows=False, delete_rowcount=1,
                      reference=0, draft=0, delete_error=None):
    def responder(sql, params):
        if "FROM Assessment WITH (UPDLOCK, HOLDLOCK)" in sql:
            return ([(params[0],)] if owned else []), -1
        if "FROM AssessmentSection" in sql and "FROM Notification" in sql:
            return [tuple(related)], -1
        if sql.startswith("SELECT OBJECT_ID(N'dbo.AssessmentRubricCriterion'"):
            return [(123 if table_exists else None,)], -1
        if "FROM dbo.AssessmentRubricCriterion" in sql:
            return [(1 if rubric_rows else 0,)], -1
        if "FROM dbo.ReferenceSolution" in sql:
            assert "FROM dbo.ConstraintReviewDraft" in sql
            return [(reference, draft)], -1
        if sql.startswith("DELETE FROM Assessment"):
            if delete_error is not None:
                raise delete_error
            return [], delete_rowcount
        raise AssertionError(f"unexpected SQL: {sql}")
    return responder


class DeleteAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def delete(self, conn, hdrs=None, assessment_id=7):
        with patch.object(app_module, "get_connection", return_value=conn):
            return self.client.delete(f"/api/assessments/{assessment_id}",
                                      headers=hdrs or headers())

    def test_rubric_criteria_block_delete_with_409(self):
        conn = FakeConnection(responder_factory(rubric_rows=True))
        r = self.delete(conn)
        self.assertEqual(r.status_code, 409)
        self.assertIn("rubric criteria exist", r.get_json()["message"])
        self.assertFalse(conn.statements("DELETE FROM Assessment"))
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)

    def test_rubric_check_scoped_to_this_assessment(self):
        conn = FakeConnection(responder_factory(rubric_rows=True))
        self.delete(conn, assessment_id=42)
        params = [p for s, p in conn.executed
                  if "FROM dbo.AssessmentRubricCriterion" in s]
        self.assertEqual(params, [(42,)])

    def test_no_related_rows_still_deletes(self):
        conn = FakeConnection(responder_factory())
        r = self.delete(conn)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(conn.statements("DELETE FROM Assessment")), 1)
        self.assertEqual(conn.commits, 1)

    def test_missing_rubric_table_does_not_break_delete(self):
        conn = FakeConnection(responder_factory(table_exists=False))
        r = self.delete(conn)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(conn.statements("FROM dbo.AssessmentRubricCriterion"))

    def test_existing_related_rows_message_preserved(self):
        for related in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            with self.subTest(related=related):
                conn = FakeConnection(responder_factory(related=related,
                                                        rubric_rows=True))
                r = self.delete(conn)
                self.assertEqual(r.status_code, 409)
                self.assertIn("sections, notifications, or test cases",
                              r.get_json()["message"])
                self.assertFalse(conn.statements("DELETE FROM Assessment"))

    def test_not_owner_still_404(self):
        conn = FakeConnection(responder_factory(owned=False))
        r = self.delete(conn)
        self.assertEqual(r.status_code, 404)
        self.assertFalse(conn.statements("DELETE"))

    def test_ownership_uses_jwt_teacher_id(self):
        conn = FakeConnection(responder_factory())
        self.delete(conn, hdrs=headers(identity="T99"))
        lock_params = [p for s, p in conn.executed
                       if "WITH (UPDLOCK, HOLDLOCK)" in s][0]
        self.assertEqual(lock_params, (7, "T99"))

    def test_student_forbidden(self):
        conn = FakeConnection(responder_factory())
        r = self.delete(conn, hdrs=headers(role="student"))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(conn.executed)

    def test_reference_solution_or_draft_blocks_delete_with_409(self):
        for reference, draft in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(reference=reference, draft=draft):
                conn = FakeConnection(responder_factory(reference=reference,
                                                        draft=draft))
                r = self.delete(conn)
                self.assertEqual(r.status_code, 409)
                self.assertIn("reference solution or constraint review draft",
                              r.get_json()["message"])
                self.assertFalse(conn.statements("DELETE FROM Assessment"))
                self.assertEqual(conn.commits, 0)

    def test_reference_check_scoped_to_this_assessment(self):
        conn = FakeConnection(responder_factory())
        self.delete(conn, assessment_id=42)
        params = [p for s, p in conn.executed
                  if "FROM dbo.ReferenceSolution" in s]
        self.assertEqual(params, [(42, 42)])

    def test_unknown_fk_conflict_is_409_not_500(self):
        err = FakeIntegrityError("23000", "[547] The DELETE statement "
                                 "conflicted with the REFERENCE constraint")
        conn = FakeConnection(responder_factory(delete_error=err))
        r = self.delete(conn)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)

    def test_other_integrity_errors_still_500(self):
        err = FakeIntegrityError("23000", "[2627] something else")
        conn = FakeConnection(responder_factory(delete_error=err))
        self.assertEqual(self.delete(conn).status_code, 500)
        self.assertEqual(conn.commits, 0)

    def test_concurrent_delete_rowcount_still_409(self):
        conn = FakeConnection(responder_factory(delete_rowcount=0))
        r = self.delete(conn)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(conn.commits, 0)


if __name__ == "__main__":
    unittest.main()
