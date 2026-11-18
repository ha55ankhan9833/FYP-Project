"""
DEVELOPMENT-ONLY driver for the structured-input E2E fixture (array sum),
run ONLY through the normal authenticated APIs of a backend started with
dev_tools/run_e2e_backend.py (database FYP_E2E_Test).

    python fixtures/run_structured_array_sum_e2e.py check
    python fixtures/run_structured_array_sum_e2e.py <step> --confirm FYP_E2E_Test
        step: draft | assessment | reference | generate | verify

  read-only  check, verify
  WRITES     draft       ConstraintReviewDraft (AI draft, rejections, the
                         teacher value rule, input schema, approvals, finalize)
             assessment  Assessment (linked to the finalized draft)
             reference   ReferenceSolution (PENDING -> APPROVED; nothing runs)
             generate    TestCase x10 ONLY if the backend can verify expected
                         outputs (sandbox, or owner-allow-listed hash);
                         otherwise HTTP 409 EXPECTED_OUTPUT_UNVERIFIABLE and
                         nothing is saved

- Teacher JWT from dev_tools/.tokens/teacher.token (fixtures/teacher_login.py,
  typed by the owner); never printed.
- Refuses unless this process AND the running backend use FYP_E2E_Test, the
  token belongs to BIIT235, and the production student gate is disabled.
- The reference payload must hash to the reviewed EXPECTED_HASH.
- Idempotent: created IDs are kept in structured_array_sum/setup_state.json.
- This script never compiles or executes C++ itself, and never edits the
  trusted reference allowlist.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:5000"
TEACHER = "BIIT235"
REQUIRED_DATABASE = "FYP_E2E_Test"
DATA = ROOT / "fixtures" / "structured_array_sum"
STATE_FILE = DATA / "setup_state.json"
TOKEN_FILE = ROOT / "dev_tools" / ".tokens" / "teacher.token"
EXPECTED_HASH = "4ea30854b29ac9ceafe7392c71a8e5ca039d98fe91ce86137654a2567c0d8e40"
PROTECTED_ASSESSMENTS = {2, 4, 5}
WRITE_STEPS = {"draft", "assessment", "reference", "generate"}


class Stop(Exception):
    pass


def body(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))["body"]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def require_confirm(step, confirm):
    if step in WRITE_STEPS and confirm != REQUIRED_DATABASE:
        raise Stop(f"step {step!r} writes to {REQUIRED_DATABASE}; "
                   f"--confirm {REQUIRED_DATABASE} is required")


def reviewed_source():
    source = body("04_reference_solution.json")["source_code"]
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_HASH:
        raise Stop("Reference payload differs from the reviewed source")
    return source


class Api:
    def __init__(self, token, session=None):
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def call(self, method, path, payload=None, timeout=60, ok=(200, 201)):
        r = self.session.request(method, BASE + path, json=payload, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}
        if r.status_code not in ok:
            raise Stop(f"{method} {path} -> HTTP {r.status_code}: "
                       f"{data.get('message') or data.get('error') or data}")
        return r.status_code, data


def require_environment(api, database_name):
    """This process and the running backend must both use FYP_E2E_Test."""
    if database_name() != REQUIRED_DATABASE:
        raise Stop(f"this driver must use {REQUIRED_DATABASE}")
    _, status = api.call("GET", "/api/grading/status")
    if status.get("active_database") != REQUIRED_DATABASE:
        raise Stop(f"backend is not running against {REQUIRED_DATABASE} "
                   f"(reports {status.get('active_database')!r})")
    if status.get("student_execution_enabled"):
        raise Stop("production student execution is enabled")
    _, me = api.call("GET", "/api/auth/me")
    if me.get("role") != "teacher" or me.get("user_id") != TEACHER:
        raise Stop(f"token is not a {TEACHER} teacher token")


def sql(query, *params):
    """Read-only SQL against the selected (E2E) database."""
    from db import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, *params)
        return cur.fetchall()
    finally:
        conn.rollback()
        conn.close()


def owned_assessment(state):
    aid = state.get("assessment_id")
    if not aid:
        raise Stop("No assessment_id recorded; run the assessment step first")
    if aid in PROTECTED_ASSESSMENTS:
        raise Stop("Refusing to touch a protected assessment")
    rows = sql("SELECT teacher_id, title FROM dbo.Assessment WHERE assessment_id = ?", aid)
    if not rows or rows[0][0] != TEACHER or rows[0][1] != body(
            "03_assessment_create.json")["title"]:
        raise Stop(f"Assessment {aid} is not this fixture's assessment")
    return aid


# ------------------------------------------------------------------ steps

def step_check(api, state):
    reviewed_source()
    print("environment OK:", REQUIRED_DATABASE, "| teacher", TEACHER)
    print("reference hash OK:", EXPECTED_HASH)
    print("state:", state or "{}")


def final_draft_ok(draft, schema, rule):
    """The backend stores the CHECKED (normalized) schema, so compare normalized forms."""
    from input_schema import SchemaError, check_schema
    accepted = [c for c in draft["constraints"] if c["review_status"] == "ACCEPTED"]
    try:
        stored = check_schema(draft.get("input_schema") or {})
    except SchemaError:
        return False
    wanted = check_schema(schema)
    return (draft.get("finalized") is True
            and draft["approval"] == {"value": True, "code": True}
            and [(c["category"], c["text"]) for c in accepted] == [("value", rule)]
            and stored == wanted and not stored["legacy"])


def step_draft(api, state):
    schema = body("02_input_schema.json")["input_schema"]
    rule = json.loads((DATA / "02b_value_rule.json").read_text(encoding="utf-8"))["value_rule"]

    if state.get("draft_id"):
        _, data = api.call("GET", f"/api/ai/drafts/{state['draft_id']}")
        draft = data["draft"]
        if draft.get("finalized"):
            if not final_draft_ok(draft, schema, rule):
                raise Stop("Recorded draft is finalized but not as intended")
            print("draft step already complete:", state["draft_id"])
            return
    else:
        print("creating draft (calls the local constraint AI; may take a while)...")
        _, data = api.call("POST", "/api/ai/drafts", body("01_draft_create.json"),
                           timeout=300)
        draft = data["draft"]
        state["draft_id"] = draft["draft_id"]
        save_state(state)
        print("draft created:", draft["draft_id"])

    draft_id = state["draft_id"]
    for item in draft["constraints"]:
        if item["review_status"] == "PENDING":
            _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/actions", {
                "action": "DELETE", "category": item["category"],
                "constraint_id": item["id"]})
            draft = data["draft"]
            print(f"rejected AI {item['category']} suggestion: {item['text']!r}")

    accepted = [c["text"] for c in draft["constraints"] if c["review_status"] == "ACCEPTED"]
    if not accepted:
        _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/actions",
                           {"action": "ADD", "category": "value", "text": rule})
        draft = data["draft"]
        print("added teacher value rule:", rule)
    elif accepted != [rule]:
        raise Stop(f"Unexpected accepted constraints: {accepted}")

    if not draft["approval"]["value"]:
        _, data = api.call("PUT", f"/api/ai/drafts/{draft_id}/input-schema",
                           {"input_schema": schema})
        draft = data["draft"]
        print("input schema attached")

    for category in ("value", "code"):
        if not draft["approval"][category]:
            _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/approve",
                               {"category": category})
            draft = data["draft"]
            print("approved category:", category)

    api.call("POST", f"/api/ai/drafts/{draft_id}/finalize", {})
    _, data = api.call("GET", f"/api/ai/drafts/{draft_id}")
    if not final_draft_ok(data["draft"], schema, rule):
        raise Stop("Finalized draft does not match the reviewed rule and schema")
    print("draft step done:", draft_id)


def step_assessment(api, state):
    if state.get("assessment_id"):
        owned_assessment(state)
        print("assessment step already complete:", state["assessment_id"])
        return
    draft_id = state.get("draft_id")
    if not draft_id:
        raise Stop("Run the draft step first")
    payload = dict(body("03_assessment_create.json"))
    if sql("SELECT assessment_id FROM dbo.Assessment WHERE teacher_id = ? AND title = ?",
           TEACHER, payload["title"]):
        raise Stop("An assessment with this title already exists")
    payload["draft_id"] = draft_id
    _, data = api.call("POST", "/api/assessments", payload)
    state["assessment_id"] = int(data["assessment_id"])
    save_state(state)
    owned_assessment(state)
    print("assessment step done:", state["assessment_id"])


def step_reference(api, state):
    aid = owned_assessment(state)
    source = reviewed_source()
    status, data = api.call("GET", f"/api/assessments/{aid}/reference-solution",
                            ok=(200, 404))
    if status == 404:
        api.call("POST", f"/api/assessments/{aid}/reference-solution",
                 {"source_code": source})
        _, data = api.call("GET", f"/api/assessments/{aid}/reference-solution")
    ref = data["reference_solution"]
    if ref["source_hash"] != EXPECTED_HASH or ref["source_code"] != source:
        raise Stop("Stored reference differs from the reviewed source")
    if ref["approval_status"] == "PENDING":
        api.call("POST", f"/api/assessments/{aid}/reference-solution/approve",
                 {"source_hash": EXPECTED_HASH})
        _, data = api.call("GET", f"/api/assessments/{aid}/reference-solution")
        ref = data["reference_solution"]
    if ref["approval_status"] != "APPROVED":
        raise Stop(f"Reference status is {ref['approval_status']}")
    state["reference_solution_id"] = ref["reference_solution_id"]
    save_state(state)
    print("reference step done:", ref["reference_solution_id"], ref["source_hash"])


def step_generate(api, state):
    aid = owned_assessment(state)
    status, data = api.call("POST", f"/api/assessments/{aid}/generate-test-cases",
                            body("06_generate_test_cases.json"), timeout=300,
                            ok=(200, 201, 409))
    if status == 409:
        if data.get("code") != "EXPECTED_OUTPUT_UNVERIFIABLE":
            raise Stop(f"Unexpected 409: {data}")
        planned = [c["input_data"] for c in json.loads(
            (DATA / "planned_test_cases.json").read_text(encoding="utf-8"))["cases"]]
        returned = [c.get("input_data") for c in data.get("validated_inputs", [])]
        print("HTTP 409 EXPECTED_OUTPUT_UNVERIFIABLE (nothing saved):",
              data.get("reason"))
        print("validated inputs match the planned inputs:", returned == planned)
        return
    result = data.get("data") or {}          # success body: {"status", "message", "data"}
    print("generated:", result.get("generated_count"), "saved:", result.get("saved_count"),
          "mode:", result.get("execution_mode"), "| hash:", result.get("source_hash"))
    step_verify(api, state)


def compare_with_oracle(saved, planned):
    """Saved rows [(number, input, expected)] vs planned oracle cases."""
    problems = []
    if len(saved) != len(planned):
        problems.append(f"saved {len(saved)} test cases, planned {len(planned)}")
    for (number, input_data, expected), case in zip(saved, planned):
        if input_data != case["input_data"]:
            problems.append(f"test {number}: input differs from the planned exact text")
        if expected.replace("\r\n", "\n") != case["expected_output_python_oracle"]:
            problems.append(f"test {number}: reference output differs from the Python oracle")
    return problems


def step_verify(api, state):
    aid = owned_assessment(state)
    planned = json.loads((DATA / "planned_test_cases.json").read_text(
        encoding="utf-8"))["cases"]
    saved = [tuple(r) for r in sql(
        "SELECT test_number, input_data, expected_output FROM dbo.TestCase "
        "WHERE assessment_id = ? ORDER BY test_number", aid)]
    if not saved:
        print("no test cases saved yet")
        return
    problems = compare_with_oracle(saved, planned)
    print("verify:", "OK - all 10 match the oracle" if not problems else problems)


STEPS = {"check": step_check, "draft": step_draft, "assessment": step_assessment,
         "reference": step_reference, "generate": step_generate, "verify": step_verify}


def main():
    parser = argparse.ArgumentParser(description="Structured array-sum E2E steps")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        require_confirm(args.step, args.confirm)
        import db
        db.select_database(REQUIRED_DATABASE)
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            raise Stop("Missing teacher token: run fixtures/teacher_login.py first")
        api = Api(token)
        require_environment(api, db.database_name)
        STEPS[args.step](api, load_state())
    except Stop as stop:
        sys.exit(f"STOPPED: {stop}")


if __name__ == "__main__":
    main()
