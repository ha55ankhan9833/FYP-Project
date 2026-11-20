"""
cpp_compilation_service -> sandbox-contract mapping -> grading pipeline.
subprocess.run is MOCKED everywhere: no compiler runs, nothing executes.
"""

import ast
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import BACKEND

import cpp_compilation_service as ccs
import grading_pipeline as gp
import secure_execution_gate as gate
from compilation_contract import INFRASTRUCTURE_STATUSES, to_sandbox_compile_result
from secure_execution_gate import SandboxInfrastructureError

FIXTURES = Path(BACKEND) / "dev_tools" / "trusted_fixtures"


def mocked_success(command, **kwargs):
    Path(command[command.index("-o") + 1]).write_bytes(b"MOCK_EXE")
    return subprocess.CompletedProcess(command, 0)


def mocked_failure(command, **kwargs):
    kwargs["stdout"].write(b"main.cpp:5:5: error: expected ',' or ';' before 'std'")
    return subprocess.CompletedProcess(command, 1)


def compile_mocked(source, side_effect, which="g++.exe"):
    with patch.object(ccs.shutil, "which", return_value=which), \
         patch.object(ccs.subprocess, "run", side_effect=side_effect) as run:
        return ccs.compile_cpp_source(source), run


class CompileServiceMockTests(unittest.TestCase):
    def test_success_maps_to_compiled_and_runs_only_the_compiler(self):
        result, run = compile_mocked((FIXTURES / "sum_two_numbers.cpp").read_text(),
                                     mocked_success)
        try:
            self.assertEqual(result["status"], "COMPILED")
            self.assertEqual(run.call_count, 1)              # compiler only
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["g++.exe", "-std=c++17", "-O0",
                                        "-fdiagnostics-color=never"])
            self.assertTrue(argv[-1].endswith("student_solution.exe"))
            self.assertIs(run.call_args.kwargs["shell"], False)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            mapped = to_sandbox_compile_result(result)
            self.assertEqual(mapped["status"], "COMPILED")
            self.assertEqual(mapped["artifact"], result["build_directory"])
        finally:
            ccs.cleanup_cpp_build(result["build_directory"])
        self.assertFalse(Path(result["build_directory"]).exists())

    def test_compiler_error_is_a_student_verdict_and_cleaned_up(self):
        result, _ = compile_mocked(
            (FIXTURES / "compile_error_missing_semicolon.cpp").read_text(),
            mocked_failure)
        self.assertEqual(result["status"], "COMPILATION_FAILED")
        self.assertIsNone(result["build_directory"])
        mapped = to_sandbox_compile_result(result)
        self.assertEqual(mapped["status"], "COMPILATION_FAILED")
        self.assertIn("error", mapped["diagnostics"])

    def test_infrastructure_outcomes_never_become_student_verdicts(self):
        cases = {
            "COMPILER_UNAVAILABLE": dict(source="int main(){}", side_effect=None,
                                         which=None),
            "COMPILATION_TIMEOUT": dict(source="int main(){}",
                                        side_effect=subprocess.TimeoutExpired("g++", 15)),
            "COMPILATION_ERROR": dict(source="int main(){}",
                                      side_effect=OSError("disk full")),
            "SOURCE_TOO_LARGE": dict(source="A" * 100_001, side_effect=None),
            "INVALID_SOURCE": dict(source="   ", side_effect=None),
        }
        for status, kw in cases.items():
            with self.subTest(status):
                result, _ = compile_mocked(kw["source"], kw["side_effect"],
                                           kw.get("which", "g++.exe"))
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["build_directory"])
                with self.assertRaises(SandboxInfrastructureError):
                    to_sandbox_compile_result(result)
        self.assertEqual(set(cases), INFRASTRUCTURE_STATUSES)

    def test_inconsistent_or_unknown_results_are_infrastructure(self):
        for bad in (None, {"status": "COMPILED", "success": False},
                    {"status": "COMPILED", "success": True, "build_directory": None},
                    {"status": "COMPILATION_FAILED", "success": True},
                    {"status": "SOMETHING_NEW"}):
            with self.subTest(bad=bad), self.assertRaises(SandboxInfrastructureError):
                to_sandbox_compile_result(bad)

    def test_finding_unicode_source_can_pass_submission_but_exceed_byte_limit(self):
        # Submission allows 100,000 characters; the compiler allows 100,000
        # BYTES. Non-ASCII text can pass the first and fail the second; the
        # mapping makes that an infrastructure result (never a zero).
        source = "// " + "é" * 60_000 + "\nint main(){}"
        self.assertLessEqual(len(source), 100_000)
        result, _ = compile_mocked(source, None)
        self.assertEqual(result["status"], "SOURCE_TOO_LARGE")


