"""
E2E driver: "Find Maximum and Minimum Using Loops" through the REAL API on
the FYP_E2E_Test backend (127.0.0.1:5000), Step 1 -> Step 2 -> Step 3.

    python fixtures/run_max_min_e2e.py <step> [--confirm FYP_E2E_Test]

Steps (in order): draft, approve, assessment, rubric, reference, readiness,
publish-guard, preview, generate, retry, verify. `all` runs them in order.
Every step that writes requires --confirm FYP_E2E_Test. Read-only steps:
readiness, publish-guard (expects 409, writes nothing), preview, verify.

The teacher token is read from dev_tools/.tokens/teacher.token (written by
`python fixtures/teacher_login.py dev_tools/.tokens/teacher.token BIIT235`
after the owner logs in) and is never printed.
Expected outputs are never sent by this script; they come from the server.
Nothing is ever published (no section assignment).
"""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:5000"
TOKEN_FILE = ROOT / "dev_tools" / ".tokens" / "teacher.token"
STATE = ROOT / "fixtures" / "max_min_oracle" / "setup_state.json"
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "max_min_reference.cpp"
CONFIRM = "FYP_E2E_Test"

PROBLEM = (
    "Find Maximum and Minimum Using Loops\n"
    "Write a C++ program that reads N integers into an array and prints the maximum "
    "and the minimum value.\n"
    "Requirements:\n"
    "* You must use a for loop.\n"
    "* You must use if/else conditional statements.\n"
    "* You must NOT use sort() or STL sorting algorithms.\n"
    "* You must NOT use recursion.\n"
    "* Use cin and cout.\n"
    "Constraints:\n"
    "1 <= N <= 100\n"
    "-1000 <= A[i] <= 1000")
INPUT_FORMAT = "The first line contains N.\nThe second line contains N integers A[i]."
OUTPUT_FORMAT = ("Print the maximum element on the first line and the minimum element "
                 "on the second line.")
SAMPLE_INPUT = "5\n3 -2 7 0 4\n"
EXPECTED_SCHEMA = {"version": 1, "variables": {"N": {"min": 1, "max": 100}},
                   "items": [{"type": "ints", "vars": ["N"]},
                             {"type": "array", "name": "A", "length": "N",
                              "min": -1000, "max": 1000}],
                   "relations": []}
TEACHER_CASES = ["3\n5 5 5\n", "4\n-1 -7 -3 -2\n"]
SECTION = "SEC000001"

WRITE_STEPS = {"draft", "approve", "assessment", "rubric", "reference", "generate", "retry"}


class Stop(Exception):
    pass


def state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(**values):
    data = {**state(), **values}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def call(method, path, body=None):
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    r = requests.request(method, BASE + path, json=body, timeout=300,
                         headers={"Authorization": f"Bearer {token}"})
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    return r.status_code, data


def need(key):
    value = state().get(key)
    if value is None:
        raise Stop(f"Run the step that creates {key} first")
    return value


def show(label, status, data, keys=()):
    summary = {k: data.get(k) for k in keys if k in data}
    print(f"{label}: HTTP {status} {data.get('code', '')} {data.get('message', '')} {summary}")


def oracle_body(dry_run):
    return {"oracle_id": need("oracle_id"), "additional_inputs": TEACHER_CASES,
            "dry_run": dry_run}


def check_cases(cases):
    from input_schema import check_schema, validate_input
    schema = check_schema(EXPECTED_SCHEMA)
    texts = [c["input_data"] for c in cases]
    problems = []
    if len(cases) != 10 or len(set(texts)) != 10:
        problems.append(f"count/unique: {len(cases)}/{len(set(texts))}")
    for c in cases:
        if not validate_input(schema, c["input_data"])["valid"]:
            problems.append(f"invalid input in test {c.get('test_number')}")
        values = [int(t) for t in c["input_data"].split()[1:]]
        if c["expected_output"] != f"{max(values)}\n{min(values)}\n":
            problems.append(f"output mismatch in test {c.get('test_number')}")
    return problems


