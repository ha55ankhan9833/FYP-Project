"""
DEVELOPMENT-ONLY grading preview harness (SYNTHETIC EVIDENCE).

    .venv\\Scripts\\python.exe dev_tools\\grading_preview_harness.py
    .venv\\Scripts\\python.exe dev_tools\\grading_preview_harness.py --scenario most_pass
    .venv\\Scripts\\python.exe dev_tools\\grading_preview_harness.py --live-negative

What is REAL
- Assessment 5 configuration, problem text and teacher rubric, loaded
  read-only from SQL Server by the production loader services. Test cases
  come from the production loader (which enforces exactly 10) or, with
  --test-source proposed-migration, from the 10 reviewed database rows in
  fixtures/new_assessment_csc101/migration_to_10_tests.json (in memory).
- grading_pipeline.process_claimed_job (evidence validation, equal-weight
  scoring, rubric validation, score combination, feedback composition).
- Rubric grading by the local Ollama model through rubric_ai_grader.

What is SYNTHETIC (and labelled so everywhere)
- The student submissions (dev_tools/synthetic_submissions/*.cpp) are
  fixtures. They are NEVER compiled or executed.
- Their per-test stdout comes from a hand-written Python model of each
  fixture's logic, not from running the program.
- No Submission / GradingJob / Evaluation rows exist or are written:
  the pipeline's write steps are replaced by in-memory capture, and every
  SQL connection is wrapped so any non-SELECT statement or commit raises.

This is NOT an end-to-end student execution test.
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import grading_configuration_service  # noqa: E402
import grading_test_case_loader  # noqa: E402
import ollama_grading_service  # noqa: E402
from assessment_rubric_service import load_assessment_rubric  # noqa: E402
from grading_pipeline import GradingDependencies, process_claimed_job  # noqa: E402
from rubric_ai_grader import grade_rubric_with_ollama  # noqa: E402

ASSESSMENT_ID = 5
SUBMISSIONS = Path(__file__).resolve().parent / "synthetic_submissions"
REPORTS = Path(__file__).resolve().parent / "reports"
SYNTHETIC_LABEL = "SYNTHETIC DEVELOPMENT EVIDENCE - not a real student execution"


# ----------------------------------------------------------------------
# Read-only database guard
# ----------------------------------------------------------------------

class ReadOnlyViolation(RuntimeError):
    pass


_ALLOWED_FIRST_WORDS = {"SELECT", "WITH"}


def _first_word(sql):
    text = sql.strip().lstrip(";").lstrip()
    return text.split(None, 1)[0].upper() if text else ""


class ReadOnlyCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, *params):
        if _first_word(sql) not in _ALLOWED_FIRST_WORDS:
            raise ReadOnlyViolation(f"Blocked non-SELECT statement: "
                                    f"{_first_word(sql)}")
        return self._cursor.execute(sql, *params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ReadOnlyConnection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return ReadOnlyCursor(self._conn.cursor())

    def commit(self):
        raise ReadOnlyViolation("commit() blocked by read-only harness")

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __setattr__(self, name, value):
        if name == "_conn":
            object.__setattr__(self, name, value)
        elif name == "autocommit":
            raise ReadOnlyViolation("autocommit change blocked")
        else:
            setattr(self._conn, name, value)


def read_only_connection(factory=None):
    return ReadOnlyConnection((factory or db.get_connection)())


_ORIGINAL_GET_CONNECTION = db.get_connection


def install_read_only_guard():
    """Route the loader modules' get_connection through the guard."""
    original = _ORIGINAL_GET_CONNECTION
    guarded = lambda: read_only_connection(original)  # noqa: E731
    grading_configuration_service.get_connection = guarded
    grading_test_case_loader.get_connection = guarded
    return guarded


# ----------------------------------------------------------------------
# Python behaviour models of the synthetic fixtures (no execution)
# ----------------------------------------------------------------------

