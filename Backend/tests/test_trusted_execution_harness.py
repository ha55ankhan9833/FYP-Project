"""
dev_tools/trusted_execution_harness.py - MOCKED ONLY. Compilation and
execution are replaced by stand-ins (and subprocess.run is mocked where
the real runner is exercised): nothing is compiled or executed here.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.fakes import BACKEND

import app as app_module
from dev_tools import trusted_execution_harness as teh

PLAN = json.loads((Path(BACKEND) / "fixtures/new_assessment_csc101/"
                   "migration_to_10_tests.json").read_text())
TESTS = [{"test_number": p["test_number"], "input_data": p["input_data"],
          "expected_output": p["expected_output"]} for p in PLAN["proposed_10"]]

PINNED = {   # reported to the owner for approval; any edit changes these
    "prime_correct": "f258e59ba3daefb8a4142beb2b15094356b16c9fc95ed61b0e2a34946e66b824",
    "prime_buggy_square": "d52fba8be7c1f2df6a5faca49c85848e6e949ee40fea7f287cd6e92576cd3bdd",
    "prime_compile_error": "89b1c9ebfe88c7d8d61a9a5301289fcd0e871a21d40f9768f127ef9a7231b1e1",
}


def model_correct(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d += 1
    return True


def model_buggy(n):
    prime = n >= 2
    d = 2
    while d * d < n:
        if n % d == 0:
            return False
        d += 1
    return prime


def fake_compile(status="COMPILED"):
    calls = []

    def compile_fn(source):
        calls.append(source)
        if status != "COMPILED":
            return {"success": False, "status": status, "executable_path": None,
                    "build_directory": None,
                    "diagnostics": r"C:\Users\Lenovo\AppData\Local\Temp\fyp_cpp_build_q"
                                   r"\student_solution.cpp:6:20: error: 'isPrime' was "
                                   "not declared in this scope"}
        build = Path(tempfile.mkdtemp(prefix="fyp_cpp_build_"))
        exe = build / "student_solution.exe"
        exe.write_bytes(b"MOCK")
        return {"success": True, "status": "COMPILED", "executable_path": str(exe),
                "build_directory": str(build), "diagnostics": ""}
    compile_fn.calls = calls
    return compile_fn


def fake_run(model, overrides=None):
    calls = []

    def run_fn(executable, stdin_text, build_directory, env):
        n = int(stdin_text.strip())
        calls.append({"n": n, "env": env, "cwd": build_directory,
                      "exe": executable})
        if overrides and n in overrides:
            return overrides[n]
        return {"status": "completed", "exit_code": 0,
                "stdout": ("YES" if model(n) else "NO") + "\n", "stderr": "",
                "seconds": 0.004}
    run_fn.calls = calls
    return run_fn


def approved(*keys):
    return {k: PINNED[k] for k in keys}


class FixtureTests(unittest.TestCase):
    def test_fixture_hashes_match_the_reported_values(self):
        for key, digest in PINNED.items():
            self.assertEqual(teh.fixture_sha256(key), digest, key)

    def test_approval_file_holds_only_the_gate_v_fixture(self):
        self.assertEqual(teh.load_approvals(), {
            "array_sum_int_accumulator":
                "ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4"})
        for key in ("prime_correct", "prime_buggy_square", "prime_compile_error"):
            self.assertNotIn(key, teh.load_approvals())


class ExecutionTests(unittest.TestCase):
    def test_correct_solution_passes_10_of_10(self):
        compile_fn, run_fn = fake_compile(), fake_run(model_correct)
        r = teh.run_fixture("prime_correct", TESTS, "5.00",
                            approvals=approved("prime_correct"),
                            compile_fn=compile_fn, run_fn=run_fn,
                            compiler_path=r"C:\toolchain\bin\g++.exe")
        self.assertEqual((r["outcome"], r["passed"], r["test_score"]),
                         ("EXECUTED", 10, "5.00"))
        self.assertEqual(len(run_fn.calls), 10)
        self.assertTrue(r["build_directory_removed"])

    def test_buggy_solution_fails_the_square_cases(self):
        r = teh.run_fixture("prime_buggy_square", TESTS, "5.00",
                            approvals=approved("prime_buggy_square"),
                            compile_fn=fake_compile(), run_fn=fake_run(model_buggy))
        failed = [t["input"] for t in r["tests"] if not t["passed"]]
        self.assertEqual(failed, ["4", "994009"])
        self.assertEqual((r["passed"], r["test_score"]), (8, "4.00"))
        self.assertEqual(r["formula"], "ROUND_HALF_UP(5.00 x 8 / 10, 2)")

    def test_compilation_failure_never_runs_anything(self):
        run_fn = fake_run(model_correct)
        r = teh.run_fixture("prime_compile_error", TESTS, "5.00",
                            approvals=approved("prime_compile_error"),
                            compile_fn=fake_compile("COMPILATION_FAILED"),
                            run_fn=run_fn)
        self.assertEqual(r["outcome"], "COMPILATION_FAILED")
        self.assertEqual(run_fn.calls, [])
        self.assertNotIn("Lenovo", r["compile"]["diagnostics"])
        self.assertIn("student_solution.cpp:6:20: error: 'isPrime' was not "
                      "declared", r["compile"]["diagnostics"])

    def test_timeouts_runtime_errors_and_wrong_output(self):
        overrides = {
            97: {"status": "timeout", "exit_code": None, "stdout": "",
                 "stderr": "", "seconds": 2.0},
            7919: {"status": "runtime_error", "exit_code": 3221225477, "stdout": "",
                   "stderr": r"crash in C:\Users\Lenovo\x", "seconds": 0.01},
            2: {"status": "completed", "exit_code": 0, "stdout": "yes\n",
                "stderr": "", "seconds": 0.01},
        }
        r = teh.run_fixture("prime_correct", TESTS, "5.00",
                            approvals=approved("prime_correct"),
                            compile_fn=fake_compile(),
                            run_fn=fake_run(model_correct, overrides))
        by_input = {t["input"]: t for t in r["tests"]}
        self.assertEqual(by_input["97"]["status"], "timeout")
        self.assertEqual(by_input["7919"]["status"], "runtime_error")
        self.assertNotIn("Lenovo", by_input["7919"]["stderr"])
        self.assertFalse(by_input["2"]["passed"])            # case-sensitive
        self.assertEqual((r["passed"], r["test_score"]), (7, "3.50"))

    def test_cleanup_even_if_running_raises(self):
        compile_fn = fake_compile()

        def broken(*a):
            raise OSError("launch failed")
        with self.assertRaises(OSError):
            teh.run_fixture("prime_correct", TESTS, "5.00",
                            approvals=approved("prime_correct"),
                            compile_fn=compile_fn, run_fn=broken)
        leftovers = list(Path(tempfile.gettempdir()).glob("fyp_cpp_build_*"))
        self.assertEqual(leftovers, [])

    def test_minimal_environment_passed_to_program(self):
        run_fn = fake_run(model_correct)
        teh.run_fixture("prime_correct", TESTS, "5.00",
                        approvals=approved("prime_correct"),
                        compile_fn=fake_compile(), run_fn=run_fn,
                        compiler_path=r"C:\toolchain\bin\g++.exe")
        env = run_fn.calls[0]["env"]
        self.assertEqual(set(env), {"SystemRoot", "PATH"})
        self.assertIn(r"C:\toolchain\bin", env["PATH"])
        self.assertTrue(run_fn.calls[0]["exe"].endswith("student_solution.exe"))


class RefusalTests(unittest.TestCase):
    def test_unapproved_tampered_or_unknown_fixtures_refused_before_compiling(self):
        cases = [
            ("not approved", "prime_correct", {}),
            ("approved hash differs", "prime_correct", {"prime_correct": "0" * 64}),
            ("approval for another fixture", "prime_correct",
             approved("prime_buggy_square")),
            ("unknown key", "student_upload", approved("prime_correct")),
            ("path instead of key", "..\\..\\app.py", approved("prime_correct")),
        ]
        for label, key, approvals in cases:
            with self.subTest(label):
                compile_fn = fake_compile()
                with self.assertRaises(teh.NotApproved):
                    teh.run_fixture(key, TESTS, "5.00", approvals=approvals,
                                    compile_fn=compile_fn, run_fn=fake_run(model_correct))
                self.assertEqual(compile_fn.calls, [])

    def test_wrong_test_count_refused_before_compiling(self):
        compile_fn = fake_compile()
        with self.assertRaises(ValueError):
            teh.run_fixture("prime_correct", TESTS[:9], "5.00",
                            approvals=approved("prime_correct"),
                            compile_fn=compile_fn, run_fn=fake_run(model_correct))
        self.assertEqual(compile_fn.calls, [])

    def test_command_line_accepts_only_fixture_keys(self):
        for bad in (["--fixture", "my_upload.cpp"], ["--fixture", r"C:\x.cpp"], []):
            with self.subTest(bad=bad), \
                 patch("sys.argv", ["trusted_execution_harness.py", *bad]), \
                 patch("sys.stderr"), self.assertRaises(SystemExit):
                teh.main()

    def test_plan_mode_compiles_and_runs_nothing(self):
        with patch.object(teh, "run_fixture") as run, \
             patch.object(teh.cpp_compilation_service, "compile_cpp_source") as comp, \
             patch("sys.argv", ["x", "--fixture", "prime_correct"]), \
             patch("builtins.print"):
            teh.main()
        run.assert_not_called()
        comp.assert_not_called()

    def test_execute_mode_refused_while_approval_file_is_empty(self):
        with patch.object(teh, "load_assessment_5", return_value=(TESTS, "5.00")), \
             patch.object(teh.cpp_compilation_service, "compile_cpp_source") as comp, \
             patch("sys.argv", ["x", "--fixture", "prime_correct", "--execute"]):
            with self.assertRaises(SystemExit) as stop:
                teh.main()
        self.assertIn("not approved", str(stop.exception.code))
        comp.assert_not_called()


class DefaultRunnerMockTests(unittest.TestCase):
    """Real default_run with subprocess.run MOCKED (nothing launches)."""

    def test_arguments_and_status_mapping(self):
        with patch.object(teh.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, b"YES\n", b"")) as run:
            r = teh.default_run(r"C:\b\student_solution.exe", "97\n", r"C:\b",
                                {"SystemRoot": "C:\\Windows", "PATH": "x"})
        kwargs = run.call_args.kwargs
        self.assertEqual(run.call_args.args[0], [r"C:\b\student_solution.exe"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], teh.RUN_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["cwd"], r"C:\b")
        self.assertEqual(kwargs["input"], b"97\n")
        self.assertEqual((r["status"], r["stdout"]), ("completed", "YES\n"))

        with patch.object(teh.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 3, b"", b"boom")):
            self.assertEqual(teh.default_run("x", "1\n", "c", {})["status"],
                             "runtime_error")
        with patch.object(teh.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("x", 2)):
            self.assertEqual(teh.default_run("x", "1\n", "c", {})["status"], "timeout")

    def test_output_is_capped(self):
        with patch.object(teh.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, b"A" * (teh.MAX_STDOUT_CHARS + 5000), b"")):
            r = teh.default_run("x", "1\n", "c", {})
        self.assertEqual(len(r["stdout"]), teh.MAX_STDOUT_CHARS)


class NoProductionIntegrationTests(unittest.TestCase):
    def test_no_route_and_no_application_import(self):
        import ast
        for rule in app_module.app.url_map.iter_rules():
            for word in ("trusted", "fixture", "harness", "execute"):
                self.assertNotIn(word, rule.rule.lower())
        for path in Path(BACKEND).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom)
                         else [])
                self.assertFalse(any("trusted_execution_harness" in n for n in names),
                                 path.name)

    def test_gate_stays_disabled(self):
        import secure_execution_gate as gate
        gate.register_secure_sandbox(None)
        self.assertIsNone(gate.get_secure_sandbox())


if __name__ == "__main__":
    unittest.main()