def step_draft():
    if state().get("draft_id"):
        print("draft: already created", state()["draft_id"])
        return
    status, data = call("POST", "/api/ai/drafts", {"problem": PROBLEM, "input_format": INPUT_FORMAT,
                                                   "output_format": OUTPUT_FORMAT})
    show("draft", status, data)
    if status != 201:
        raise Stop("draft creation failed")
    draft = data["draft"]
    save_state(draft_id=draft["draft_id"],
               ai_value=[c["text"] for c in draft["constraints"] if c["category"] == "value"],
               ai_code=[c["text"] for c in draft["constraints"] if c["category"] == "code"],
               ai_warnings=[w["code"] for w in draft.get("ai_warnings", [])])
    print("  AI value:", state()["ai_value"])
    print("  AI code :", state()["ai_code"])
    print("  AI warnings:", state()["ai_warnings"])


def step_approve():
    draft_id = need("draft_id")
    status, data = call("POST", "/api/test-inputs/suggestions", {
        "problem": {"problem_statement": PROBLEM, "input_format": INPUT_FORMAT,
                    "output_format": OUTPUT_FORMAT,
                    "value_constraints": "\n".join(state()["ai_value"]),
                    "sample_input": SAMPLE_INPUT}, "count": 1})
    schema = data.get("data", {}).get("input_schema")
    clean = {k: schema[k] for k in ("version", "variables", "items", "relations")} if schema else None
    if clean is not None:
        for item in clean["items"]:
            item.pop("distinct", None) if item.get("distinct") is False else None
            item.pop("order", None) if item.get("order") is None else None
    print(f"schema suggestion: HTTP {status} source={data.get('data', {}).get('schema_source')}")
    if clean != EXPECTED_SCHEMA:
        raise Stop(f"Reviewed schema differs from the expected one: {clean}")
    body = {"value_constraints": state()["ai_value"], "code_constraints": state()["ai_code"],
            "input_schema": clean}
    status, data = call("POST", f"/api/ai/drafts/{draft_id}/approve-reviewed", body)
    show("approve-reviewed", status, data, ("replay",))
    if status != 200:
        raise Stop("approval failed")
    print("  notes:", [n["constraint"] for n in data.get("notes", [])])


def step_assessment():
    if state().get("assessment_id"):
        print("assessment: already created", state()["assessment_id"])
        return
    body = {"course_code": "CSC-101", "assessment_type": "Assignment",
            "title": "Find Maximum and Minimum Using Loops", "problem_statement": PROBLEM,
            "input_format": INPUT_FORMAT, "output_format": OUTPUT_FORMAT, "total_marks": 10,
            "deadline_at": "2026-12-31T23:59:00", "validation_percentage": 50,
            "allow_late": False, "late_penalty_marks": 0,
            "additional_constraints": "Use cin and cout.", "draft_id": need("draft_id")}
    status, data = call("POST", "/api/assessments", body)
    show("assessment", status, data, ("assessment_id",))
    if status == 201 or (status == 409 and data.get("code") == "DRAFT_ALREADY_USED"):
        save_state(assessment_id=data["assessment_id"])
        status2, data2 = call("POST", "/api/assessments", body)          # repeat click
        show("assessment repeat click", status2, data2, ("assessment_id",))
        if status2 != 409 or data2.get("assessment_id") != data["assessment_id"]:
            raise Stop("repeat click did not return the same assessment")
        return
    raise Stop("assessment creation failed")


def step_rubric():
    aid = need("assessment_id")
    status, data = call("PUT", f"/api/assessments/{aid}/rubric", {"criteria": [
        {"criterion_name": "Logic & Correctness", "max_marks": 3,
         "description": "Correct maximum and minimum using a single for loop"},
        {"criterion_name": "Code Quality", "max_marks": 2,
         "description": "Readable code following the stated rules"}]})
    show("rubric", status, data)
    if status != 200:
        raise Stop("rubric failed")