def _trial_division(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d += 1
    return True


def model_all_pass(n):
    return ("YES" if _trial_division(n) else "NO") + "\n"


def model_square_bug(n):
    prime = n >= 2
    d = 2
    while d * d < n:                     # the fixture's off-by-one
        if n % d == 0:
            prime = False
            break
        d += 1
    return ("YES" if prime else "NO") + "\n"


_SIEVE_CACHE = {}


def model_sieve_bound_bug(n):
    if "s" not in _SIEVE_CACHE:
        maxn = 1_000_000
        is_prime = [True] * (maxn + 1)
        is_prime[0] = is_prime[1] = False
        i = 2
        while i * i <= maxn:
            if is_prime[i]:
                j = i * i
                while j <= 1000:         # the fixture's wrong inner bound
                    is_prime[j] = False
                    j += i
            i += 1
        _SIEVE_CACHE["s"] = is_prime
    return ("YES" if _SIEVE_CACHE["s"][n] else "NO") + "\n"


def model_output_case(n):
    return ("Yes" if _trial_division(n) else "No") + "\n"


# Pass counts on the 10-case Assessment 5 set (fixed 10-test rule).
SCENARIOS = {
    "all_pass": ("all_pass_trial_division.cpp", model_all_pass, 10),
    "most_pass": ("most_pass_square_bug.cpp", model_square_bug, 8),
    "half_range": ("half_sieve_bound_bug.cpp", model_sieve_bound_bug, 6),
    "none_pass": ("none_pass_output_case.cpp", model_output_case, 0),
}

MIGRATION_PLAN = (ROOT / "fixtures" / "new_assessment_csc101" /
                  "migration_to_10_tests.json")
PROPOSED_LABEL = ("PROPOSED MIGRATION PREVIEW - these 10 rows are read from the "
                  "database and renumbered IN MEMORY ONLY; the database still "
                  "holds 15 test cases until the migration is approved")


def load_proposed_migration_tests(assessment_id=ASSESSMENT_ID):
    """The 10 reviewed rows from migration_to_10_tests.json, read-only."""
    if assessment_id != ASSESSMENT_ID:
        raise ValueError("Proposed migration exists only for assessment 5")
    plan = json.loads(MIGRATION_PLAN.read_text(encoding="utf-8"))
    conn = read_only_connection(_ORIGINAL_GET_CONNECTION)
    try:
        cur = conn.cursor()
        cur.execute("SELECT test_case_id, test_name, category, input_data, "
                    "expected_output FROM dbo.TestCase WHERE assessment_id = ?",
                    (ASSESSMENT_ID,))
        rows = {int(r[0]): r for r in cur.fetchall()}
    finally:
        conn.rollback()
        conn.close()

    tests = []
    for item in plan["proposed_10"]:
        row = rows.get(item["current_test_case_id"])
        if row is None or row[3] != item["input_data"] \
                or row[4] != item["expected_output"]:
            raise RuntimeError("Database rows differ from the migration plan")
        tests.append({"test_number": item["test_number"],
                      "test_name": str(row[1]), "category": item["category"],
                      "input_data": str(row[3]), "expected_output": str(row[4])})
    return tests


def strip_developer_header(labelled_source):
    """
    Fixture files begin with '//' comment lines that label them SYNTHETIC
    and describe the intended bug. Those lines are for developers only:
    showing them to the model would hand it the diagnosis. Returns
    (code_without_header, number_of_header_lines_removed).
    """
    lines = labelled_source.splitlines(keepends=True)
    removed = 0
    while removed < len(lines) and lines[removed].lstrip().startswith("//"):
        removed += 1
    if removed == 0 or "SYNTHETIC" not in lines[0]:
        raise RuntimeError("Fixture is missing its SYNTHETIC developer header")
    return "".join(lines[removed:]), removed


class SyntheticEvidenceSandbox:
    """
    Stand-in for the sandbox inside this harness ONLY. It is never
    registered with secure_execution_gate, so the production worker keeps
    refusing to claim jobs.
    """

    synthetic = True

    def __init__(self, model):
        self.model = model

    def compile(self, source_code):
        return {"status": "COMPILED", "artifact": "synthetic-not-compiled",
                "diagnostics": "SYNTHETIC: source was not compiled"}

    def run_tests(self, artifact, cases):
        return [{"test_number": c["test_number"], "status": "completed",
                 "stdout": self.model(int(c["input_data"].strip()))}
                for c in cases]

    def cleanup(self, artifact):
        return None


# ----------------------------------------------------------------------
# Harness run
# ----------------------------------------------------------------------

def build_dependencies(model, source, record, config_override=None,
                       test_loader=None, hypothetical_code_constraints=None,
                       sandbox=None, evidence_overrides=None,
                       file_name="synthetic.cpp", student_id="SYNTHETIC"):
    """
    sandbox / evidence_overrides let other development harnesses (e.g. the
    recorded trusted-fixture replay) reuse this read-only wiring. Defaults
    keep the synthetic behaviour.
    """
    guarded = install_read_only_guard()
    if sandbox is None:
        sandbox = SyntheticEvidenceSandbox(model)
    if evidence_overrides is None:
        evidence_overrides = {"synthetic": True}
    config_loader = grading_configuration_service.load_grading_configuration
    context_loader = grading_configuration_service.load_assessment_problem_context

    def load_context(assessment_id):
        context = context_loader(assessment_id)
        if hypothetical_code_constraints:
            # HYPOTHETICAL, in memory only: Assessment 5 has no approved
            # code constraints; this exercises the static-check path.
            record["hypothetical_code_constraints"] = hypothetical_code_constraints
            extra = "Required code constraints: " + ", ".join(
                hypothetical_code_constraints)
            context = {**context,
                       "code_constraints": list(hypothetical_code_constraints),
                       "constraints": context["constraints"] + "\n" + extra}
        return context

    def load_config(assessment_id):
        config = config_loader(assessment_id)
        if config_override:
            config = {**config, **config_override}
        return config

    def grade_rubric(context, rubric, allocation, source_code, evidence):
        record["llm_calls"] += 1
        record["failed_tests_supplied"] = [
            {"test_number": c["test_number"], "input": c.get("input_data"),
             "expected": (c.get("expected_output") or "").strip(),
             "student_output": (c.get("student_output") or "").strip()}
            for c in evidence.get("cases", []) if c.get("passed") is False]
        record["code_constraint_checks"] = (
            evidence.get("code_constraint_checks") or {}).get("checks", [])
        started = time.perf_counter()
        try:
            result = grade_rubric_with_ollama(
                context, rubric, allocation, source_code,
                {**evidence, **evidence_overrides})
            record["ai"] = {
                "contract": result.get("contract"),
                "rubric_total_source": result.get("rubric_total_source"),
                "ignored_model_fields": result.get("ignored_model_fields"),
                "criteria": [{"criterion_name": c["criterion_name"],
                              "mark": c["rating"],
                              "cited_failed_tests": c.get("cited_failed_tests"),
                              "explanation": c["explanation"]}
                             for c in result.get("criteria", [])],
                "observed_failures": result.get("observed_failures"),
                "suspected_bugs": result.get("suspected_bugs"),
                "issues": result.get("issues"),
                "reliability_flags": result.get("reliability_flags"),
            }
            return result
        except Exception as error:
            record["ai_error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["llm_seconds"] = round(time.perf_counter() - started, 2)

    def capture(name):
        def inner(*args, **kwargs):
            record["captured_writes"].append({"step": name, "args": args,
                                              "kwargs": kwargs})
            if name == "release":
                return {"job_status": "PENDING (simulated, not written)"}
            return {"job_status": "COMPLETED (simulated, not written)"}
        return inner

    return GradingDependencies(
        claim=lambda *a: None,
        mark_exhausted=lambda: [],
        prepare=lambda job_id, worker, token: {
            "job_id": job_id, "submission_id": -1, "source_code": source,
            "file_name": file_name, "student_id": student_id,
            "assessment_section_id": -1},
        renew_lease=lambda *a: "synthetic-lease",
        load_assessment_marks=lambda sid: {"assessment_id": ASSESSMENT_ID},
        load_config=load_config,
        load_context=load_context,
        load_rubric=lambda aid: load_assessment_rubric(
            aid, connection_factory=guarded),
        load_test_cases=(test_loader or
                         grading_test_case_loader.load_grading_test_cases),
        get_sandbox=lambda: sandbox,
        grade_rubric=grade_rubric,
        complete=capture("complete"),
        complete_compilation_failure=capture("complete_compilation_failure"),
        release=capture("release"),
        allow_synthetic_evidence=True,     # dev harness ONLY
    )


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def run_scenario(name, config_override=None, test_source="database",
                 hypothetical_code_constraints=None):
    install_read_only_guard()            # before ANY database access
    fixture, model, expected_passes = SCENARIOS[name]
    labelled_source = (SUBMISSIONS / fixture).read_text(encoding="utf-8")
    source, header_lines = strip_developer_header(labelled_source)

    if test_source == "proposed-migration":
        test_loader = load_proposed_migration_tests
    else:
        test_loader = grading_test_case_loader.load_grading_test_cases

    record = {"scenario": name, "label": SYNTHETIC_LABEL, "fixture": fixture,
              "developer_header_lines_removed_before_grading": header_lines,
              "test_source": test_source, "llm_calls": 0, "llm_seconds": None,
              "captured_writes": []}
    if test_source == "proposed-migration":
        record["test_source_label"] = PROPOSED_LABEL

    # Honesty guard: the modelled evidence must give the declared count.
    # If the stored test set itself is refused (e.g. not exactly 10), the
    # pipeline below reports that as a configuration failure instead.
    try:
        tests = test_loader(ASSESSMENT_ID)
    except ValueError as error:
        record["test_load_refused"] = str(error)
    else:
        modelled = sum(model(int(t["input_data"])) == t["expected_output"]
                       for t in tests)
        if modelled != expected_passes:
            raise RuntimeError(f"{name}: modelled passes {modelled} != "
                               f"declared {expected_passes}")

    deps = build_dependencies(model, source, record, config_override,
                              test_loader, hypothetical_code_constraints)
    claim = {"job_id": 900001, "submission_id": -1,     # synthetic, no row
             "claim_token": str(uuid.uuid4())}

    started = time.perf_counter()
    result = process_claimed_job(claim, "dev-harness", deps)
    record["total_seconds"] = round(time.perf_counter() - started, 2)
    record["outcome"] = result.get("outcome")
    record["error_code"] = result.get("error_code")
    record["score"] = _plain(result.get("score_breakdown"))

    completes = [w for w in record["captured_writes"] if w["step"] == "complete"]
    if completes:
        args, kwargs = completes[0]["args"], completes[0]["kwargs"]
        record["suggested_evaluation_preview"] = {
            "suggested_score": str(args[3]),
            "tests_passed": args[5], "tests_run": args[6],
            "auto_evaluation_status": kwargs["auto_evaluation_status"],
            "criteria": kwargs["criteria"],
            "feedback": "[" + SYNTHETIC_LABEL + "]\n" + args[4],
        }
    record["captured_writes"] = [
        {"step": w["step"], "written_to_database": False,
         "detail": _plain(w["args"][3:5]) if w["step"] == "release" else None}
        for w in record["captured_writes"]]
    return record


def live_negative_checks(test_source="database"):
    """Real failure paths against the real local services."""
    results = {}
    original_url = ollama_grading_service.OLLAMA_URL
    original_timeout = ollama_grading_service.OLLAMA_TIMEOUT
    try:
        ollama_grading_service.OLLAMA_URL = "http://127.0.0.1:9/api/chat"
        results["ollama_unreachable"] = run_scenario(
            "most_pass", test_source=test_source)
    finally:
        ollama_grading_service.OLLAMA_URL = original_url
    try:
        ollama_grading_service.OLLAMA_TIMEOUT = (10, 0.5)
        results["ollama_read_timeout_0_5s"] = run_scenario(
            "most_pass", test_source=test_source)
    finally:
        ollama_grading_service.OLLAMA_TIMEOUT = original_timeout
    results["validation_100_override"] = run_scenario(
        "most_pass", config_override={"validation_percentage": Decimal("100")},
        test_source=test_source)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--live-negative", action="store_true")
    parser.add_argument("--test-source", default="database",
                        choices=["database", "proposed-migration"],
                        help="'database' uses the stored test set (refused "
                             "unless exactly 10); 'proposed-migration' uses "
                             "the reviewed 10-case plan, in memory only")
    parser.add_argument("--hypothetical-code-constraint", action="append",
                        default=[], help="HYPOTHETICAL code constraint "
                        "applied in memory only (repeatable)")
    args = parser.parse_args()

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "label": SYNTHETIC_LABEL, "assessment_id": ASSESSMENT_ID,
              "test_source": args.test_source,
              "model": ollama_grading_service.OLLAMA_MODEL, "runs": {}}
    if args.test_source == "proposed-migration":
        report["test_source_label"] = PROPOSED_LABEL
        print(PROPOSED_LABEL, flush=True)

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        print(f"--- {name} ({SYNTHETIC_LABEL})", flush=True)
        report["runs"][name] = run_scenario(
            name, test_source=args.test_source,
            hypothetical_code_constraints=args.hypothetical_code_constraint)
        r = report["runs"][name]
        preview = r.get("suggested_evaluation_preview") or {}
        print(json.dumps({k: r[k] for k in ("outcome", "error_code",
                                             "llm_calls", "llm_seconds",
                                             "total_seconds")}),
              "| score:", json.dumps({k: (r["score"] or {}).get(k) for k in (
                  "test_score", "rubric_score", "suggested_score")}),
              "| criteria:", json.dumps([(c["criterion_name"], c["rating"])
                                         for c in preview.get("criteria") or []]),
              "| status:", preview.get("auto_evaluation_status"), flush=True)
        ai = r.get("ai") or {}
        print("    failed tests supplied:",
              [(f["test_number"], f["input"]) for f in r.get("failed_tests_supplied", [])],
              flush=True)
        for c in ai.get("criteria", []):
            print(f"    [{c['mark']}] {c['criterion_name']} cites "
                  f"{c['cited_failed_tests']}: {c['explanation'][:200]}", flush=True)
        print("    observed:", ai.get("observed_failures"), flush=True)
        print("    suspected:", ai.get("suspected_bugs"), flush=True)
        for check in r.get("code_constraint_checks", []):
            print(f"    constraint {check['constraint']}: {check['status']} "
                  f"({check['evidence']})", flush=True)
        for flag in ai.get("reliability_flags") or []:
            print("    FLAG:", flag, flush=True)
        if r.get("ai_error"):
            print("    AI ERROR:", r["ai_error"], flush=True)

    if args.live_negative:
        report["live_negative"] = live_negative_checks(args.test_source)
        for key, r in report["live_negative"].items():
            print(f"--- negative {key}:", r["outcome"], r["error_code"],
                  "llm_calls", r["llm_calls"], "seconds", r["llm_seconds"],
                  "captured", [w["step"] for w in r["captured_writes"]],
                  "score", (r["score"] or {}).get("suggested_score"), flush=True)

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"grading_preview_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(_plain(report), indent=2), encoding="utf-8")
    print("report:", out)


if __name__ == "__main__":
    main()
