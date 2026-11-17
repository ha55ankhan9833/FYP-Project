"""
Fixed rule: every assessment has EXACTLY 10 equally weighted test cases.
No database writes; SQL migration scripts are only inspected as text.
"""

import json
import re
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import BACKEND, FakeConnection

import generic_test_inputs as gti
import grading_pipeline as gp
import grading_test_case_loader as loader
from equal_weight_test_scoring import calculate_equal_weight_score
from test_case_builder import select_inputs
from test_case_policy import (REQUIRED_TEST_CASE_COUNT, TestCaseCountError,
                              enforce_test_case_count)
from verified_oracles import PRIME_CHECK_V1

DATA = Path(BACKEND) / "fixtures" / "new_assessment_csc101"


class PolicyTests(unittest.TestCase):
    def test_constant(self):
        self.assertEqual(REQUIRED_TEST_CASE_COUNT, 10)
        self.assertEqual(enforce_test_case_count(10), 10)
        for bad in (0, 9, 11, 15, "10", 10.0, None):
            with self.subTest(bad=bad), self.assertRaises(TestCaseCountError):
                enforce_test_case_count(bad)


class SelectionTests(unittest.TestCase):
    def select(self, constraints, extra=None):
        return select_inputs(gti.parse_approved_value_constraints(constraints), extra)

    def test_always_exactly_10_unique_valid_inputs(self):
        for constraints in ("1 <= N <= 100", "1 <= N <= 1000000", "-5 <= X <= 5",
                            "0 <= N <= 9", "1 <= a, b <= 1000",
                            "1 <= a, b, c <= 9", "-1000000 <= K <= 1000000"):
            with self.subTest(constraints=constraints):
                chosen = self.select(constraints)
                self.assertEqual(len(chosen), 10)
                self.assertEqual(len({c["input_data"] for c in chosen}), 10)
                gti.validate_inputs(gti.parse_approved_value_constraints(constraints),
                                    chosen)

    def test_priority_boundaries_then_teacher_then_random(self):
        chosen = self.select("1 <= N <= 1000", ["7", "500"])     # 500 is midpoint
        cats = [c["category"] for c in chosen]
        self.assertEqual(cats[:5], ["boundary", "boundary", "near_boundary",
                                    "near_boundary", "normal"])
        self.assertEqual(cats[5], "teacher_specified")           # "7" only
        self.assertTrue(all(c == "random" for c in cats[6:]))

    def test_deterministic(self):
        self.assertEqual(self.select("1 <= N <= 1000000"),
                         self.select("1 <= N <= 1000000"))

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "allow only 9"):
            self.select("1 <= N <= 9")
        with self.assertRaisesRegex(ValueError, "allow only 9"):
            self.select("0 <= a, b <= 2")
        with self.assertRaisesRegex(ValueError, "exceed the fixed 10"):
            self.select("1 <= N <= 1000", [str(n) for n in range(10, 16)])
        with self.assertRaisesRegex(ValueError, "At most 3"):
            gti.parse_approved_value_constraints("1 <= a, b, c, d <= 9")

    def test_assessment_4_constraint_still_generates_10(self):
        chosen = self.select("1 <= N <= 100")
        self.assertEqual(len(chosen), 10)
        self.assertEqual([c["input_data"] for c in chosen[:5]],
                         ["1", "100", "2", "99", "50"])


class LoaderEnforcementTests(unittest.TestCase):
    def load(self, n):
        rows = [(i, f"t{i}", "c", str(i), "x\n") for i in range(1, n + 1)]
        conn = FakeConnection(lambda sql, p: (rows, -1))
        with patch.object(loader, "get_connection", return_value=conn):
            return loader.load_grading_test_cases(5)

    def test_exactly_10_loads(self):
        self.assertEqual(len(self.load(10)), 10)

    def test_assessment_5_current_15_rows_are_refused(self):
        for n in (15, 9, 11, 1):
            with self.subTest(n=n), self.assertRaisesRegex(TestCaseCountError,
                                                           "exactly 10"):
                self.load(n)