def step_reference():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/reference-solution",
                        {"source_code": REFERENCE.read_text(encoding="utf-8")})
    show("reference submit", status, data, ("reference_solution_id", "source_hash", "approval_status"))
    status, data = call("GET", f"/api/assessments/{aid}/reference-solution")
    ref = data.get("reference_solution") or data.get("data") or data
    source_hash = ref.get("source_hash")
    print(f"reference review: HTTP {status} hash={source_hash} status={ref.get('approval_status')}")
    if ref.get("approval_status") != "APPROVED":
        status, data = call("POST", f"/api/assessments/{aid}/reference-solution/approve",
                            {"source_hash": source_hash})
        show("reference approve", status, data, ("approval_status",))
        if status != 200:
            raise Stop("reference approval failed")


def step_readiness():
    aid = need("assessment_id")
    status, data = call("GET", f"/api/assessments/{aid}/generation-readiness")
    d = data.get("data", {})
    print(f"readiness: HTTP {status} generate={d.get('ready_to_generate')} "
          f"missing={[m['code'] for m in d.get('missing_for_generation', [])]} "
          f"publish={d.get('ready_to_publish')} "
          f"{[m['code'] for m in d.get('missing_for_publish', [])]}")
    source = (d.get("checks") or {}).get("expected_output_source") or {}
    print("  output source:", source)
    if source.get("oracle_id"):
        save_state(oracle_id=source["oracle_id"])
    return d


def step_publish_guard():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/sections", {"section_ids": [SECTION]})
    show("publish (expected refusal)", status, data)
    print("  missing:", [m["code"] for m in data.get("missing", [])])
    if status == 201:
        raise Stop("PUBLISHED unexpectedly")


def step_preview():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/generate-test-cases/oracle",
                        oracle_body(True))
    d = data.get("data", {})
    show("oracle preview", status, data)
    cases = d.get("test_cases", [])
    print(f"  generated={d.get('generated_count')} saved={d.get('saved_count')} "
          f"mode={d.get('execution_mode')} format={d.get('input_format')}")
    print("  problems:", check_cases(cases) or "none")
    save_state(preview=[{"input_data": c["input_data"], "expected_output": c["expected_output"]}
                        for c in cases])


def step_generate():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/generate-test-cases/oracle",
                        oracle_body(False))
    show("oracle generate", status, data)
    print("  ", {k: data.get("data", {}).get(k) for k in
                 ("generated_count", "saved_count", "marks_per_test", "test_marks_allocated")})
    if status != 201:
        raise Stop("generation failed")


def step_retry():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/generate-test-cases/oracle",
                        oracle_body(False))
    show("oracle generate retry", status, data)
    if status != 409:
        raise Stop("retry was not refused")


def step_verify():
    aid = need("assessment_id")
    status, data = call("GET", f"/api/assessments/{aid}/test-cases")
    cases = data.get("test_cases", [])
    print(f"test-cases: HTTP {status} total={data.get('total')} "
          f"visibility={sorted({c['visibility'] for c in cases})} "
          f"marks={sorted({c['marks'] for c in cases})}")
    print("  problems:", check_cases(cases) or "none")
    preview = state().get("preview")
    if preview:
        same = [{"input_data": c["input_data"], "expected_output": c["expected_output"]}
                for c in cases] == preview
        print("  identical to preview:", same)
    step_readiness()


STEPS = {"draft": step_draft, "approve": step_approve, "assessment": step_assessment,
         "rubric": step_rubric, "reference": step_reference, "readiness": step_readiness,
         "publish-guard": step_publish_guard, "preview": step_preview,
         "generate": step_generate, "retry": step_retry, "verify": step_verify}


def main():
    args = sys.argv[1:]
    if not args or args[0] not in [*STEPS, "all"]:
        raise SystemExit(__doc__)
    names = list(STEPS) if args[0] == "all" else [args[0]]
    confirmed = len(args) == 3 and args[1] == "--confirm" and args[2] == CONFIRM
    if any(n in WRITE_STEPS for n in names) and not confirmed:
        raise SystemExit(f"Write step(s) need --confirm {CONFIRM}")
    if not TOKEN_FILE.exists():
        raise SystemExit("No teacher token: run fixtures/teacher_login.py first")
    sys.path.insert(0, str(ROOT))
    try:
        for name in names:
            STEPS[name]()
    except Stop as stop:
        raise SystemExit(f"STOPPED: {stop}")


if __name__ == "__main__":
    main()
