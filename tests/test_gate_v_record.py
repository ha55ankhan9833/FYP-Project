"""
Gate V (V1-V3): the owner-approved Assessment 6 fixture, its single recorded
execution and its reviewed record. No database, no compiler, no execution,
no real LLM. The recorded report is git-ignored, so the integrity/replay
tests skip when it is absent (same policy as the Assessment 5 records).
"""

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import BACKEND

import cpp_compilation_service
import grading_pipeline as gp
from dev_tools import recorded_evidence_grading as reg
from dev_tools import trusted_execution_harness as teh
from ollama_grading_service import parse_criterion_only_response
from rubric_ai_grader import grade_rubric_with_ollama

KEY = "array_sum_int_accumulator"
SHA = "ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4"
PLANNED = json.loads((Path(BACKEND) / "fixtures/structured_array_sum/"
                      "planned_test_cases.json").read_text(encoding="utf-8"))["cases"]
# Stored A6 tests = the planned inputs + oracle outputs (verified equal in S4).
TESTS = [{"test_number": c["test_number"], "input_data": c["input_data"],
          "expected_output": c["expected_output_python_oracle"]} for c in PLANNED]
RUBRIC = [{"criterion_name": "Logic & Correctness", "max_marks": Decimal("3.00")},
          {"criterion_name": "Code Quality", "max_marks": Decimal("2.00")}]
HAVE_REPORT = (reg.REPORTS / reg.REVIEWED_RECORDS[KEY]["report"]).exists()


class ApprovalTests(unittest.TestCase):

    def test_only_the_reviewed_fixture_is_approved(self):
        self.assertEqual(teh.load_approvals(), {KEY: SHA})
        self.assertEqual(teh.fixture_sha256(KEY), SHA)
        self.assertEqual(len(teh.fixture_path(KEY).read_bytes()), 454)
        for other in ("prime_correct", "prime_buggy_square", "prime_compile_error"):
            with self.assertRaises(teh.NotApproved):
                teh.require_approval(other)

    def test_changed_bytes_are_refused(self):
        with self.assertRaises(teh.NotApproved):
            teh.require_approval(KEY, approvals={KEY: "0" * 64})

    def test_fixture_bound_to_assessment_6_in_e2e_database(self):
        self.assertEqual(teh.assessment_for(KEY), 6)
        self.assertEqual(teh.FIXTURE_DATABASES[KEY], "FYP_E2E_Test")
        self.assertNotIn("prime_correct", teh.FIXTURE_DATABASES)   # A5 unchanged

    def test_reviewed_record_matches_approval(self):
        record = reg.REVIEWED_RECORDS[KEY]
        self.assertEqual(record["fixture_sha256"], SHA)
        self.assertEqual((record["compile_status"], record["passed"], record["failed_tests"]),
                         ("COMPILED", 6, [3, 6, 7, 10]))
        self.assertEqual({k: v["report_sha256"][:12] for k, v in reg.REVIEWED_RECORDS.items()
                          if k != KEY},
                         {"prime_correct": "62b170a47452", "prime_buggy_square": "9bfe61feeb19",
                          "prime_compile_error": "f70d5fa665e7"})          # A5 preserved

    def test_llm_worker_leaves_this_fixture_to_the_verified_path(self):
        from dev_tools import run_e2e_llm_worker as runner
        import llm_analysis_worker as w
        hashes = runner.protected_hashes()
        self.assertIn(SHA, hashes)
        job, skipped = w.choose_job([{"job_id": 9, "submission_id": 9, "source_sha256": SHA}],
                                    hashes)
        self.assertIsNone(job)
        self.assertEqual([s["job_id"] for s in skipped], [9])

    def test_recorded_preview_cli_only_replays_assessment_5(self):
        import inspect
        source = inspect.getsource(reg.main)
        self.assertIn("teh.assessment_for(k) == teh.ASSESSMENT_ID", source)