class ContractSandbox:
    """Test double that speaks the gate contract via the mapping."""

    def __init__(self, compile_result):
        self.compile_result = compile_result
        self.ran = False

    def compile(self, source):
        return to_sandbox_compile_result(self.compile_result)

    def run_tests(self, artifact, cases):
        self.ran = True
        return [{"test_number": c["test_number"], "status": "completed",
                 "stdout": "OK\n"} for c in cases]

    def cleanup(self, artifact):
        pass


def run_pipeline(compile_result):
    calls = {"complete": [], "compile_fail": [], "release": [], "ai": 0}
    sandbox = ContractSandbox(compile_result)
    deps = gp.GradingDependencies(
        prepare=lambda *a: {"job_id": 1, "submission_id": 1,
                            "source_code": "int main(){}"},
        renew_lease=lambda *a: "ok",
        load_assessment_marks=lambda s: {"assessment_id": 5},
        load_config=lambda a: {"total_marks": Decimal("10"),
                               "validation_percentage": Decimal("100")},
        load_context=lambda a: {"problem": "p", "input_format": "i",
                                "output_format": "o", "constraints": "c",
                                "code_constraints": []},
        load_rubric=lambda a: [],
        load_test_cases=lambda a: [{"test_number": n, "input_data": str(n),
                                    "expected_output": "OK\n"}
                                   for n in range(1, 11)],
        get_sandbox=lambda: sandbox,
        grade_rubric=lambda *a: calls.__setitem__("ai", calls["ai"] + 1),
        complete=lambda *a, **k: calls["complete"].append(a) or {"job_status": "COMPLETED"},
        complete_compilation_failure=lambda *a: calls["compile_fail"].append(a)
        or {"job_status": "COMPLETED"},
        release=lambda *a: calls["release"].append(a) or {"job_status": "PENDING"})
    result = gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                     "claim_token": str(uuid4())}, "w", deps)
    return result, calls, sandbox


class PipelineConnectionTests(unittest.TestCase):
    def test_compiled_runs_exactly_10_tests_and_is_verified(self):
        result, calls, sandbox = run_pipeline({
            "success": True, "status": "COMPILED", "diagnostics": "",
            "build_directory": "X", "executable_path": "X/a.exe"})
        self.assertTrue(sandbox.ran)
        self.assertEqual(result["outcome"], "GRADED")
        self.assertEqual(result["verification_report"]["tests"]["run"], 10)
        self.assertEqual(calls["complete"][0][3], Decimal("10.00"))

    def test_compiler_verdict_records_failure_without_llm_or_tests(self):
        result, calls, sandbox = run_pipeline({
            "success": False, "status": "COMPILATION_FAILED",
            "diagnostics": "error: expected ';'", "build_directory": None})
        self.assertEqual(result["outcome"], "COMPILATION_FAILED")
        self.assertEqual(len(calls["compile_fail"]), 1)
        self.assertFalse(sandbox.ran)
        self.assertEqual(calls["ai"], 0)

    def test_infrastructure_compile_failure_releases_without_zero(self):
        for status in sorted(INFRASTRUCTURE_STATUSES):
            with self.subTest(status):
                result, calls, sandbox = run_pipeline(
                    {"success": False, "status": status, "diagnostics": "",
                     "build_directory": None})
                self.assertEqual(result["error_code"], "SANDBOX_COMPILE_FAILURE")
                self.assertFalse(calls["complete"] or calls["compile_fail"])
                self.assertEqual(len(calls["release"]), 1)


class StillDisabledTests(unittest.TestCase):
    def test_gate_disabled_and_host_compiler_not_wired(self):
        gate.register_secure_sandbox(None)
        self.assertIsNone(gate.get_secure_sandbox())
        for module in ("grading_pipeline", "secure_execution_gate",
                       "grading_worker", "app", "student_submission_routes"):
            tree = ast.parse((Path(BACKEND) / f"{module}.py").read_text("utf-8-sig"))
            imported = {n.module for n in ast.walk(tree)
                        if isinstance(n, ast.ImportFrom)} | {
                a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
            self.assertFalse(imported & {"cpp_compilation_service",
                                         "compilation_contract", "cpp_runner"},
                             module)

    def test_student_compilation_orchestrator_stays_disabled(self):
        import submission_compilation_orchestrator as sco
        with self.assertRaises(RuntimeError):
            sco.process_submission_compilation(1, "int main(){}")


if __name__ == "__main__":
    unittest.main()
