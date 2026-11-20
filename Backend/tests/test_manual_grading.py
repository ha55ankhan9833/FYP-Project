"""
Teacher-only manual grading fallback (POST .../evaluation/manual-grade).
Real Flask routes and services against the in-memory tests.e2e_world
database, plus fake-connection transaction tests. No SQL Server, no C++.
"""

import json
import unittest
from datetime import timedelta
from decimal import Decimal

import pyodbc

from tests.fakes import FakeConnection
from tests.test_e2e_student_grading_flow import (HAVE_REPORTS, TESTS, Flow,
                                                 fake_rubric)

import evaluation_review_service as review
import manual_grading_service as mgs
from dev_tools import e2e_api_client
from grading_job_claim_service import claim_grading_job_by_id

JUSTIFICATION = "Secure execution is disabled; graded by reading the code."
CONFIRM = {"confirm_manual_grade": True}


class ManualGradingFlowTests(unittest.TestCase):

    def setUp(self):
        self.flow = Flow(self)
        self.w = self.flow.world
        self.teacher = self.flow.login_teacher()
        self.assertEqual(self.flow.assign(self.teacher).status_code, 201)
        self.student = self.flow.login_student("S-1")
        r = self.flow.submit(self.student, "prime_buggy_square")
        self.sid = r.get_json()["submission"]["submission_id"]
        self.job_id = next(j["job_id"] for j in self.w.jobs.values()
                           if j["submission_id"] == self.sid)

    def manual(self, sid=None, token=None, **body):
        payload = {**CONFIRM, "final_score": "6.50", "justification": JUSTIFICATION}
        payload.update(body)
        return self.flow.client.post(
            f"/api/submissions/{sid or self.sid}/evaluation/manual-grade",
            headers=token or self.teacher,
            json={k: v for k, v in payload.items() if v is not None})

    def assertNothingWritten(self):
        self.assertNotIn(self.sid, self.w.evals)
        job = self.w.jobs[self.job_id]
        self.assertEqual((job["status"], job["attempts"]), ("PENDING", 0))

    # --- happy path ------------------------------------------------------

    def test_pending_submission_manually_graded(self):
        r = self.manual(instructor_feedback="Correct idea; check the loop bound.")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["evaluation"]["mode"], "MANUALLY_GRADED")

        e = self.w.evals[self.sid]
        self.assertEqual((e["auto_status"], e["result_status"], e["final"],
                          e["suggested"], e["passed"], e["run"]),
                         ("MANUALLY_GRADED", "FINALIZED", Decimal("6.50"), None, 0, 0))
        record = mgs.parse_manual_record(e["feedback"])
        self.assertEqual((record["teacher_id"], record["justification"],
                          record["previous_job"]["job_status"],
                          record["automatic_grading_performed"]),
                         ("BIIT235", JUSTIFICATION, "PENDING", False))
        job = self.w.jobs[self.job_id]
        self.assertEqual((job["status"], job["worker"], job["token"], job["lease"],
                          job["attempts"]),
                         ("COMPLETED", "manual-grade:BIIT235", None, None, 0))
        # nothing fabricated
        self.assertFalse([c for c in self.w.crits if c[1] == self.sid])
        self.assertEqual(self.w.approvals, [])

    def test_teacher_sees_audit_record(self):
        self.manual()
        ev = self.flow.client.get(f"/api/submissions/{self.sid}/evaluation",
                                  headers=self.teacher).get_json()["submission"]
        self.assertEqual(ev["grading_status"], "RESULT_PUBLISHED")
        m = ev["evaluation"]["manual_grading"]
        self.assertEqual((m["type"], m["final_score"], m["justification"]),
                         ("MANUALLY_GRADED", "6.50", JUSTIFICATION))
        self.assertIsNone(ev["evaluation"]["verified_test_marks"])
        self.assertEqual(ev["evaluation"]["ai_criteria"], [])

    def test_student_sees_only_final_mark_and_teacher_feedback(self):
        self.manual(instructor_feedback="Correct idea; check the loop bound.")
        body = self.flow.client.get("/api/student/assessments/5/submission",
                                    headers=self.student).get_json()
        result = body["submission"]["result"]
        self.assertEqual(body["submission"]["status"], "RESULT_PUBLISHED")
        self.assertEqual((result["outcome"], result["final_score"],
                          result["tests_passed"], result["tests_run"],
                          result["test_marks"], result["criteria"],
                          result["compilation_diagnostics"]),
                         ("MANUALLY_GRADED", "6.50", None, None, None, [], None))
        self.assertEqual(result["instructor_feedback"],
                         "Correct idea; check the loop bound.")
        self.assertEqual(result["summary"],
                         ["Your teacher graded this submission manually.",
                          "Your teacher left feedback on this submission."])
        text = json.dumps(body)
        for forbidden in (JUSTIFICATION, mgs.MANUAL_RECORD_MARKER, "BIIT235",
                          "manual-grade:", "suggested", "justification"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(e2e_api_client.scan_student_payload(body, TESTS), [])

    def test_failed_job_is_also_eligible(self):
        self.w.jobs[self.job_id]["status"] = "FAILED"
        self.w.jobs[self.job_id]["attempts"] = 3
        r = self.manual()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["evaluation"]["previous_job_status"], "FAILED")
        self.assertEqual(self.w.jobs[self.job_id]["attempts"], 3)

    def test_worker_never_claims_afterwards(self):
        self.manual()
        self.assertIsNone(claim_grading_job_by_id(self.job_id, "some-worker"))
        import grading_pipeline
        self.assertEqual(grading_pipeline.run_worker_once("w")["outcome"],
                         "SECURE_EXECUTION_DISABLED")

    # --- authorization ---------------------------------------------------

    def test_unauthorized_requests(self):
        self.assertEqual(self.manual(token=self.student).status_code, 403)
        self.assertEqual(self.manual(token=self.flow.login_teacher("BIIT999"))
                         .status_code, 404)
        r = self.flow.client.post(
            f"/api/submissions/{self.sid}/evaluation/manual-grade",
            json={**CONFIRM, "final_score": 5, "justification": JUSTIFICATION})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.manual(sid=999).status_code, 404)
        self.assertNothingWritten()

    # --- validation ------------------------------------------------------

    def test_invalid_requests_write_nothing(self):
        cases = [
            {"confirm_manual_grade": None},
            {"confirm_manual_grade": "yes"},
            {"confirm_manual_grade": False},
            {"justification": None},
            {"justification": "too short"},
            {"justification": 123},
            {"final_score": None},
            {"final_score": "10.01"},
            {"final_score": -1},
            {"final_score": "5.005"},
            {"final_score": True},
            {"unexpected": 1},
            {"tests_passed": 10},
            {"instructor_feedback": "Fails for N = 994009."},
            {"instructor_feedback": 5},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                r = self.manual(**extra)
                self.assertEqual(r.status_code, 400, r.get_json())
                self.assertEqual(r.get_json()["status"], "error")
        self.assertNothingWritten()

    # --- conflicts and concurrency ---------------------------------------

    def test_duplicate_manual_grade_rejected(self):
        self.assertEqual(self.manual().status_code, 200)
        before = dict(self.w.evals[self.sid])
        self.assertEqual(self.manual(final_score="9").status_code, 409)
        self.assertEqual(self.w.evals[self.sid], before)
        # and the normal finalize cannot change it either
        r = self.flow.client.post(f"/api/submissions/{self.sid}/evaluation/finalize",
                                  headers=self.teacher, json={"final_score": 9})
        self.assertEqual(r.status_code, 409)

    @unittest.skipUnless(HAVE_REPORTS, "recorded trusted-fixture reports not present")
    def test_existing_ai_evaluation_is_never_overwritten(self):
        self.flow.grade(self.sid, fake_rubric(2, 2)[0])
        before = dict(self.w.evals[self.sid])
        crits = list(self.w.crits)
        self.assertEqual(self.manual().status_code, 409)
        self.assertEqual((self.w.evals[self.sid], self.w.crits), (before, crits))

    def test_active_worker_lease_is_never_taken_over(self):
        claim = claim_grading_job_by_id(self.job_id, "worker-1", lease_seconds=600)
        self.assertIsNotNone(claim)
        before = dict(self.w.jobs[self.job_id])
        r = self.manual()
        self.assertEqual(r.status_code, 409)
        self.assertIn("in progress", r.get_json()["message"])
        self.assertEqual(self.w.jobs[self.job_id], before)
        self.assertNotIn(self.sid, self.w.evals)
        # an expired claim is also refused (the worker may still report)
        self.w.now += timedelta(hours=2)
        self.assertEqual(self.manual().status_code, 409)
        self.assertNotIn(self.sid, self.w.evals)

    def test_completed_job_without_evaluation_refused(self):
        self.w.jobs[self.job_id]["status"] = "COMPLETED"
        self.assertEqual(self.manual().status_code, 409)
        self.assertNotIn(self.sid, self.w.evals)


class ManualGradingTransactionTests(unittest.TestCase):
    """Fake-connection checks of the exact transaction behaviour."""

    def make_conn(self, job_status="PENDING", lease_active=0, evaluation=None,
                  close_rowcount=1, insert_error=None, owner=True):
        def responder(sql, params):
            if sql.startswith("SELECT a.total_marks, a.assessment_id"):
                return ([("10.00", 5, 7, job_status, 0, lease_active,
                          "2026-11-18 12:00:00")] if owner else []), -1
            if sql.startswith("SELECT result_status, auto_evaluation_status FROM"):
                return ([evaluation] if evaluation else []), -1
            if sql.startswith("SELECT input_data"):
                return [("994009", "NO\n")], -1
            if sql.startswith("UPDATE dbo.GradingJob"):
                return [], close_rowcount
            if sql.startswith("INSERT INTO dbo.Evaluation"):
                if insert_error:
                    raise insert_error
                return [], 1
            raise AssertionError(sql)
        return FakeConnection(responder)

    def grade(self, conn, **kwargs):
        args = {"final_score": "7", "justification": JUSTIFICATION}
        args.update(kwargs)
        return mgs.manually_grade_submission(
            11, "BIIT235", args["final_score"], args["justification"],
            args.get("instructor_feedback"), connection_factory=lambda: conn)

    def test_single_commit_and_statement_order(self):
        conn = self.make_conn()
        result = self.grade(conn)
        self.assertEqual(result["mode"], "MANUALLY_GRADED")
        self.assertEqual(conn.commits, 1)
        self.assertFalse(conn.autocommit)
        order = [s.split()[0] + " " + s.split()[1] for s, _ in conn.executed]
        self.assertEqual(order, ["SELECT a.total_marks,", "SELECT result_status,",
                                 "UPDATE dbo.GradingJob", "INSERT INTO"])
        lock_sql = conn.executed[0][0]
        self.assertIn("WITH (UPDLOCK, HOLDLOCK)", lock_sql)
        update_sql, update_params = conn.executed[2]
        self.assertIn("job_status IN ('PENDING', 'FAILED')", update_sql)
        self.assertEqual(update_params, ("manual-grade:BIIT235", 7, 11))
        insert_params = conn.executed[3][1]
        self.assertEqual((insert_params[0], insert_params[2], insert_params[3]),
                         (11, Decimal("7"), None))

    def test_concurrent_job_change_rolls_back(self):
        conn = self.make_conn(close_rowcount=0)
        with self.assertRaisesRegex(review.ConflictError, "concurrently"):
            self.grade(conn)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))
        self.assertFalse(conn.statements("INSERT"))

    def test_concurrent_evaluation_insert_rolls_back(self):
        conn = self.make_conn(insert_error=pyodbc.IntegrityError("23000", "(2627) PK_Evaluation"))
        with self.assertRaisesRegex(review.ConflictError, "concurrently"):
            self.grade(conn)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_refusals_happen_before_any_write(self):
        cases = [(dict(job_status="PROCESSING", lease_active=1), review.ConflictError),
                 (dict(job_status="PROCESSING"), review.ConflictError),
                 (dict(job_status="COMPLETED"), review.ConflictError),
                 (dict(evaluation=("FINALIZED", "COMPLETED")), review.ConflictError),
                 (dict(evaluation=(None, "NEEDS_REVIEW")), review.ConflictError),
                 (dict(owner=False), review.NotFoundError)]
        for kwargs, error in cases:
            with self.subTest(**kwargs):
                conn = self.make_conn(**kwargs)
                with self.assertRaises(error):
                    self.grade(conn)
                self.assertFalse(conn.statements("UPDATE") + conn.statements("INSERT"))
                self.assertEqual(conn.commits, 0)
        conn = self.make_conn()
        with self.assertRaises(ValueError):
            self.grade(conn, instructor_feedback="N = 994009 fails")
        self.assertFalse(conn.statements("UPDATE"))

    def test_record_round_trip(self):
        text = mgs.build_manual_record("T1", "2026-11-18T12:00:00", Decimal("7.00"),
                                       "10.00", JUSTIFICATION, 7, "PENDING", 0)
        record = mgs.parse_manual_record(text)
        self.assertEqual((record["final_score"], record["previous_job"]["job_id"]),
                         ("7.00", 7))
        self.assertIsNone(mgs.parse_manual_record("ordinary feedback"))
        self.assertIn("NOT performed", text)


if __name__ == "__main__":
    unittest.main()
