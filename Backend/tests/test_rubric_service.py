import unittest
from decimal import Decimal

from tests.fakes import FakeConnection

import assessment_rubric_service as svc


def responder_factory(table_exists=True, assessment=("10.00", "50.00"),
                      submissions=0, rubric_rows=()):
    def responder(sql, params):
        if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
            return [(1 if table_exists else 0,)], -1
        if sql.startswith("SELECT total_marks, validation_percentage"):
            return ([assessment] if assessment else []), -1
        if sql.startswith("SELECT COUNT(*) FROM dbo.Submission"):
            return [(submissions,)], -1
        if "FROM dbo.AssessmentRubricCriterion" in sql:
            return list(rubric_rows), -1
        return [], 1
    return responder


GOOD = [{"criterion_name": "Logic", "max_marks": 3, "description": "x"},
        {"criterion_name": "Style", "max_marks": "2.00"}]


class NormalizeTests(unittest.TestCase):
    def test_valid(self):
        out = svc.normalize_rubric_input(GOOD, Decimal("5"))
        self.assertEqual([c["max_marks"] for c in out],
                         [Decimal("3"), Decimal("2.00")])

    def test_sum_mismatch_message(self):
        with self.assertRaisesRegex(ValueError, "must equal the rubric"):
            svc.normalize_rubric_input(GOOD, Decimal("6"))

    def test_rejections(self):
        bad_inputs = [
            [{"criterion_name": "", "max_marks": 5}],
            [{"criterion_name": "A", "max_marks": 0},
             {"criterion_name": "B", "max_marks": 5}],
            [{"criterion_name": "A", "max_marks": "2.555"},
             {"criterion_name": "B", "max_marks": "2.445"}],
            [{"criterion_name": "A", "max_marks": 2.5},
             {"criterion_name": "a ", "max_marks": 2.5}],
            [{"criterion_name": "A", "max_marks": 5, "weight": 1}],
            [{"criterion_name": "A" * 151, "max_marks": 5}],
            [{"criterion_name": "A", "max_marks": True}],
            "not a list",
            [],
        ]
        for bad in bad_inputs:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                svc.normalize_rubric_input(bad, Decimal("5"))

    def test_zero_allocation_forbids_criteria(self):
        self.assertEqual(svc.normalize_rubric_input([], 0), [])
        with self.assertRaises(ValueError):
            svc.normalize_rubric_input(GOOD, 0)


class ReplaceRubricTests(unittest.TestCase):
    def run_replace(self, criteria, **kw):
        conn = FakeConnection(responder_factory(**kw))
        try:
            result = svc.replace_assessment_rubric(
                7, "T1", criteria, connection_factory=lambda: conn)
        except Exception as exc:
            return conn, exc
        return conn, result

    def test_success_atomic(self):
        conn, result = self.run_replace(GOOD)
        self.assertEqual(result["criteria_saved"], 2)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.statements("DELETE FROM dbo.AssessmentRubricCriterion")), 1)
        self.assertEqual(len(conn.statements("INSERT INTO dbo.AssessmentRubricCriterion")), 2)
        lock = conn.statements("FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)")
        self.assertTrue(lock, "assessment row must be locked")

    def test_table_missing(self):
        conn, exc = self.run_replace(GOOD, table_exists=False)
        self.assertIsInstance(exc, svc.RubricStorageUnavailable)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(conn.statements("INSERT"))

    def test_not_owner(self):
        conn, exc = self.run_replace(GOOD, assessment=None)
        self.assertIsInstance(exc, svc.RubricNotFoundError)
        self.assertEqual(conn.commits, 0)

    def test_locked_after_submissions(self):
        conn, exc = self.run_replace(GOOD, submissions=1)
        self.assertIsInstance(exc, svc.RubricLockedError)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertFalse(conn.statements("DELETE"))

    def test_invalid_sum_rolls_back(self):
        conn, exc = self.run_replace(
            [{"criterion_name": "A", "max_marks": 1}])
        self.assertIsInstance(exc, ValueError)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(conn.statements("INSERT"))


class ReadinessTests(unittest.TestCase):
    def cursor(self, **kw):
        return FakeConnection(responder_factory(**kw)).cursor()

    def test_no_enforcement_before_table_exists(self):
        self.assertFalse(svc.check_rubric_ready(
            self.cursor(table_exists=False), 1, "10", "50"))

    def test_incomplete_rubric_blocks_submission(self):
        with self.assertRaisesRegex(ValueError, "not open for submissions"):
            svc.check_rubric_ready(self.cursor(rubric_rows=[]),
                                   1, "10", "50")

    def test_complete_rubric_allows_submission(self):
        rows = [(1, "Logic", "3.00", None), (2, "Style", "2.00", None)]
        self.assertTrue(svc.check_rubric_ready(
            self.cursor(rubric_rows=rows), 1, "10", "50"))

    def test_full_validation_needs_no_rubric(self):
        self.assertTrue(svc.check_rubric_ready(self.cursor(), 1, "10", "100"))

    def test_allocation_change_conflict(self):
        rows = [(1, "Logic", "5.00", None)]
        cur = self.cursor(rubric_rows=rows)
        self.assertIsNone(svc.rubric_allows_allocation_change(
            cur, 1, Decimal("10"), Decimal("50")))
        cur = self.cursor(rubric_rows=rows)
        self.assertIsNotNone(svc.rubric_allows_allocation_change(
            cur, 1, Decimal("10"), Decimal("60")))


if __name__ == "__main__":
    unittest.main()
