"""
DEVELOPMENT-ONLY trusted C++ execution demo against the real 10 hidden test
cases of the fixture's own assessment (FIXTURE_ASSESSMENTS: 5 or 6).

    .venv\\Scripts\\python.exe dev_tools\\trusted_execution_harness.py --fixture prime_correct
        -> prints the plan only (no compilation, no execution)
    .venv\\Scripts\\python.exe dev_tools\\trusted_execution_harness.py --fixture prime_correct --execute
        -> refused unless the fixture's CURRENT sha256 is listed in
           dev_tools/trusted_fixtures/approved_for_execution.json

Hard limits
- Only the named fixtures in FIXTURES can be used. No path, file
  name or source text is ever accepted from the command line, a request
  or any other input. Nothing in the application imports this module.
- The fixture file is re-hashed immediately before compiling; the exact
  hash must be owner-approved.
- Database access is read-only (the preview harness's guard blocks every
  non-SELECT statement and every commit). No rows are written.
- This is NOT a sandbox. The program runs as the current Windows user.
  The safeguards here (wall-clock timeout, minimal environment, output
  caps, isolated temp folder, cleanup) are only acceptable because the
  programs are developer-written and reviewed. secure_execution_gate
  stays disabled and student code is never run.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cpp_compilation_service  # noqa: E402
from diagnostics_sanitizer import sanitize_compiler_diagnostics  # noqa: E402
from equal_weight_test_scoring import calculate_equal_weight_score  # noqa: E402
from grading_allocation import split_allocation  # noqa: E402
from student_grading_evidence_builder import build_student_grading_evidence  # noqa: E402
from test_case_policy import enforce_test_case_count  # noqa: E402

ASSESSMENT_ID = 5
FIXTURE_DIR = Path(__file__).resolve().parent / "trusted_fixtures"
APPROVAL_FILE = FIXTURE_DIR / "approved_for_execution.json"
REPORTS = Path(__file__).resolve().parent / "reports"

FIXTURES = {
    "prime_correct": "prime_correct.cpp",
    "prime_buggy_square": "prime_buggy_square.cpp",
    "prime_compile_error": "prime_compile_error.cpp",
    # Assessment 6 automatic-evaluation demo (needs its own owner approval).
    "array_sum_int_accumulator": "array_sum_int_accumulator.cpp",
}

# Each fixture runs ONLY against its own assessment's approved tests.
FIXTURE_ASSESSMENTS = {
    "prime_correct": 5,
    "prime_buggy_square": 5,
    "prime_compile_error": 5,
    "array_sum_int_accumulator": 6,
}

RUN_TIMEOUT_SECONDS = 2
MAX_STDOUT_CHARS = 64 * 1024
MAX_STDERR_CHARS = 4 * 1024
CREATE_NO_WINDOW = 0x08000000


class NotApproved(RuntimeError):
    pass


# ----------------------------------------------------------------------
# Approval
# ----------------------------------------------------------------------

def fixture_path(key):
    if key not in FIXTURES:
        raise NotApproved(f"Unknown fixture {key!r}; allowed: {sorted(FIXTURES)}")
    path = (FIXTURE_DIR / FIXTURES[key]).resolve()
    if path.parent != FIXTURE_DIR.resolve():
        raise NotApproved("Fixture outside the trusted fixture folder")
    return path


def assessment_for(key):
    fixture_path(key)                       # unknown keys are refused
    return FIXTURE_ASSESSMENTS[key]


def fixture_sha256(key):
    return hashlib.sha256(fixture_path(key).read_bytes()).hexdigest()


def load_approvals(path=APPROVAL_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    approved = {}
    for item in data.get("approved", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("fixture") in FIXTURES \
                and isinstance(item.get("sha256"), str):
            approved[item["fixture"]] = item["sha256"].lower()
    return approved


def require_approval(key, approvals=None):
    """Returns (source_text, sha256) only if this exact file is approved."""
    approvals = load_approvals() if approvals is None else approvals
    raw = fixture_path(key).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if approvals.get(key) != digest:
        raise NotApproved(
            f"{key} (sha256 {digest}) is not approved for execution; "
            f"add it to {APPROVAL_FILE.name} only with owner approval")
    return raw.decode("utf-8"), digest


# ----------------------------------------------------------------------
# Execution of ONE approved, already-compiled fixture
# ----------------------------------------------------------------------

def minimal_environment(compiler_path):
    """No inherited secrets: only what the MinGW runtime DLLs need."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    bin_dir = str(Path(compiler_path).resolve().parent) if compiler_path else ""
    return {"SystemRoot": system_root,
            "PATH": os.pathsep.join(p for p in (bin_dir,
                                                str(Path(system_root) / "System32"))
                                    if p)}


def default_run(executable, stdin_text, build_directory, env):
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(executable)], input=stdin_text.encode("utf-8"),
            capture_output=True, timeout=RUN_TIMEOUT_SECONDS,
            cwd=str(build_directory), env=env, shell=False,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exit_code": None, "stdout": "",
                "stderr": "", "seconds": round(time.perf_counter() - started, 4)}
    return {"status": "completed" if proc.returncode == 0 else "runtime_error",
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace")[:MAX_STDOUT_CHARS],
            "stderr": proc.stderr.decode("utf-8", errors="replace")[:MAX_STDERR_CHARS],
            "seconds": round(time.perf_counter() - started, 4)}