@unittest.skipUnless(HAVE_REPORT, "recorded Gate V report not present")
class RecordIntegrityTests(unittest.TestCase):

    def verify(self, report=None, digest=None, tests=TESTS, fixture_sha=SHA):
        r, d = reg.read_record(KEY)
        return reg.verify_record(KEY, report if report is not None else r, digest or d,
                                 tests, fixture_sha, Decimal("5.00"))

    def test_real_report_verifies(self):
        s = self.verify()
        self.assertEqual((s["tests_passed"], s["failed_test_numbers"], s["recorded_test_score"],
                          s["provenance"]), (6, [3, 6, 7, 10], "3.00", "trusted_fixture_recorded"))

    def test_every_run_completed_cleanly(self):
        report, _ = reg.read_record(KEY)
        self.assertEqual(report["compile"]["status"], "COMPILED")
        self.assertTrue(report["build_directory_removed"])
        for t in report["tests"]:
            self.assertEqual((t["status"], t["exit_code"], t["stderr"]), ("completed", 0, ""))
            self.assertLess(t["seconds"], teh.RUN_TIMEOUT_SECONDS)

    def test_failures_are_exact_32_bit_overflow(self):
        report, _ = reg.read_record(KEY)

        def wrap(x):
            return (x + 2**31) % 2**32 - 2**31
        for t, case in zip(report["tests"], TESTS):
            values = [int(v) for v in case["input_data"].split("\n")[1].split()]
            acc = 0
            for v in values:
                acc = wrap(acc + v)
            self.assertEqual(t["actual"], str(acc))          # deterministic wraparound
            self.assertEqual(t["passed"], acc == sum(values))

    def test_tampering_is_refused(self):
        report, _ = reg.read_record(KEY)
        bad = copy.deepcopy(report)
        bad["tests"][2]["passed"] = True
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify(report=bad)
        bad = copy.deepcopy(report)
        bad["tests"][0]["actual"] = "61"
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify(report=bad)
        with self.assertRaisesRegex(reg.RecordIntegrityError, "report file hash"):
            self.verify(digest="0" * 64)
        with self.assertRaisesRegex(reg.RecordIntegrityError, "fixture file changed"):
            self.verify(fixture_sha="1" * 64)
        with self.assertRaisesRegex(reg.RecordIntegrityError, "wrong assessment"):
            self.verify(report={**report, "assessment_id": 5})

    def test_changed_stored_tests_are_refused(self):
        changed = copy.deepcopy(TESTS)
        changed[0]["expected_output"] = "61\n"
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify(tests=changed)
        with self.assertRaises(reg.RecordIntegrityError):
            self.verify(tests=TESTS[:9])

    def test_replay_through_pipeline_without_execution(self):
        report, _ = reg.read_record(KEY)
        source = teh.fixture_path(KEY).read_text(encoding="utf-8")
        seen, writes = {}, []

        def grader(**kwargs):
            return parse_criterion_only_response({
                "criteria": [{"criterion_name": "Logic & Correctness", "mark": 1.5,
                              "explanation": "int sum overflows", "cited_failed_tests": [3]},
                             {"criterion_name": "Code Quality", "mark": 1.5,
                              "explanation": "Readable."}],
                "overall_feedback": "ok", "issues": [], "improvement_suggestions": []},
                kwargs["rubric"])

        def grade_rubric(context, rubric, alloc, src, evidence):
            seen["evidence"] = evidence
            return grade_rubric_with_ollama(context, rubric, alloc, src, evidence, grader=grader)

        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": -1, "source_code": source},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 6},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("50")},
            load_context=lambda a: {"problem": "Array sum", "input_format": "N then N ints",
                                    "output_format": "sum", "constraints": "1 <= N <= 100",
                                    "code_constraints": []},
            load_rubric=lambda a: RUBRIC, load_test_cases=lambda a: TESTS,
            get_sandbox=lambda: reg.RecordedEvidenceSandbox(report),
            grade_rubric=grade_rubric,
            complete=lambda *a, **k: writes.append((a, k)) or {"job_status": "simulated"},
            release=lambda *a: writes.append(("release", a)) or {"job_status": "PENDING"},
            allow_synthetic_evidence=True)
        boom = AssertionError("compilation/execution attempted")
        with patch.object(cpp_compilation_service, "compile_cpp_source", side_effect=boom), \
                patch("subprocess.run", side_effect=boom), patch("subprocess.Popen", side_effect=boom):
            result = gp.process_claimed_job({"job_id": 1, "submission_id": -1,
                                             "claim_token": str(uuid4())}, "test", deps)
        self.assertEqual(result["outcome"], "GRADED")
        self.assertEqual((result["score_breakdown"]["test_score"],
                          result["score_breakdown"]["suggested_score"]), ("3.00", "6.00"))
        self.assertEqual(seen["evidence"]["provenance"], "trusted_fixture_recorded")
        (args, kwargs), = writes
        self.assertEqual((args[5], args[6]), (6, 10))                 # real pass counts
        from evaluation_report import parse_report
        structured = parse_report(args[4])
        self.assertEqual(structured["evaluation_mode"], "VERIFIED_EXECUTION")
        self.assertEqual(structured["test_execution"]["failed_test_numbers"], [3, 6, 7, 10])


if __name__ == "__main__":
    unittest.main()
