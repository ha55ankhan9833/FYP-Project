"""
Owner-approved setup Steps 2-5 for the CSC-101 Prime Number Check
assessment, executed ONLY through the normal authenticated APIs.

    python fixtures/run_csc101_setup.py <token_file> <step>
        step: check | 2 | 3 | 4 | 5

- Uses a teacher JWT obtained by fixtures/teacher_login.py; never prints it.
- Before every write: confirms the token belongs to BIIT235 and verifies
  the target records (read-only SQL) are the expected ones.
- Idempotent: created IDs are stored in setup_state.json; re-running a
  step verifies/resumes instead of creating duplicates.
- Steps 6-8 (allowlist, test generation, section assignment) are NOT
  implemented here on purpose.
- Never compiles or executes C++.
"""

import hashlib
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import get_connection  # noqa: E402  (read-only checks only)

BASE = "http://127.0.0.1:5000"
TEACHER = "BIIT235"
RULE = "1 <= N <= 1000000"
DATA = ROOT / "fixtures" / "new_assessment_csc101"
STATE_FILE = DATA / "setup_state.json"
EXPECTED_HASH = "529e00e0a8e4000826d288f293e33e0e4753ecc49426156b9c936bb730aac9ab"
PROTECTED_ASSESSMENTS = {2, 4}


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


class Api:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def call(self, method, path, payload=None, timeout=60, ok=(200, 201)):
        r = self.session.request(method, BASE + path, json=payload,
                                 timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}
        if r.status_code not in ok:
            raise Stop(f"{method} {path} -> HTTP {r.status_code}: "
                       f"{data.get('message') or data.get('error') or data}")
        return r.status_code, data


def sql_one(query, *params):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, *params)
        return cur.fetchall()
    finally:
        conn.rollback()
        conn.close()


def verify_identity(api):
    _, me = api.call("GET", "/api/auth/me")
    if me.get("role") != "teacher" or me.get("user_id") != TEACHER:
        raise Stop("Token is not a BIIT235 teacher token")
    print(f"identity OK: teacher {TEACHER}")


def verify_owned_new_assessment(state):
    aid = state.get("assessment_id")
    if not aid:
        raise Stop("No assessment_id recorded; run step 3 first")
    if aid in PROTECTED_ASSESSMENTS:
        raise Stop("Refusing to touch a protected assessment")
    rows = sql_one("SELECT teacher_id, course_code, title FROM dbo.Assessment "
                   "WHERE assessment_id = ?", aid)
    if not rows or rows[0][0] != TEACHER or rows[0][1] != "CSC-101":
        raise Stop(f"Assessment {aid} is not BIIT235's CSC-101 assessment")
    return aid


# ---------------------------------------------------------------- steps

def step_check(api, state):
    verify_identity(api)
    print("state:", state or "{}")


def verify_final_draft(draft):
    accepted = [c for c in draft["constraints"]
                if c["review_status"] == "ACCEPTED"]
    others = [c for c in draft["constraints"]
              if c["review_status"] != "ACCEPTED"]
    return (draft.get("finalized") is True
            and draft["approval"] == {"value": True, "code": True}
            and len(accepted) == 1
            and accepted[0]["category"] == "value"
            and accepted[0]["text"] == RULE
            and all(c["review_status"] == "REJECTED" for c in others))


def step_2(api, state):
    verify_identity(api)

    if state.get("draft_id"):
        _, data = api.call("GET", f"/api/ai/drafts/{state['draft_id']}")
        draft = data["draft"]
        if draft.get("finalized"):
            if not verify_final_draft(draft):
                raise Stop("Recorded draft is finalized but not as intended")
            print("step 2 already complete; draft", state["draft_id"])
            return
    else:
        existing = sql_one("SELECT COUNT(*) FROM dbo.ConstraintReviewDraft "
                           "WHERE teacher_id = ?", TEACHER)[0][0]
        if existing:
            raise Stop(f"{TEACHER} already has {existing} draft(s) not recorded "
                       "in setup_state.json; refusing to create a duplicate")
        print("creating draft (calls local constraint AI; may take a while)...")
        _, data = api.call("POST", "/api/ai/drafts",
                           body("01_draft_create.json"), timeout=300)
        draft = data["draft"]
        state["draft_id"] = draft["draft_id"]
        save_state(state)
        print("draft created:", draft["draft_id"])
        print("AI suggestions:",
              [(c["category"], c["text"], c["review_status"])
               for c in draft["constraints"]])

    draft_id = state["draft_id"]

    for item in draft["constraints"]:
        if item["review_status"] == "PENDING":
            _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/actions", {
                "action": "DELETE", "category": item["category"],
                "constraint_id": item["id"]})
            draft = data["draft"]
            print(f"rejected AI {item['category']} suggestion: {item['text']!r}")

    teacher_rules = [c for c in draft["constraints"]
                     if c["review_status"] == "ACCEPTED"]
    if not teacher_rules:
        _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/actions", {
            "action": "ADD", "category": "value", "text": RULE})
        draft = data["draft"]
        print("added teacher value constraint:", RULE)
    elif [c["text"] for c in teacher_rules] != [RULE]:
        raise Stop(f"Unexpected accepted constraints: {teacher_rules}")

    for category in ("value", "code"):
        if not draft["approval"][category]:
            _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/approve",
                               {"category": category})
            draft = data["draft"]
            print("approved category:", category)

    _, data = api.call("POST", f"/api/ai/drafts/{draft_id}/finalize", {})
    _, data = api.call("GET", f"/api/ai/drafts/{draft_id}")
    if not verify_final_draft(data["draft"]):
        raise Stop("Finalized draft does not match the approved rule")
    row = sql_one("SELECT teacher_id, assessment_id, is_finalized FROM "
                  "dbo.ConstraintReviewDraft WHERE draft_id = ?", draft_id)[0]
    print(f"step 2 done: draft {draft_id} teacher={row[0]} "
          f"linked_assessment={row[1]} finalized={bool(row[2])}")