def run_fixture(key, tests, test_allocation, approvals=None,
                compile_fn=None, run_fn=None, compiler_path=None):
    """
    Compile ONE approved fixture, run it against the supplied tests,
    compare outputs and compute equal-weight test marks. Always cleans up.
    """

    source, digest = require_approval(key, approvals)
    assessment_id = assessment_for(key)
    enforce_test_case_count(len(tests), f"Assessment {assessment_id}")
    compile_fn = compile_fn or cpp_compilation_service.compile_cpp_source
    run_fn = run_fn or default_run
    compiler_path = compiler_path or shutil.which("g++")

    report = {"fixture": key, "sha256": digest, "assessment_id": assessment_id,
              "label": "TRUSTED DEVELOPER FIXTURE - not a student submission"}
    started = time.perf_counter()
    compiled = compile_fn(source)
    report["compile"] = {"status": compiled["status"],
                         "seconds": round(time.perf_counter() - started, 3),
                         "diagnostics": sanitize_compiler_diagnostics(
                             compiled.get("diagnostics") or "")}
    build_dir = compiled.get("build_directory")

    try:
        if compiled["status"] != "COMPILED":
            report["outcome"] = compiled["status"]
            return report

        env = minimal_environment(compiler_path)
        raw = []
        for test in tests:
            result = run_fn(compiled["executable_path"],
                            test["input_data"] + "\n", build_dir, env)
            raw.append({"test_number": test["test_number"], **result})

        evidence = build_student_grading_evidence(
            [{"test_number": t["test_number"], "input_data": t["input_data"],
              "expected_output": t["expected_output"]} for t in tests],
            [{"test_number": r["test_number"], "status": r["status"],
              "stdout": r["stdout"] if r["status"] == "completed" else None}
             for r in raw])
        passed = evidence["tests_passed"]
        score = calculate_equal_weight_score(passed, len(tests),
                                             test_allocation)["test_score"]

        by_number = {c["test_number"]: c for c in evidence["cases"]}
        report["tests"] = [{
            "test_number": r["test_number"],
            "input": by_number[r["test_number"]]["input_data"],
            "expected": by_number[r["test_number"]]["expected_output"].strip(),
            "actual": r["stdout"].strip(),
            "status": r["status"], "exit_code": r["exit_code"],
            "stderr": sanitize_compiler_diagnostics(r["stderr"].strip()),
            "seconds": r["seconds"],
            "passed": by_number[r["test_number"]]["passed"]} for r in raw]
        report["passed"] = passed
        report["failed"] = len(tests) - passed
        report["test_score"] = score
        report["test_marks_allocated"] = str(Decimal(str(test_allocation)))
        report["formula"] = f"ROUND_HALF_UP({test_allocation} x {passed} / 10, 2)"
        report["total_run_seconds"] = round(sum(r["seconds"] for r in raw), 4)
        report["outcome"] = "EXECUTED"
        return report
    finally:
        if build_dir:
            cpp_compilation_service.cleanup_cpp_build(build_dir)
        report["build_directory_removed"] = (not build_dir) or not Path(build_dir).exists()


# ----------------------------------------------------------------------
# Command line (fixture KEY only)
# ----------------------------------------------------------------------

def load_assessment(assessment_id):
    from dev_tools.grading_preview_harness import install_read_only_guard
    import grading_configuration_service
    import grading_test_case_loader
    install_read_only_guard()
    tests = grading_test_case_loader.load_grading_test_cases(assessment_id)
    config = grading_configuration_service.load_grading_configuration(assessment_id)
    test_alloc, _ = split_allocation(config["total_marks"],
                                     config["validation_percentage"])
    return tests, test_alloc


def load_assessment_5():
    return load_assessment(ASSESSMENT_ID)


def describe_plan(key):
    compiler = shutil.which("g++")
    return {
        "fixture": key, "file": str(fixture_path(key)),
        "sha256": fixture_sha256(key),
        "approved": load_approvals().get(key) == fixture_sha256(key),
        "compile_command": [compiler, "-std=c++17", "-O0",
                            "-fdiagnostics-color=never",
                            r"%TEMP%\fyp_cpp_build_<random>\student_solution.cpp",
                            "-o", r"%TEMP%\fyp_cpp_build_<random>\student_solution.exe"],
        "run_command": [r"%TEMP%\fyp_cpp_build_<random>\student_solution.exe"],
        "per_test": f"stdin = test input + newline; timeout {RUN_TIMEOUT_SECONDS}s; "
                    f"cwd = build folder; env = SystemRoot + PATH(compiler bin, "
                    f"System32) only; stdout cap {MAX_STDOUT_CHARS} chars",
        "tests": f"10 hidden test cases of Assessment {assessment_for(key)}, "
                 f"loaded read-only",
    }


def main():
    parser = argparse.ArgumentParser(description="Trusted fixture execution demo")
    parser.add_argument("--fixture", required=True, choices=sorted(FIXTURES))
    parser.add_argument("--execute", action="store_true",
                        help="compile and run (requires owner approval entry)")
    args = parser.parse_args()

    if not args.execute:
        print(json.dumps(describe_plan(args.fixture), indent=2))
        print("PLAN ONLY - nothing compiled or executed")
        return

    assessment_id = assessment_for(args.fixture)
    tests, test_alloc = (load_assessment_5() if assessment_id == ASSESSMENT_ID
                         else load_assessment(assessment_id))
    try:
        report = run_fixture(args.fixture, tests, test_alloc)
    except NotApproved as error:
        sys.exit(f"REFUSED: {error}")

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"trusted_execution_{args.fixture}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "tests"}, indent=2))
    for t in report.get("tests", []):
        print(f"  #{t['test_number']:>2} N={t['input']:<8} expected={t['expected']:<4} "
              f"actual={t['actual']:<4} {t['status']:<13} exit={t['exit_code']} "
              f"{t['seconds'] * 1000:.1f} ms {'PASS' if t['passed'] else 'FAIL'}")
    print("report:", out)


if __name__ == "__main__":
    main()