class PipelineEnforcementTests(unittest.TestCase):
    def deps(self, load_test_cases):
        calls = {"complete": [], "release": []}
        tests10 = [{"test_number": i, "input_data": str(i),
                    "expected_output": "NO\n"} for i in range(1, 11)]

        class Sandbox:
            def compile(self, src):
                return {"status": "COMPILED", "artifact": "A"}

            def run_tests(self, artifact, cases):
                return [{"test_number": c["test_number"], "status": "completed",
                         "stdout": "NO\n"} for c in cases]

            def cleanup(self, artifact):
                pass

        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": 1,
                                "source_code": "int main(){}"},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("100")},
            load_context=lambda a: {"problem": "p", "input_format": "i",
                                    "output_format": "o", "constraints": "c"},
            load_rubric=lambda a: [],
            load_test_cases=lambda a: load_test_cases(tests10),
            get_sandbox=Sandbox,
            grade_rubric=lambda *a: None,
            complete=lambda *a, **k: calls["complete"].append((a, k)),
            complete_compilation_failure=lambda *a: calls["complete"].append(a),
            release=lambda *a: calls["release"].append(a) or {"job_status": "PENDING"})
        return deps, calls

    def run(self, load_test_cases):
        deps, calls = self.deps(load_test_cases)
        result = gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                         "claim_token": str(uuid4())}, "w", deps)
        return result, calls

    def test_10_tests_graded(self):
        result, calls = self.run(lambda t: t)
        self.assertEqual(result["outcome"], "GRADED")
        args = calls["complete"][0][0]
        self.assertEqual((args[5], args[6]), (10, 10))
        self.assertEqual(args[3], Decimal("10.00"))

    def test_loader_refusal_releases_without_zero(self):
        def refuse(t):
            raise TestCaseCountError("Assessment 5 must have exactly 10 test "
                                     "cases (found 15)")
        result, calls = self.run(refuse)
        self.assertEqual(result["error_code"], "TEST_CASES_NOT_CONFIGURED")
        self.assertFalse(calls["complete"])
        self.assertEqual(len(calls["release"]), 1)

    def test_pipeline_rejects_non_10_evidence_even_if_loader_bypassed(self):
        result, calls = self.run(lambda t: t[:9])
        self.assertEqual(result["error_code"], "TEST_CASES_NOT_CONFIGURED")
        self.assertFalse(calls["complete"])


class FormulaTests(unittest.TestCase):
    def test_score_is_allocated_times_passed_over_10(self):
        for allocation in ("5", "5.00", "7.5", "3.33", "10"):
            for passed in range(11):
                expected = (Decimal(allocation) * passed / 10).quantize(
                    Decimal("0.01"), rounding="ROUND_HALF_UP")
                self.assertEqual(Decimal(calculate_equal_weight_score(
                    passed, 10, allocation)["test_score"]), expected)


class MigrationPlanTests(unittest.TestCase):
    def plan(self):
        return json.loads((DATA / "migration_to_10_tests.json").read_text())

    def test_plan_is_10_verified_unique_and_covers_required_cases(self):
        plan = self.plan()
        cases = plan["proposed_10"]
        self.assertEqual(len(cases), 10)
        self.assertEqual([c["test_number"] for c in cases], list(range(1, 11)))
        self.assertEqual(len({c["input_data"] for c in cases}), 10)
        for c in cases:
            self.assertEqual(PRIME_CHECK_V1.expected_output(c["input_data"]),
                             c["expected_output"])
        inputs = {int(c["input_data"]) for c in cases}
        for required in (1, 2, 4, 997 * 997, 999983, 1_000_000, 500_000):
            self.assertIn(required, inputs)
        outs = [c["expected_output"] for c in cases]
        self.assertEqual((outs.count("YES\n"), outs.count("NO\n")), (4, 6))

    def test_plan_matches_generator_output(self):
        plan = self.plan()
        extra = plan["generation_request_after_migration"]["body"]["additional_inputs"]
        chosen = select_inputs(gti.parse_approved_value_constraints(
            "1 <= N <= 1000000"), extra)
        self.assertEqual([c["input_data"] for c in chosen],
                         [c["input_data"] for c in plan["proposed_10"]])

    def test_kept_and_dropped_partition_the_current_15_rows(self):
        plan = self.plan()
        kept = {c["current_test_case_id"] for c in plan["proposed_10"]}
        dropped = {c["current_test_case_id"] for c in plan["dropped_5"]}
        self.assertEqual(kept | dropped, set(range(13, 28)))
        self.assertFalse(kept & dropped)

    def test_sql_scripts_only_touch_assessment_5_and_are_guarded(self):
        for name in ("migrate_a5_option_a_delete_15.sql",
                     "migrate_a5_option_b_keep_10.sql"):
            text = (DATA / name).read_text()
            code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            self.assertRegex(text, r"EXECUTED")   # status line documented
            self.assertIn("SET XACT_ABORT ON", code)
            self.assertIn("COMMIT TRANSACTION", code)
            self.assertGreaterEqual(code.count("THROW"), 4)
            statements = list(re.finditer(r"^\s*(DELETE|UPDATE)\b[^;]*;", code,
                                          flags=re.S | re.M))
            self.assertTrue(statements)
            for match in statements:
                self.assertIn("assessment_id = 5", match.group(0), match.group(0))
            self.assertNotRegex(code, r"\b(DROP|TRUNCATE|ALTER|INSERT\s+INTO\s+dbo)\b")


if __name__ == "__main__":
    unittest.main()
