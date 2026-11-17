import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import FakeConnection

import grading_job_completion_service as completion
from grading_job_failure_service import release_job_after_infrastructure_failure

TOKEN = str(uuid4())


def completion_responder(owned=True, evaluation_exists=False,
                         total="10.00", complete_rowcount=1):
    def responder(sql, params):
        if "FROM dbo.GradingJob WITH (UPDLOCK, HOLDLOCK)" in sql:
            return ([(21,)] if owned else []), -1
        if sql.startswith("SELECT a.total_marks"):
            return [(total,)], -1
        if "FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)" in sql:
            return ([(21,)] if evaluation_exists else []), -1
        if sql.startswith("UPDATE dbo.GradingJob"):
            return [], complete_rowcount
        return [], 1
    return responder


CRITERIA = [{"criterion_name": "Logic", "rating": "3.00", "explanation": "ok"},
            {"criterion_name": "Style", "rating": "1.00", "explanation": None}]


class CompletionTests(unittest.TestCase):
    def run_complete(self, score="8.00", criteria=CRITERIA, **kw):
        conn = FakeConnection(completion_responder(**kw))
        with patch.object(completion, "get_connection", return_value=conn):
            try:
                return conn, completion.complete_grading_job(
                    11, "w1", TOKEN, Decimal(score), "feedback", 8, 10,
                    criteria=criteria)
            except Exception as exc:
                return conn, exc

    def test_atomic_success(self):
        conn, r = self.run_complete()
        self.assertEqual(r["criteria_saved"], 2)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.statements("INSERT INTO dbo.Evaluation (")), 1)
        self.assertEqual(len(conn.statements("INSERT INTO dbo.EvaluationCriterion")), 2)
        order = [s.split()[0] + " " + s.split()[2] for s, _ in conn.executed
                 if s.startswith(("INSERT", "UPDATE"))]
        self.assertEqual(order[-1], "UPDATE SET")   # job completed last

    def test_duplicate_evaluation_rejected(self):
        conn, exc = self.run_complete(evaluation_exists=True)
        self.assertIsInstance(exc, completion.GradingJobCompletionError)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(conn.statements("INSERT"))

    def test_stale_worker_rejected(self):
        conn, exc = self.run_complete(owned=False)
        self.assertIsInstance(exc, completion.GradingJobOwnershipError)
        self.assertEqual(conn.commits, 0)

    def test_lost_ownership_during_completion_rolls_back(self):
        conn, exc = self.run_complete(complete_rowcount=0)
        self.assertIsInstance(exc, completion.GradingJobCompletionError)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)

    def test_score_above_total_rejected(self):
        conn, exc = self.run_complete(score="10.01")
        self.assertIsInstance(exc, completion.GradingJobCompletionError)
        self.assertEqual(conn.commits, 0)

    def test_invalid_criteria_rejected_before_db(self):
        for bad in ([{"criterion_name": "A", "rating": "-1"}],
                    [{"criterion_name": "A", "rating": "1.234"}],
                    [{"criterion_name": "A", "rating": 1},
                     {"criterion_name": "a", "rating": 1}]):
            with self.subTest(bad=bad):
                conn, exc = self.run_complete(criteria=bad)
                self.assertIsInstance(exc, ValueError)
                self.assertFalse(conn.executed)

    def test_backwards_compatible_signature(self):
        conn = FakeConnection(completion_responder())
        with patch.object(completion, "get_connection", return_value=conn):
            r = completion.complete_grading_job(
                11, "w1", TOKEN, "5", "fb", 5, 10)
        self.assertEqual(r["auto_evaluation_status"], "COMPLETED")
        self.assertEqual(r["criteria_saved"], 0)


class ReleaseTests(unittest.TestCase):
    def run_release(self, row):
        conn = FakeConnection(lambda sql, p: ([row] if row else [], 1))
        r = release_job_after_infrastructure_failure(
            11, "w1", TOKEN, "SANDBOX_RUN_FAILURE", "x" * 5000,
            connection_factory=lambda: conn)
        return conn, r

    def test_release_back_to_pending(self):
        conn, r = self.run_release((11, 21, "PENDING", 1))
        self.assertEqual(r["job_status"], "PENDING")
        self.assertFalse(r["evaluation_written"])
        self.assertLessEqual(len(r["last_error"]), 2000)
        sql, params = conn.executed[0]
        self.assertIn("claim_token = ?", sql)
        self.assertNotIn("Evaluation", sql)
        self.assertEqual(params[0], 3)          # max attempts -> FAILED rule
        self.assertEqual(conn.commits, 1)

    def test_release_by_stale_worker_is_noop(self):
        _, r = self.run_release(None)
        self.assertIsNone(r)

    def test_rejects_bad_token(self):
        with self.assertRaises(ValueError):
            release_job_after_infrastructure_failure(11, "w1", "nope", "X")


if __name__ == "__main__":
    unittest.main()