def step_3(api, state):
    verify_identity(api)
    payload = dict(body("03_assessment_create.json"))

    if state.get("assessment_id"):
        verify_owned_new_assessment(state)
        print("step 3 already complete; assessment", state["assessment_id"])
        return

    draft_id = state.get("draft_id")
    if not draft_id:
        raise Stop("Run step 2 first")

    row = sql_one("SELECT teacher_id, assessment_id, is_finalized FROM "
                  "dbo.ConstraintReviewDraft WHERE draft_id = ?", draft_id)
    if not row or row[0][0] != TEACHER or row[0][1] is not None or not row[0][2]:
        raise Stop("Draft is not a finalized, unlinked BIIT235 draft")

    dupes = sql_one("SELECT assessment_id FROM dbo.Assessment WHERE teacher_id = ? "
                    "AND title = ?", TEACHER, payload["title"])
    if dupes:
        raise Stop(f"Assessment with this title already exists: {dupes}")

    payload["draft_id"] = draft_id
    _, data = api.call("POST", "/api/assessments", payload)
    aid = int(data["assessment_id"])
    state["assessment_id"] = aid
    save_state(state)

    _, data = api.call("GET", f"/api/assessments/{aid}")
    a = data["assessment"]
    checks = {
        "teacher": a["teacher_id"] == TEACHER,
        "course": a["course_code"] == "CSC-101",
        "marks": a["total_marks"] == 10.0,
        "validation": a["validation_percentage"] == 50.0,
        "deadline": a["deadline_at"].startswith("2026-12-15T23:59"),
        "late": a["allow_late"] is False and a["late_penalty_marks"] == 0.0,
        "value_constraints": a["value_constraints"] == RULE,
        "code_constraints": (a["code_constraints"] or "") == "",
    }
    linked = sql_one("SELECT assessment_id FROM dbo.ConstraintReviewDraft "
                     "WHERE draft_id = ?", draft_id)[0][0]
    checks["draft_linked"] = linked == aid
    print(f"step 3 done: assessment {aid}", checks)
    if not all(checks.values()):
        raise Stop("Created assessment does not match the payload")


def step_4(api, state):
    verify_identity(api)
    aid = verify_owned_new_assessment(state)
    wanted = body("04_rubric.json")["criteria"]

    _, data = api.call("GET", f"/api/assessments/{aid}/rubric")
    rubric = data["rubric"]
    current = [(c["criterion_name"], c["max_marks"]) for c in rubric["criteria"]]
    target = [(c["criterion_name"], f"{c['max_marks']:.2f}") for c in wanted]

    if current == target and rubric["rubric_complete"]:
        print("step 4 already complete")
    elif current:
        raise Stop(f"Unexpected existing rubric: {current}")
    else:
        if rubric["locked"]:
            raise Stop("Rubric is locked (submissions exist)")
        api.call("PUT", f"/api/assessments/{aid}/rubric", {"criteria": wanted})

    _, data = api.call("GET", f"/api/assessments/{aid}/rubric")
    rubric = data["rubric"]
    total = sum(float(c["max_marks"]) for c in rubric["criteria"])
    print("step 4 done:", {
        "criteria": [(c["rubric_criterion_id"], c["criterion_name"],
                      c["max_marks"]) for c in rubric["criteria"]],
        "total": total, "allocation": rubric["rubric_marks_allocated"],
        "complete": rubric["rubric_complete"]})
    if total != 5.0 or not rubric["rubric_complete"]:
        raise Stop("Rubric total is not 5")
    state["rubric_criterion_ids"] = [c["rubric_criterion_id"]
                                     for c in rubric["criteria"]]
    save_state(state)


def step_5(api, state):
    verify_identity(api)
    aid = verify_owned_new_assessment(state)
    source = body("05_reference_solution.json")["source_code"]

    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_HASH:
        raise Stop("Reference payload hash differs from the reviewed source")

    status, data = api.call("GET", f"/api/assessments/{aid}/reference-solution",
                            ok=(200, 404))
    if status == 404:
        _, data = api.call("POST", f"/api/assessments/{aid}/reference-solution",
                           {"source_code": source})
        ref = data["reference_solution"]
        print("reference saved:", ref["reference_solution_id"],
              ref["approval_status"])
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
    print("step 5 done:", {"reference_solution_id": ref["reference_solution_id"],
                           "status": ref["approval_status"],
                           "hash": ref["source_hash"]})


STEPS = {"check": step_check, "2": step_2, "3": step_3, "4": step_4,
         "5": step_5}


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in STEPS:
        sys.exit("usage: run_csc101_setup.py <token_file> check|2|3|4|5")
    token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    state = load_state()
    try:
        STEPS[sys.argv[2]](Api(token), state)
    except Stop as stop:
        sys.exit(f"STOPPED: {stop}")


if __name__ == "__main__":
    main()
