"""
DEVELOPMENT-ONLY: grade the RECORDED outputs of the approved trusted C++
fixture runs through the existing grading pipeline.

    .venv\\Scripts\\python.exe dev_tools\\recorded_evidence_grading.py
    .venv\\Scripts\\python.exe dev_tools\\recorded_evidence_grading.py --fixture prime_buggy_square

Three kinds of evidence exist in this project. They are never mixed:
  synthetic_development     modelled outputs (grading_preview_harness)
  trusted_fixture_recorded  THIS MODULE: real stdout / exit codes recorded
                            when owner-approved developer fixtures were
                            compiled and run once by trusted_execution_harness
  sandbox_execution         real student submissions in a verified sandbox
                            (production; still disabled)

Nothing is compiled or executed here. RecordedEvidenceSandbox replays the
verified report: compile() returns the recorded compiler verdict and
run_tests() returns the recorded stdout. It is refused by
secure_execution_gate and by the production pipeline configuration.

Reused unchanged: grading_pipeline.process_claimed_job (evidence
validation, Python equal-weight scoring, Ollama criterion grading,
consistency checks, verification layer, compilation-failure policy) and
the read-only wiring of grading_preview_harness. SQL Server is read-only;
every write step is captured in memory.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import compilation_contract  # noqa: E402
import grading_test_case_loader  # noqa: E402
import ollama_grading_service  # noqa: E402
from dev_tools import grading_preview_harness as preview  # noqa: E402
from dev_tools import trusted_execution_harness as teh  # noqa: E402
from equal_weight_test_scoring import calculate_equal_weight_score  # noqa: E402
from evaluation_review_service import hidden_values, student_safe_feedback  # noqa: E402
from grading_pipeline import process_claimed_job  # noqa: E402
from output_comparison import outputs_match  # noqa: E402
from secure_execution_gate import SandboxInfrastructureError  # noqa: E402
from test_case_policy import REQUIRED_TEST_CASE_COUNT  # noqa: E402

PROVENANCE = "trusted_fixture_recorded"
LABEL = ("TRUSTED DEVELOPER FIXTURE - RECORDED EXECUTION EVIDENCE - "
         "not a student submission")
REPORTS = teh.REPORTS

# The reviewed execution records: report file SHA-256 and the fixture
# SHA-256 the owner approved for the single execution run.
REVIEWED_RECORDS = {
    "prime_correct": {
        "report": "trusted_execution_prime_correct_20261117_124510.json",
        "report_sha256": "62b170a47452bc58b87c1b835311f2d7932e75ecc493a6c14bd9734e96bfa793",
        "fixture_sha256": "f258e59ba3daefb8a4142beb2b15094356b16c9fc95ed61b0e2a34946e66b824",
        "compile_status": "COMPILED", "passed": 10, "failed_tests": []},
    "prime_buggy_square": {
        "report": "trusted_execution_prime_buggy_square_20261117_124511.json",
        "report_sha256": "9bfe61feeb1919220b4ebca00af2425f3bd72e48b46d836b7d9e663a98cbdff8",
        "fixture_sha256": "d52fba8be7c1f2df6a5faca49c85848e6e949ee40fea7f287cd6e92576cd3bdd",
        "compile_status": "COMPILED", "passed": 8, "failed_tests": [6, 9]},
    "prime_compile_error": {
        "report": "trusted_execution_prime_compile_error_20261117_124512.json",
        "report_sha256": "f70d5fa665e79faae02d434fbaf2270517f3e3dd9e58e753991d5cc7a04838b8",
        "fixture_sha256": "89b1c9ebfe88c7d8d61a9a5301289fcd0e871a21d40f9768f127ef9a7231b1e1",
        "compile_status": "COMPILATION_FAILED", "passed": None, "failed_tests": None},
}

RUN_STATUSES = {"completed", "runtime_error", "timeout"}
# A genuine compiler verdict on the student's file names a location in it.
_LOCATED_ERROR = re.compile(r"^student_solution\.cpp:\d+:\d+: (fatal )?error: ",
                            re.MULTILINE)


class RecordIntegrityError(RuntimeError):
    pass


def _require(condition, message):
    if not condition:
        raise RecordIntegrityError(message)


# ----------------------------------------------------------------------
# Record verification (pure: no DB, no execution)
# ----------------------------------------------------------------------

def classify_compile_record(compile_record):
    """
    STUDENT_CODE_COMPILATION_ERROR  genuine compiler verdict on the source
    COMPILED                        tests may be replayed
    INFRASTRUCTURE_FAILURE          compiler missing / timeout / OS error /
                                    a failure with no located source error
    """
    status = (compile_record or {}).get("status")
    if status == "COMPILED":
        return "COMPILED"
    if status == "COMPILATION_FAILED" and _LOCATED_ERROR.search(
            compile_record.get("diagnostics") or ""):
        return "STUDENT_CODE_COMPILATION_ERROR"
    return "INFRASTRUCTURE_FAILURE"


def verify_record(key, report, report_sha256, tests, fixture_sha256,
                  test_allocation):
    """
    Check a recorded report against the reviewed record and against the
    CURRENT approved test cases. Pass/fail and marks are re-derived here
    from the recorded stdout, never taken from the report.
    """
    reviewed = REVIEWED_RECORDS.get(key)
    _require(reviewed is not None, f"No reviewed record for {key!r}")
    _require(report_sha256 == reviewed["report_sha256"],
             f"{key}: report file hash {report_sha256} differs from the "
             f"reviewed record")
    _require(report.get("fixture") == key, f"{key}: fixture name mismatch")
    _require(report.get("sha256") == reviewed["fixture_sha256"],
             f"{key}: recorded fixture hash differs from the approved hash")
    _require(fixture_sha256 == reviewed["fixture_sha256"],
             f"{key}: fixture file changed since it was executed")
    _require(report.get("assessment_id") == teh.assessment_for(key),
             f"{key}: wrong assessment")
    _require(report.get("label") ==
             "TRUSTED DEVELOPER FIXTURE - not a student submission",
             f"{key}: report is not labelled as a trusted fixture")
    _require(report.get("build_directory_removed") is True,
             f"{key}: build directory cleanup was not confirmed")

    compile_record = report.get("compile") or {}
    classification = classify_compile_record(compile_record)
    _require(compile_record.get("status") == reviewed["compile_status"],
             f"{key}: compile status differs from the reviewed record")
    summary = {"fixture": key, "provenance": PROVENANCE,
               "report_file": reviewed["report"],
               "report_sha256": report_sha256,
               "fixture_sha256": fixture_sha256,
               "compile_status": compile_record.get("status"),
               "compile_classification": classification,
               "compile_seconds": compile_record.get("seconds")}

    if classification != "COMPILED":
        _require(report.get("outcome") == compile_record.get("status"),
                 f"{key}: outcome inconsistent with compile status")
        _require("tests" not in report,
                 f"{key}: tests recorded for a program that did not compile")
        return summary

    _require(report.get("outcome") == "EXECUTED", f"{key}: not executed")
    recorded = report.get("tests") or []
    _require(len(recorded) == REQUIRED_TEST_CASE_COUNT == len(tests),
             f"{key}: expected exactly {REQUIRED_TEST_CASE_COUNT} tests")
    by_number = {t["test_number"]: t for t in tests}
    _require(sorted(r.get("test_number") for r in recorded) == sorted(by_number),
             f"{key}: recorded test numbers differ from the approved tests")

    passed_numbers, failed_numbers = [], []
    for r in recorded:
        test = by_number[r["test_number"]]
        _require(r.get("input") == test["input_data"].strip() or
                 r.get("input") == test["input_data"],
                 f"{key}: test {r['test_number']} input differs from database")
        _require(r.get("expected") == test["expected_output"].strip(),
                 f"{key}: test {r['test_number']} expected output differs")
        _require(r.get("status") in RUN_STATUSES,
                 f"{key}: test {r['test_number']} has unknown status")
        _require(isinstance(r.get("actual"), str),
                 f"{key}: test {r['test_number']} has no recorded stdout")
        derived = (r["status"] == "completed" and
                   outputs_match(r["actual"], test["expected_output"]))
        _require(derived == r.get("passed"),
                 f"{key}: test {r['test_number']} recorded pass/fail does not "
                 f"match the recorded stdout")
        (passed_numbers if derived else failed_numbers).append(r["test_number"])

    passed = len(passed_numbers)
    _require(passed == report.get("passed") == reviewed["passed"],
             f"{key}: pass count differs from the reviewed record")
    _require(sorted(failed_numbers) == reviewed["failed_tests"],
             f"{key}: failed tests differ from the reviewed record")
    score = calculate_equal_weight_score(passed, len(tests), test_allocation)
    _require(str(score["test_score"]) == report.get("test_score"),
             f"{key}: recorded test score differs from the Python calculation")
    summary.update({"tests_passed": passed, "tests_failed": len(failed_numbers),
                    "failed_test_numbers": sorted(failed_numbers),
                    "recorded_test_score": report["test_score"],
                    "recorded_run_seconds": report.get("total_run_seconds")})
    return summary


def read_record(key):
    reviewed = REVIEWED_RECORDS[key]
    path = REPORTS / reviewed["report"]
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


# ----------------------------------------------------------------------
# Replay "sandbox" (never compiles, never executes)
# ----------------------------------------------------------------------

class RecordedEvidenceSandbox:
    """
    Replays a verified trusted-fixture report. Declares its provenance so
    the pipeline labels the evidence correctly and secure_execution_gate
    refuses to register it as a production sandbox.
    """

    synthetic = False
    evidence_provenance = PROVENANCE

    def __init__(self, report):
        self._report = report
        self.compile_calls = 0
        self.run_calls = 0

    def compile(self, source_code):
        self.compile_calls += 1
        record = self._report["compile"]
        classification = classify_compile_record(record)
        if classification == "COMPILED":
            return {"status": "COMPILED", "artifact": "recorded-not-compiled",
                    "diagnostics": record.get("diagnostics") or ""}
        if classification == "STUDENT_CODE_COMPILATION_ERROR":
            # Same mapping as a live compiler result would get.
            return compilation_contract.to_sandbox_compile_result(
                {"status": "COMPILATION_FAILED", "success": False,
                 "diagnostics": record.get("diagnostics") or ""})
        raise SandboxInfrastructureError(
            f"Recorded compile status {record.get('status')!r} is not a "
            f"confirmed student-code compilation error")

    def run_tests(self, artifact, cases):
        self.run_calls += 1
        recorded = {t["test_number"]: t for t in self._report["tests"]}
        return [{"test_number": c["test_number"],
                 "status": recorded[c["test_number"]]["status"],
                 "stdout": (recorded[c["test_number"]]["actual"]
                            if recorded[c["test_number"]]["status"] == "completed"
                            else None)}
                for c in cases]

    def cleanup(self, artifact):
        return None


# ----------------------------------------------------------------------
# Grade one recorded report through the existing pipeline
# ----------------------------------------------------------------------

def grade_recorded(key, report, source, verification, test_loader=None,
                   build=preview.build_dependencies):
    record = {"fixture": key, "label": LABEL, "provenance": PROVENANCE,
              "record_verification": verification, "llm_calls": 0,
              "llm_seconds": None, "captured_writes": []}
    sandbox = RecordedEvidenceSandbox(report)
    deps = build(None, source, record, test_loader=test_loader,
                 sandbox=sandbox, evidence_overrides={},
                 file_name=teh.FIXTURES[key],
                 student_id="TRUSTED_FIXTURE_NOT_A_STUDENT")
    claim = {"job_id": 910001, "submission_id": -1,   # no row exists
             "claim_token": str(uuid.uuid4())}

    started = time.perf_counter()
    result = process_claimed_job(claim, "recorded-evidence-harness", deps)
    record["total_seconds"] = round(time.perf_counter() - started, 2)
    record["outcome"] = result.get("outcome")
    record["error_code"] = result.get("error_code")
    record["score"] = preview._plain(result.get("score_breakdown"))
    record["verification_report"] = preview._plain(
        result.get("verification_report"))
    record["replay"] = {"compile_calls": sandbox.compile_calls,
                        "run_calls": sandbox.run_calls,
                        "real_compilation": False, "real_execution": False}

    writes = record["captured_writes"]
    completes = [w for w in writes if w["step"] == "complete"]
    failures = [w for w in writes if w["step"] == "complete_compilation_failure"]
    if completes:
        args, kwargs = completes[0]["args"], completes[0]["kwargs"]
        record["suggested_evaluation_preview"] = {
            "suggested_score": str(args[3]), "tests_passed": args[5],
            "tests_run": args[6],
            "auto_evaluation_status": kwargs["auto_evaluation_status"],
            "criteria": kwargs["criteria"],
            "teacher_feedback": "[" + LABEL + "]\n" + args[4]}
    if failures:
        compilation_result = failures[0]["args"][3]
        # Policy of grading_compilation_failure_completion (not written):
        record["suggested_evaluation_preview"] = {
            "suggested_score": "0.00", "tests_passed": 0, "tests_run": 0,
            "auto_evaluation_status": "COMPILATION_FAILED",
            "criteria": None,
            "diagnostics": compilation_result["diagnostics"]}
    record["captured_writes"] = [{"step": w["step"], "written_to_database": False}
                                 for w in writes]
    return record


def student_facing_view(preview_record, tests):
    """What a student could see, using the production redaction."""
    feedback = preview_record.get("teacher_feedback")
    if feedback is None:
        return None
    hidden = hidden_values([(t["input_data"], t["expected_output"])
                            for t in tests])
    return student_safe_feedback(feedback, hidden)


def leaked_hidden_inputs(text, tests):
    """
    Hidden inputs still visible to a student. The AI section is checked
    for every input. The deterministic section contains only counts and
    marks, so 1-2 digit inputs (1, 2, 4, 97) are checked there only as
    part of a test-input context, and longer inputs anywhere.
    """
    if not text:
        return []
    marker = "=== AI rubric assessment"
    ai_part = text.partition(marker)[2] if marker in text else ""
    leaks = []
    for t in tests:
        value = t["input_data"].strip()
        pattern = r"(?<![\w.])" + re.escape(value) + r"(?!\w|\.\d)"
        if re.search(pattern, ai_part) or (len(value) >= 3 and
                                           re.search(pattern, text)):
            leaks.append(value)
    return leaks


def run(keys):
    preview.install_read_only_guard()          # before ANY database access
    tests, test_alloc = teh.load_assessment_5()
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "label": LABEL, "assessment_id": teh.ASSESSMENT_ID,
           "model": ollama_grading_service.OLLAMA_MODEL,
           "compiled_or_executed": False, "runs": {}}
    for key in keys:
        report, report_sha = read_record(key)
        verification = verify_record(key, report, report_sha, tests,
                                     teh.fixture_sha256(key), test_alloc)
        source = teh.fixture_path(key).read_text(encoding="utf-8")
        graded = grade_recorded(
            key, report, source, verification,
            test_loader=grading_test_case_loader.load_grading_test_cases)
        if graded.get("suggested_evaluation_preview"):
            student_text = student_facing_view(
                graded["suggested_evaluation_preview"], tests)
            graded["student_facing_feedback"] = student_text
            graded["student_facing_hidden_input_leaks"] = \
                leaked_hidden_inputs(student_text, tests)
        out["runs"][key] = graded
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", choices=["all", *REVIEWED_RECORDS],
                        default="all")
    args = parser.parse_args()
    keys = list(REVIEWED_RECORDS) if args.fixture == "all" else [args.fixture]
    report = run(keys)

    for key, r in report["runs"].items():
        v, p = r["record_verification"], r.get("suggested_evaluation_preview") or {}
        print(f"--- {key} [{PROVENANCE}] outcome={r['outcome']} "
              f"error={r['error_code']} llm_calls={r['llm_calls']} "
              f"llm_seconds={r['llm_seconds']} total={r['total_seconds']}s",
              flush=True)
        print("    compile:", v["compile_status"], "->",
              v["compile_classification"], "| tests:",
              v.get("tests_passed"), "passed, failed:",
              v.get("failed_test_numbers"), flush=True)
        print("    score:", json.dumps({k: (r["score"] or {}).get(k) for k in (
            "test_score", "rubric_score", "suggested_score")}),
              "| status:", p.get("auto_evaluation_status"), flush=True)
        for c in (r.get("ai") or {}).get("criteria", []):
            print(f"    [{c['mark']}] {c['criterion_name']} cites "
                  f"{c['cited_failed_tests']}: {c['explanation'][:240]}", flush=True)
        for flag in (r.get("ai") or {}).get("reliability_flags") or []:
            print("    FLAG:", flag, flush=True)
        for w in (r.get("verification_report") or {}).get("warnings", []):
            print("    VERIFY WARNING:", w, flush=True)
        if r.get("ai_error"):
            print("    AI ERROR:", r["ai_error"], flush=True)
        print("    hidden inputs leaked to student view:",
              r.get("student_facing_hidden_input_leaks"), flush=True)

    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"recorded_evidence_grading_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(preview._plain(report), indent=2), encoding="utf-8")
    print("report:", path)


if __name__ == "__main__":
    main()
