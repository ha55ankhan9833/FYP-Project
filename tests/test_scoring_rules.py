import unittest
from decimal import Decimal

from tests import fakes  # noqa: F401  (sets sys.path)

from equal_weight_test_scoring import calculate_equal_weight_score
from grading_allocation import split_allocation
from grading_rubric_allocation_adapter import (
    combine_grading_results, validate_rubric_allocation)
from grading_score_calculator import calculate_grading_score

RUBRIC = [{"criterion_name": "Logic", "max_marks": "3"},
          {"criterion_name": "Style", "max_marks": "2"}]


class AllocationTests(unittest.TestCase):
    def test_spec_example_split(self):
        self.assertEqual(split_allocation(10, 50),
                         (Decimal("5.00"), Decimal("5.00")))

    def test_fractional_split_sums_to_total(self):
        test, rubric = split_allocation(Decimal("10"), Decimal("33.33"))
        self.assertEqual(test, Decimal("3.33"))
        self.assertEqual(test + rubric, Decimal("10"))

    def test_full_validation_has_zero_rubric(self):
        self.assertEqual(split_allocation(10, 100)[1], Decimal("0"))

    def test_rejects_bad_inputs(self):
        for total, pct in [(0, 50), (10, 101), (10, -1), ("x", 50),
                           (True, 50), (float("nan"), 50)]:
            with self.assertRaises(ValueError):
                split_allocation(total, pct)


class EqualWeightTests(unittest.TestCase):
    def test_spec_example_8_of_10(self):
        r = calculate_equal_weight_score(8, 10, "5")
        self.assertEqual(r["test_score"], "4.00")
        self.assertEqual(r["weighting"], "EQUAL")

    def test_every_test_has_identical_weight(self):
        # passing any k tests always yields the same score
        scores = {calculate_equal_weight_score(k, 7, "10")["test_score"]
                  for k in [3]}
        self.assertEqual(scores, {"4.29"})

    def test_rejects_invalid_counts(self):
        for p, n in [(11, 10), (-1, 10), (0, 0), (1.0, 2)]:
            with self.assertRaises(ValueError):
                calculate_equal_weight_score(p, n, "5")


class CombinedScoreTests(unittest.TestCase):
    config = {"total_marks": "10", "validation_percentage": "50"}

    def test_spec_example_final_8_of_10(self):
        ai = {"criteria": [{"criterion_name": "Logic", "rating": "3"},
                           {"criterion_name": "Style", "rating": "1"}],
              "suggested_score": "4"}
        r = combine_grading_results(self.config, RUBRIC, ai, 8, 10)
        self.assertEqual(r["test_score"], "4.00")
        self.assertEqual(r["rubric_score"], "4")
        self.assertEqual(Decimal(r["suggested_score"]), Decimal("8"))

    def test_ai_cannot_exceed_criterion_max(self):
        ai = {"criteria": [{"criterion_name": "Logic", "rating": "3.5"},
                           {"criterion_name": "Style", "rating": "1"}]}
        with self.assertRaises(ValueError):
            combine_grading_results(self.config, RUBRIC, ai, 8, 10)

    def test_ai_inconsistent_total_rejected(self):
        ai = {"criteria": [{"criterion_name": "Logic", "rating": "3"},
                           {"criterion_name": "Style", "rating": "1"}],
              "suggested_score": "5"}
        with self.assertRaises(ValueError):
            combine_grading_results(self.config, RUBRIC, ai, 8, 10)

    def test_ai_unknown_or_missing_criterion_rejected(self):
        ai = {"criteria": [{"criterion_name": "Logic", "rating": "3"},
                           {"criterion_name": "Speed", "rating": "1"}]}
        with self.assertRaises(ValueError):
            combine_grading_results(self.config, RUBRIC, ai, 8, 10)
        with self.assertRaises(ValueError):
            combine_grading_results(
                self.config, RUBRIC,
                {"criteria": [{"criterion_name": "Logic", "rating": "3"}]},
                8, 10)

    def test_ai_cannot_change_test_counts(self):
        # Extra keys from the model are ignored; counts come from Python.
        ai = {"criteria": [{"criterion_name": "Logic", "rating": "0"},
                           {"criterion_name": "Style", "rating": "0"}],
              "tests_passed": 10}
        r = combine_grading_results(self.config, RUBRIC, ai, 2, 10)
        self.assertEqual(r["test_score"], "1.00")

    def test_zero_rubric_allocation_requires_no_ai(self):
        cfg = {"total_marks": "10", "validation_percentage": "100"}
        r = combine_grading_results(cfg, [], None, 7, 10)
        self.assertEqual(r["suggested_score"], "7.00")
        self.assertFalse(r["ai_called"])
        with self.assertRaises(ValueError):
            combine_grading_results(cfg, [], {"criteria": []}, 7, 10)

    def test_zero_test_allocation_requires_zero_tests(self):
        self.assertEqual(
            calculate_grading_score(10, 0, 0, 0, "6")["suggested_score"],
            "6.00")
        with self.assertRaises(ValueError):
            calculate_grading_score(10, 0, 3, 5, "6")

    def test_fractional_allocation_never_exceeds_total(self):
        cfg = {"total_marks": "10", "validation_percentage": "33.33"}
        rubric = [{"criterion_name": "A", "max_marks": "6.67"}]
        ai = {"criteria": [{"criterion_name": "A", "rating": "6.67"}]}
        r = combine_grading_results(cfg, rubric, ai, 3, 3)
        self.assertEqual(Decimal(r["suggested_score"]), Decimal("10.00"))


class RubricAllocationValidationTests(unittest.TestCase):
    def test_case_insensitive_duplicate_names(self):
        with self.assertRaises(ValueError):
            validate_rubric_allocation(
                [{"criterion_name": "Logic", "max_marks": 1},
                 {"criterion_name": " logic", "max_marks": 1}], 2)

    def test_sum_must_match(self):
        with self.assertRaises(ValueError):
            validate_rubric_allocation(RUBRIC, 6)


if __name__ == "__main__":
    unittest.main()
