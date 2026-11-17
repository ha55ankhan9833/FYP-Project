"""
Controlled Flask API tests. All service/database calls are patched;
no request reaches SQL Server, the compiler or Ollama.
"""

import unittest
from unittest.mock import patch

from tests import fakes  # noqa: F401

from flask_jwt_extended import create_access_token

import app as app_module
import assessment_rubric_service as rubric_service
import evaluation_review_service as review

flask_app = app_module.app
flask_app.config["TESTING"] = True


def token(role, identity):
    with flask_app.app_context():
        return create_access_token(identity=identity,
                                   additional_claims={"role": role})


def auth(role, identity="U1"):
    return {"Authorization": f"Bearer {token(role, identity)}"}


class RouteRegistrationTests(unittest.TestCase):
    def test_existing_and_new_routes_registered(self):
        rules = {(r.rule, m) for r in flask_app.url_map.iter_rules()
                 for m in r.methods}
        for expected in [
            ("/api/teacher/login", "POST"),
            ("/api/assessments", "POST"),
            ("/api/assessments/<int:assessment_id>/submissions", "POST"),
            ("/api/assessments/<int:assessment_id>/generate-test-cases", "POST"),
            ("/api/assessments/<int:assessment_id>/rubric", "PUT"),
            ("/api/assessments/<int:assessment_id>/rubric", "GET"),
            ("/api/assessments/<int:assessment_id>/rubric", "DELETE"),
            ("/api/assessments/<int:assessment_id>/submissions", "GET"),
            ("/api/submissions/<int:submission_id>/evaluation", "GET"),
            ("/api/submissions/<int:submission_id>/evaluation/finalize", "POST"),
            ("/api/student/assessments", "GET"),
            ("/api/student/assessments/<int:assessment_id>/submission", "GET"),
            ("/api/grading/status", "GET"),
        ]:
            self.assertIn(expected, rules)


class RubricRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_requires_jwt(self):
        self.assertEqual(self.client.get("/api/assessments/1/rubric")
                         .status_code, 401)

    def test_student_forbidden(self):
        r = self.client.put("/api/assessments/1/rubric",
                            json={"criteria": []}, headers=auth("student"))
        self.assertEqual(r.status_code, 403)

    def test_storage_unavailable_is_503(self):
        with patch.object(rubric_service, "replace_assessment_rubric",
                          side_effect=rubric_service.RubricStorageUnavailable()):
            r = self.client.put(
                "/api/assessments/1/rubric",
                json={"criteria": [{"criterion_name": "A", "max_marks": 5}]},
                headers=auth("teacher"))
        self.assertEqual(r.status_code, 503)

    def test_locked_is_409_and_teacher_id_from_jwt(self):
        with patch.object(rubric_service, "replace_assessment_rubric",
                          side_effect=rubric_service.RubricLockedError("x")) as m:
            r = self.client.put(
                "/api/assessments/9/rubric",
                json={"criteria": [{"criterion_name": "A", "max_marks": 5}]},
                headers=auth("teacher", "T77"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(m.call_args[0][:2], (9, "T77"))

    def test_rejects_extra_fields_and_empty(self):
        h = auth("teacher")
        self.assertEqual(self.client.put(
            "/api/assessments/1/rubric", json={"criteria": [], "x": 1},
            headers=h).status_code, 400)
        self.assertEqual(self.client.put(
            "/api/assessments/1/rubric", json={"criteria": []},
            headers=h).status_code, 400)


class EvaluationRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_finalize_teacher_only(self):
        r = self.client.post("/api/submissions/1/evaluation/finalize",
                             json={"final_score": 5}, headers=auth("student"))
        self.assertEqual(r.status_code, 403)

    def test_finalize_validation(self):
        h = auth("teacher")
        self.assertEqual(self.client.post(
            "/api/submissions/1/evaluation/finalize", json={},
            headers=h).status_code, 400)
        self.assertEqual(self.client.post(
            "/api/submissions/1/evaluation/finalize",
            json={"final_score": 5, "suggested_score": 9},
            headers=h).status_code, 400)

    def test_finalize_conflict(self):
        with patch.object(review, "finalize_evaluation",
                          side_effect=review.ConflictError("already")):
            r = self.client.post("/api/submissions/1/evaluation/finalize",
                                 json={"final_score": 5},
                                 headers=auth("teacher"))
        self.assertEqual(r.status_code, 409)

    def test_teacher_endpoints_forbid_students(self):
        h = auth("student")
        for url in ["/api/assessments/1/submissions",
                    "/api/submissions/1/evaluation", "/api/grading/status"]:
            self.assertEqual(self.client.get(url, headers=h).status_code, 403)

    def test_student_endpoints_forbid_teachers(self):
        h = auth("teacher")
        for url in ["/api/student/assessments",
                    "/api/student/assessments/1/submission"]:
            self.assertEqual(self.client.get(url, headers=h).status_code, 403)

    def test_student_identity_comes_from_jwt(self):
        with patch.object(review, "get_student_submission",
                          return_value={"status": "SUBMITTED"}) as m:
            r = self.client.get("/api/student/assessments/4/submission",
                                headers=auth("student", "S123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m.call_args[0], (4, "S123"))

    def test_grading_status_reports_disabled(self):
        r = self.client.get("/api/grading/status", headers=auth("teacher"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["student_execution_enabled"])


class StudentResultPrivacyTests(unittest.TestCase):
    """Service-level: students never get suggested scores or test data."""

    def make_conn(self, result_status, auto_status="COMPLETED"):
        def responder(sql, params):
            if "FROM dbo.Submission AS s" in sql:
                return [(5, "a.cpp", None, None, "COMPLETED", 1, result_status,
                         "8.00", "Well done", "auto fb", 8, 10, "10.00",
                         auto_status, "50.00", 4)], -1
            if "FROM dbo.EvaluationCriterion" in sql:
                return [("Logic", "3.00", "ok")], -1
            return [], -1
        return fakes.FakeConnection(responder)

    def test_unfinalized_result_hidden(self):
        conn = self.make_conn(None)
        r = review.get_student_submission(4, "S1",
                                          connection_factory=lambda: conn)
        self.assertEqual(r["status"], "UNDER_REVIEW")
        self.assertIsNone(r["result"])
        self.assertNotIn("suggested_score", str(r))

    def test_finalized_result_visible_without_test_data(self):
        conn = self.make_conn("FINALIZED")
        r = review.get_student_submission(4, "S1",
                                          connection_factory=lambda: conn)
        self.assertEqual(r["result"]["final_score"], "8.00")
        for forbidden in ("expected_output", "input_data", "suggested_score"):
            self.assertNotIn(forbidden, str(r))


class FinalizeServiceTests(unittest.TestCase):
    def make_conn(self, job_status="COMPLETED", existing=None, total="10.00"):
        def responder(sql, params):
            if sql.startswith("SELECT a.total_marks, j.job_status"):
                return [(total, job_status, 4, "50.00")], -1
            if "FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)" in sql:
                return ([(existing, "COMPLETED", 8, 10)]
                        if existing != "NONE" else []), -1
            return [], 1
        return fakes.FakeConnection(responder)

    def finalize(self, conn, score=8):
        return review.finalize_evaluation(1, "T1", score, " good ",
                                          connection_factory=lambda: conn)

    def test_review_existing_auto_grade(self):
        conn = self.make_conn(existing=None)
        r = self.finalize(conn)
        self.assertEqual(r["mode"], "REVIEWED_AUTOMATIC_GRADE")
        self.assertEqual(conn.commits, 1)

    def test_already_finalized_conflict(self):
        conn = self.make_conn(existing="FINALIZED")
        with self.assertRaises(review.ConflictError):
            self.finalize(conn)
        self.assertEqual(conn.commits, 0)

    def test_manual_grade_only_for_failed_jobs(self):
        conn = self.make_conn(job_status="PENDING", existing="NONE")
        with self.assertRaises(review.ConflictError):
            self.finalize(conn)
        conn = self.make_conn(job_status="FAILED", existing="NONE")
        self.assertEqual(self.finalize(conn)["mode"], "MANUAL_GRADE")

    def test_score_bounds(self):
        for bad in (-1, 10.01, "abc", True, 1.234):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.finalize(self.make_conn(), bad)


if __name__ == "__main__":
    unittest.main()
