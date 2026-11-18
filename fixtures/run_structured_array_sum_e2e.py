"""
DEVELOPMENT-ONLY driver for the structured-input E2E fixture (array sum),
run ONLY through the normal authenticated APIs of a backend started with
dev_tools/run_e2e_backend.py (database FYP_E2E_Test).

    python fixtures/run_structured_array_sum_e2e.py check
    python fixtures/run_structured_array_sum_e2e.py <step> --confirm FYP_E2E_Test
        step: draft | assessment | reference | generate | rubric | assign
    python fixtures/run_structured_array_sum_e2e.py teacher-view
    python fixtures/run_structured_array_sum_e2e.py student-view --student E2E-TEST-0001

  read-only  check, verify, teacher-view, student-view
  WRITES     draft       ConstraintReviewDraft (AI draft, rejections, the
                         teacher value rule, input schema, approvals, finalize)
             assessment  Assessment (linked to the finalized draft)
             reference   ReferenceSolution (PENDING -> APPROVED; nothing runs)
             generate    TestCase x10 ONLY if the backend can verify expected
                         outputs (sandbox, or owner-allow-listed hash);
                         otherwise HTTP 409 EXPECTED_OUTPUT_UNVERIFIABLE and
                         nothing is saved
             rubric      AssessmentRubricCriterion x2 (07_rubric.json); refuses
                         to overwrite a different rubric or a locked one
             assign      AssessmentSection x1 (SEC000001); requires a complete
                         rubric first; refuses if other sections are assigned

- student-view uses dev_tools/.tokens/<student>.token (fixtures/student_login.py,
  typed by the owner) and never submits code; nothing here starts grading.

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
import re
import sys
from decimal import Decimal
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:5000"
TEACHER = "BIIT235"
REQUIRED_DATABASE = "FYP_E2E_Test"
DATA = ROOT / "fixtures" / "structured_array_sum"
STATE_FILE = DATA / "setup_state.json"
TOKEN_DIR = ROOT / "dev_tools" / ".tokens"
TOKEN_FILE = TOKEN_DIR / "teacher.token"
EXPECTED_HASH = "4ea30854b29ac9ceafe7392c71a8e5ca039d98fe91ce86137654a2567c0d8e40"
PROTECTED_ASSESSMENTS = {2, 4, 5}
WRITE_STEPS = {"draft", "assessment", "reference", "generate", "rubric", "assign",
               "w3-submit", "w3-manual-grade"}
W3_STUDENT = "E2E-TEST-0001"
W3_SOURCE_FILE = DATA / "w3_synthetic_submission.cpp"
W3_SOURCE_HASH = "3521ad6393b50e44fb472dcc1b6ba650d4418022faf36bc87417244849bded65"
W3_FINAL_SCORE = Decimal("5.00")
WRITE_STEPS |= {"l-submit", "l-finalize"}
L_STUDENT = "E2E-TEST-0002"
L_FIXTURE_FILE = ROOT / "dev_tools" / "trusted_fixtures" / "array_sum_int_accumulator.cpp"
L_SOURCE_HASH = "ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4"
L_AUTO_STATUS = "AI_ANALYSIS_UNVERIFIED"
SECTION_ID = "SEC000001"
E2E_STUDENTS = ("E2E-TEST-0001", "E2E-TEST-0002", "E2E-TEST-0003", "E2E-TEST-0004")
TEST_CASE_COUNT = 10


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


# ------------------------------------------------ A6 demo setup (W1 / W2)

def planned_rubric():
    return body("07_rubric.json")["criteria"]


def rubric_matches(criteria, planned):
    """Stored criteria (API form) equal the planned ones, in order."""
    def norm(c):
        return (c["criterion_name"].strip(), Decimal(str(c["max_marks"])),
                (c.get("description") or "").strip())
    return [norm(c) for c in criteria] == [norm(c) for c in planned]


def check_test_cases(rows):
    """rows: [(test_case_id, test_number, marks)] -> (problems, total marks)."""
    problems = []
    if len(rows) != TEST_CASE_COUNT:
        problems.append(f"{len(rows)} test cases, expected {TEST_CASE_COUNT}")
    if [r[1] for r in rows] != list(range(1, len(rows) + 1)):
        problems.append("test numbers are not 1..N")
    marks = {Decimal(str(r[2])) for r in rows}
    if len(marks) > 1:
        problems.append("test case marks are not equal")
    return problems, sum((Decimal(str(r[2])) for r in rows), Decimal("0"))


def test_case_rows(aid):
    return [tuple(r) for r in sql(
        "SELECT test_case_id, test_number, marks FROM dbo.TestCase "
        "WHERE assessment_id = ? ORDER BY test_number", aid)]


def require_ten_tests(aid):
    problems, total = check_test_cases(test_case_rows(aid))
    if problems:
        raise Stop("Assessment test cases are not ready: " + "; ".join(problems))
    return total


def get_rubric(api, aid):
    _, data = api.call("GET", f"/api/assessments/{aid}/rubric")
    return data["rubric"]


def step_rubric(api, state):
    aid = owned_assessment(state)
    test_total = require_ten_tests(aid)
    planned = planned_rubric()
    rubric = get_rubric(api, aid)
    if not rubric["storage_available"]:
        raise Stop("Rubric storage is not available on this backend")
    if rubric["criteria"]:
        if not rubric_matches(rubric["criteria"], planned):
            raise Stop("A different rubric already exists; not overwriting it")
        print("rubric step already complete")
    else:
        if rubric["locked"]:
            raise Stop("Rubric is locked (submissions exist)")
        planned_total = sum((Decimal(c["max_marks"]) for c in planned), Decimal("0"))
        if Decimal(rubric["rubric_marks_allocated"]) != planned_total:
            raise Stop(f"Rubric allocation {rubric['rubric_marks_allocated']} "
                       f"does not equal the planned {planned_total}")
        api.call("PUT", f"/api/assessments/{aid}/rubric", {"criteria": planned})
        rubric = get_rubric(api, aid)
    if not (rubric["rubric_complete"] and rubric_matches(rubric["criteria"], planned)):
        raise Stop("Saved rubric does not match the planned criteria")
    if Decimal(rubric["test_marks_allocated"]) != test_total:
        raise Stop(f"Test allocation {rubric['test_marks_allocated']} != "
                   f"sum of test case marks {test_total}")
    state["rubric_criterion_ids"] = [c["rubric_criterion_id"] for c in rubric["criteria"]]
    save_state(state)
    print("rubric:", [(c["rubric_criterion_id"], c["criterion_name"], c["max_marks"])
                      for c in rubric["criteria"]],
          "| rubric", rubric["rubric_marks_allocated"], "+ tests", rubric["test_marks_allocated"],
          "=", rubric["total_marks"])


def get_sections(api, aid):
    _, data = api.call("GET", f"/api/assessments/{aid}/sections")
    return data["sections"]


def step_assign(api, state):
    aid = owned_assessment(state)
    require_ten_tests(aid)
    if not get_rubric(api, aid)["rubric_complete"]:
        raise Stop("Run the rubric step first (students cannot submit without it)")
    sections = get_sections(api, aid)
    ids = [s["section_id"] for s in sections]
    if ids and ids != [SECTION_ID]:
        raise Stop(f"Assessment is already assigned to other sections: {ids}")
    if not ids:
        _, data = api.call("GET", f"/api/assessments/{aid}/assignable-sections")
        match = [s for s in data["sections"] if s["section_id"] == SECTION_ID]
        if not match:
            raise Stop(f"{SECTION_ID} is not assignable for this teacher and course")
        api.call("POST", f"/api/assessments/{aid}/sections",
                 body("08_section_assignment.json"))
        sections = get_sections(api, aid)
    else:
        print("assign step already complete")
    if [s["section_id"] for s in sections] != [SECTION_ID]:
        raise Stop("Section assignment did not produce exactly SEC000001")
    state["assessment_section_id"] = sections[0]["assessment_section_id"]
    save_state(state)
    print("assigned:", SECTION_ID, "| assessment_section_id",
          sections[0]["assessment_section_id"], "| enrolled",
          sections[0]["enrolled_students"], "| submissions", sections[0]["submission_count"])


def step_teacher_view(api, state):
    aid = owned_assessment(state)
    expected = body("03_assessment_create.json")
    _, data = api.call("GET", f"/api/assessments/{aid}")
    a = data["assessment"]
    checks = {
        "title": a["title"] == expected["title"],
        "total_marks 10": Decimal(str(a["total_marks"])) == Decimal("10"),
        "validation 50 %": Decimal(str(a["validation_percentage"])) == Decimal("50"),
    }
    rubric = get_rubric(api, aid)
    checks["rubric complete + planned"] = (rubric["rubric_complete"]
                                           and rubric_matches(rubric["criteria"], planned_rubric()))
    sections = get_sections(api, aid)
    checks["assigned only SEC000001"] = [s["section_id"] for s in sections] == [SECTION_ID]
    _, data = api.call("GET", f"/api/assessments/{aid}/test-cases")
    tests = data["test_cases"]
    problems, total = check_test_cases(
        [(t["test_case_id"], t["test_number"], t["marks"]) for t in tests])
    checks["10 equal-weight test cases"] = not problems
    checks["test marks == rubric test allocation"] = (
        total == Decimal(rubric["test_marks_allocated"]))
    planned = json.loads((DATA / "planned_test_cases.json").read_text(encoding="utf-8"))["cases"]
    checks["tests match oracle"] = not compare_with_oracle(
        [(t["test_number"], t["input_data"], t["expected_output"]) for t in tests], planned)
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    print("  test_case_ids:", [t["test_case_id"] for t in tests],
          "| sections:", [(s["section_id"], s["enrolled_students"], s["submission_count"])
                          for s in sections], "| rubric locked:", rubric["locked"])
    if not all(checks.values()):
        raise Stop("teacher-view verification failed")
    print("teacher-view: OK")


PUBLIC_FIELDS = ("problem_statement", "input_format", "output_format")


def hidden_leaks(payload, tests, public_text=""):
    """
    Hidden test values / grading fields a student payload must never contain.
    A value that already appears in the assessment's published statement text
    (e.g. the documented bound -1000000000) is public, so it is not a leak.
    """
    text = json.dumps(payload)
    leaks = []

    def found(value, haystack):
        encoded = json.dumps(value)[1:-1]              # as it appears inside JSON
        return re.search(r"(?<![\w.])" + re.escape(encoded) + r"(?!\w|\.\d)", haystack)

    public = json.dumps(public_text)
    for number, input_data, expected in tests:
        for kind, value, minimum in (("input", input_data.strip(), 3),
                                     ("expected_output", expected.strip(), 6)):
            if len(value) >= minimum and found(value, text) and not found(value, public):
                leaks.append(f"test {number} {kind}")
    for key in ("input_data", "expected_output", "test_cases", "suggested_score",
                "auto_evaluation_feedback"):
        if f'"{key}"' in text:
            leaks.append(f"field {key}")
    return leaks


def step_student_view(api, state, student=None, student_api=None):
    aid = owned_assessment(state)
    if student not in E2E_STUDENTS:
        raise Stop(f"--student must be one of {', '.join(E2E_STUDENTS)}")
    if student_api is None:
        try:
            token = (TOKEN_DIR / f"{student}.token").read_text(encoding="utf-8").strip()
        except OSError:
            raise Stop(f"Missing token: run fixtures/student_login.py for {student}")
        student_api = Api(token)
    _, me = student_api.call("GET", "/api/auth/me")
    if me.get("role") != "student" or me.get("user_id") != student:
        raise Stop(f"token is not a student token for {student}")
    tests = [tuple(r) for r in sql(
        "SELECT test_number, input_data, expected_output FROM dbo.TestCase "
        "WHERE assessment_id = ? ORDER BY test_number", aid)]
    checks = {}
    _, data = student_api.call("GET", "/api/student/assessments")
    item = [x for x in data.get("assessments", []) if x.get("assessment_id") == aid]
    checks["assessment listed once"] = len(item) == 1
    if item:
        checks["open for submissions"] = item[0].get("open_for_submissions") is True
        checks["not yet submitted"] = item[0].get("has_submitted") is False
    published = sql("SELECT problem_statement, input_format, output_format "
                    "FROM dbo.Assessment WHERE assessment_id = ?", aid)
    leaks = hidden_leaks(data, tests, "\n".join(v or "" for v in published[0]))
    checks["no hidden data in list"] = not leaks
    status, data = student_api.call("GET", f"/api/student/assessments/{aid}/submission",
                                    ok=(200, 404))
    checks["no submission (404)"] = status == 404
    for path in (f"/api/assessments/{aid}", f"/api/assessments/{aid}/rubric",
                 f"/api/assessments/{aid}/test-cases", f"/api/assessments/{aid}/sections"):
        status, _ = student_api.call("GET", path, ok=(200, 403, 404))
        checks[f"403 on teacher route {path}"] = status == 403
    audience = sql(
        "SELECT DISTINCT e.student_id FROM dbo.Enrollment_Export AS e "
        "JOIN dbo.AssessmentSection AS s ON s.section_id COLLATE DATABASE_DEFAULT "
        "  = e.section_id COLLATE DATABASE_DEFAULT "
        "JOIN dbo.Assessment AS a ON a.assessment_id = s.assessment_id "
        "  AND a.course_code COLLATE DATABASE_DEFAULT = e.course_no COLLATE DATABASE_DEFAULT "
        "WHERE s.assessment_id = ? ORDER BY 1", aid)
    audience = [r[0] for r in audience]
    checks["audience = the 4 E2E students"] = audience == list(E2E_STUDENTS)
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    if leaks:
        print("  leaks:", leaks)
    print("  audience:", audience)
    if not all(checks.values()):
        raise Stop("student-view verification failed")
    print(f"student-view {student}: OK")


# ------------------------------------------------------- W3 manual grading

def student_api(student):
    if student not in E2E_STUDENTS:
        raise Stop(f"student must be one of {', '.join(E2E_STUDENTS)}")
    try:
        token = (TOKEN_DIR / f"{student}.token").read_text(encoding="utf-8").strip()
    except OSError:
        raise Stop(f"Missing token: run fixtures/student_login.py for {student}")
    api = Api(token)
    _, me = api.call("GET", "/api/auth/me")
    if me.get("role") != "student" or me.get("user_id") != student:
        raise Stop(f"token is not a student token for {student}")
    return api


def w3_source():
    raw = W3_SOURCE_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != W3_SOURCE_HASH:
        raise Stop("Synthetic W3 source differs from the reviewed bytes")
    payload = body("09_w3_submission.json")
    if payload["source_code"].encode("utf-8") != raw:
        raise Stop("09_w3_submission.json does not match the reviewed source")
    return payload


def a6_submissions(aid):
    """[(submission_id, student_id, job_id, job_status, attempt_count, worker_id, evaluation)]"""
    return [tuple(r) for r in sql(
        "SELECT s.submission_id, s.student_id, j.job_id, j.job_status, j.attempt_count, "
        "       j.worker_id, e.auto_evaluation_status "
        "FROM dbo.Submission AS s "
        "JOIN dbo.AssessmentSection AS x ON x.assessment_section_id = s.assessment_section_id "
        "LEFT JOIN dbo.GradingJob AS j ON j.submission_id = s.submission_id "
        "LEFT JOIN dbo.Evaluation AS e ON e.submission_id = s.submission_id "
        "WHERE x.assessment_id = ? ORDER BY s.submission_id", aid)]


def step_w3_submit(api, state, student_api_factory=student_api):
    aid = owned_assessment(state)
    require_ten_tests(aid)
    rubric = get_rubric(api, aid)
    if not rubric["rubric_complete"] or [c["rubric_criterion_id"] for c in rubric["criteria"]] \
            != state.get("rubric_criterion_ids"):
        raise Stop("Approved rubric (criteria 3 and 4) is not in place")
    if [s["section_id"] for s in get_sections(api, aid)] != [SECTION_ID]:
        raise Stop("Assessment is not assigned only to SEC000001")
    payload = w3_source()
    existing = a6_submissions(aid)
    if existing:
        if [r[1] for r in existing] == [W3_STUDENT] and state.get("w3_submission_id") == existing[0][0]:
            print("w3-submit already complete:", existing[0])
            return
        raise Stop(f"Assessment already has unexpected submissions: {existing}")
    sapi = student_api_factory(W3_STUDENT)
    _, data = sapi.call("POST", f"/api/assessments/{aid}/submissions", payload)
    sub = data["submission"]
    rows = a6_submissions(aid)
    if len(rows) != 1 or rows[0][0] != sub["submission_id"] or rows[0][1] != W3_STUDENT:
        raise Stop(f"Unexpected submission state after submit: {rows}")
    sid, _, job_id, job_status, attempts, worker, evaluation = rows[0]
    if (job_status, attempts, worker, evaluation) != ("PENDING", 0, None, None):
        raise Stop(f"Grading job is not an untouched PENDING job: {rows[0]}")
    state["w3_submission_id"], state["w3_job_id"] = sid, job_id
    save_state(state)
    print("submitted:", {"submission_id": sid, "job_id": job_id, "job_status": job_status,
                         "attempt_count": attempts, "worker_id": worker,
                         "evaluation": evaluation, "late": sub.get("is_late")})


def step_w3_manual_grade(api, state):
    aid = owned_assessment(state)
    sid = state.get("w3_submission_id")
    if not sid:
        raise Stop("Run w3-submit first")
    rows = a6_submissions(aid)
    if [r[0] for r in rows] != [sid]:
        raise Stop(f"Unexpected submissions for the assessment: {rows}")
    status, sandbox = api.call("GET", "/api/grading/status")
    if sandbox.get("student_execution_enabled"):
        raise Stop("Student execution is enabled; manual W3 grading is not allowed")
    job_status, evaluation = rows[0][3], rows[0][6]
    if evaluation == "MANUALLY_GRADED":
        print("w3-manual-grade already complete")
    elif evaluation is not None or job_status != "PENDING":
        raise Stop(f"Submission is not an ungraded PENDING submission: {rows[0]}")
    else:
        grade = body("10_w3_manual_grade.json")
        _, data = api.call("POST", f"/api/submissions/{sid}/evaluation/manual-grade", grade)
        print("manual-grade response:", data["evaluation"])
    ev = sql("SELECT auto_evaluation_status, result_status, final_score, suggested_score, "
             "tests_passed, tests_run FROM dbo.Evaluation WHERE submission_id = ?", sid)
    job = sql("SELECT job_status, worker_id, attempt_count, claim_token FROM dbo.GradingJob "
              "WHERE submission_id = ?", sid)
    approvals = sql("SELECT COUNT_BIG(*) FROM dbo.EvaluationCriterionApproval "
                    "WHERE submission_id = ?", sid)[0][0]
    ev, job = tuple(ev[0]), tuple(job[0])
    expected_ev = ("MANUALLY_GRADED", "FINALIZED", W3_FINAL_SCORE, None, 0, 0)
    expected_job = ("COMPLETED", f"manual-grade:{TEACHER}", 0, None)
    if (ev[:2], Decimal(str(ev[2])), ev[3:]) != (expected_ev[:2], expected_ev[2], expected_ev[3:]) \
            or job != expected_job or approvals != 0:
        raise Stop(f"Unexpected result: evaluation {ev}, job {job}, approvals {approvals}")
    print("evaluation:", ev, "| job:", job, "| criterion approval rows:", approvals)


def _dec(value):
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def step_w3_verify(api, state, student_api_factory=student_api):
    aid = owned_assessment(state)
    sid = state.get("w3_submission_id")
    if not sid:
        raise Stop("Run w3-submit first")
    checks = {}
    _, data = api.call("GET", f"/api/submissions/{sid}/evaluation")
    teacher = data["submission"]
    print("teacher evaluation view keys:", sorted(teacher))
    _, lst = api.call("GET", f"/api/assessments/{aid}/submissions")
    checks["teacher list: exactly 1 submission"] = lst["total"] == 1
    audit = sql("SELECT auto_evaluation_feedback FROM dbo.Evaluation WHERE submission_id = ?", sid)
    from manual_grading_service import parse_manual_record
    record = parse_manual_record(audit[0][0]) if audit else None
    checks["audit record: BIIT235, 5.00, not auto-graded, PENDING job"] = bool(record) and (
        record["teacher_id"] == TEACHER and Decimal(record["final_score"]) == W3_FINAL_SCORE
        and record["automatic_grading_performed"] is False
        and record["previous_job"]["job_status"] == "PENDING"
        and "NOT AUTO-EXECUTED" in record["justification"])
    tests = [tuple(r) for r in sql(
        "SELECT test_number, input_data, expected_output FROM dbo.TestCase "
        "WHERE assessment_id = ? ORDER BY test_number", aid)]
    published = "\n".join(v or "" for v in sql(
        "SELECT problem_statement, input_format, output_format FROM dbo.Assessment "
        "WHERE assessment_id = ?", aid)[0])
    for student in E2E_STUDENTS:
        sapi = student_api_factory(student)
        status, mine = sapi.call("GET", f"/api/student/assessments/{aid}/submission",
                                 ok=(200, 404))
        _, listing = sapi.call("GET", "/api/student/assessments")
        item = [x for x in listing["assessments"] if x["assessment_id"] == aid]
        teacher_status, _ = sapi.call("GET", f"/api/submissions/{sid}/evaluation",
                                      ok=(200, 403, 404))
        checks[f"{student}: teacher evaluation route 403"] = teacher_status == 403
        checks[f"{student}: no hidden data"] = not hidden_leaks([mine, listing], tests, published)
        if student == W3_STUDENT:
            sub = mine.get("submission") or {}
            result = sub.get("result") or {}
            print(f"{student} result:", json.dumps(result, indent=1, default=str))
            checks[f"{student}: own result published"] = (
                status == 200 and sub.get("submission_id") == sid
                and sub.get("status") == "RESULT_PUBLISHED")
            checks[f"{student}: marks 5.00/10.00, manual, tests not shown as run"] = (
                result.get("outcome") == "MANUALLY_GRADED"
                and _dec(result.get("final_score")) == W3_FINAL_SCORE
                and _dec(result.get("total_marks")) == Decimal("10")
                and result.get("tests_passed") is None and result.get("tests_run") is None
                and "NOT AUTO-EXECUTED" in (result.get("instructor_feedback") or ""))
            checks[f"{student}: listed as submitted"] = bool(item) and item[0]["has_submitted"]
        else:
            checks[f"{student}: no submission/result (404)"] = status == 404
            checks[f"{student}: A6 still available"] = bool(item) and \
                item[0]["open_for_submissions"] is True and item[0]["has_submitted"] is False
    rows = a6_submissions(aid)
    checks["DB: only E2E-TEST-0001 submitted"] = [r[1] for r in rows] == [W3_STUDENT]
    checks["rubric locked"] = get_rubric(api, aid)["locked"] is True
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    if not all(checks.values()):
        raise Stop("w3-verify failed")
    print("w3-verify: OK")


# ------------------------------------------- Gate L: automatic LLM analysis

def l_source():
    raw = L_FIXTURE_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != L_SOURCE_HASH:
        raise Stop("Gate L demo source differs from the reviewed bytes")
    return {"file_name": L_FIXTURE_FILE.name, "source_code": raw.decode("utf-8")}


def l_rows(aid, state):
    rows = a6_submissions(aid)
    expected_others = [(state.get("w3_submission_id"), W3_STUDENT)]
    others = [(r[0], r[1]) for r in rows if r[1] != L_STUDENT]
    if others != expected_others:
        raise Stop(f"Unexpected submissions for the assessment: {rows}")
    return [r for r in rows if r[1] == L_STUDENT]


def step_l_submit(api, state, student_api_factory=student_api):
    aid = owned_assessment(state)
    require_ten_tests(aid)
    rubric = get_rubric(api, aid)
    if not rubric["rubric_complete"] or [c["rubric_criterion_id"] for c in rubric["criteria"]] \
            != state.get("rubric_criterion_ids"):
        raise Stop("Approved rubric (criteria 3 and 4) is not in place")
    if [s["section_id"] for s in get_sections(api, aid)] != [SECTION_ID]:
        raise Stop("Assessment is not assigned only to SEC000001")
    payload = l_source()
    mine = l_rows(aid, state)
    if mine:
        if state.get("l_submission_id") == mine[0][0]:
            print("l-submit already complete:", mine[0])
            return
        raise Stop(f"{L_STUDENT} already has an unrecorded submission: {mine}")
    sapi = student_api_factory(L_STUDENT)
    _, data = sapi.call("POST", f"/api/assessments/{aid}/submissions", payload)
    sub = data["submission"]
    mine = l_rows(aid, state)
    if len(mine) != 1 or mine[0][0] != sub["submission_id"]:
        raise Stop(f"Unexpected state after submit: {mine}")
    sid, _, job_id, job_status, attempts, worker, evaluation = mine[0]
    if (job_status, attempts, worker, evaluation) != ("PENDING", 0, None, None):
        raise Stop(f"Grading job is not an untouched PENDING job: {mine[0]}")
    state["l_submission_id"], state["l_job_id"] = sid, job_id
    save_state(state)
    print("submitted:", {"submission_id": sid, "job_id": job_id, "job_status": job_status,
                         "attempt_count": attempts, "evaluation": evaluation})


def l_teacher_view(api, state):
    sid = state.get("l_submission_id")
    if not sid:
        raise Stop("Run l-submit first")
    _, data = api.call("GET", f"/api/submissions/{sid}/evaluation")
    return data["submission"]


def step_l_report(api, state):
    owned_assessment(state)
    sub = l_teacher_view(api, state)
    ev = sub.get("evaluation") or {}
    report = ev.get("evaluation_report") or {}
    tests = report.get("test_execution") or {}
    checks = {
        "auto status AI_ANALYSIS_UNVERIFIED": ev.get("auto_evaluation_status") == L_AUTO_STATUS,
        "teacher sees NEEDS_REVIEW": sub.get("grading_status") == "AUTO_GRADED_NEEDS_REVIEW",
        "stored tests 0/0 (none run)": (ev.get("tests_passed"), ev.get("tests_run")) == (0, 0),
        "report mode LLM_ANALYSIS": report.get("evaluation_mode") == "LLM_ANALYSIS",
        "report tests NOT_RUN, no counts": tests.get("status") == "NOT_RUN"
        and tests.get("tests_passed") is None and tests.get("failed_test_numbers") == [],
        "no test marks suggested": (report.get("suggested_marks") or {}).get("test_marks") is None
        and ev.get("verified_test_marks") is None,
        "no test citations": all(r["cited_failed_tests"] == [] for r in report.get("rubric", [])),
    }
    print(json.dumps({k: report.get(k) for k in (
        "evaluation_mode", "evidence_basis", "constraints", "test_execution",
        "logic_analysis", "rubric", "suggested_marks", "reliability_flags",
        "teacher_approval")}, indent=1))
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    if not all(checks.values()):
        raise Stop("l-report verification failed")
    print("l-report: OK")


def step_l_finalize(api, state):
    owned_assessment(state)
    sid = state.get("l_submission_id")
    sub = l_teacher_view(api, state)
    ev = sub.get("evaluation") or {}
    payload = body("11_gate_l_finalize.json")
    if ev.get("result_status") == "FINALIZED":
        if _dec(ev.get("final_score")) != _dec(payload["final_score"]):
            raise Stop("Already finalized with a different score")
        print("l-finalize already complete")
    else:
        if ev.get("auto_evaluation_status") != L_AUTO_STATUS:
            raise Stop("Evaluation is not an AI_ANALYSIS_UNVERIFIED evaluation")
        total = sum(Decimal(c["marks"]) for c in payload["criteria"])
        if total != Decimal(payload["final_score"]):
            raise Stop("final_score must equal the approved criterion total")
        _, data = api.call("POST", f"/api/submissions/{sid}/evaluation/finalize", payload)
        print("finalize response:", data["evaluation"])
    sub = l_teacher_view(api, state)
    ev = sub["evaluation"]
    approval = ev["evaluation_report"]["teacher_approval"]
    print("teacher approval:", json.dumps(approval, indent=1, default=str))
    if approval["status"] != "APPROVED_FINAL" or len(ev["teacher_criteria"]) != 2:
        raise Stop("Teacher approval was not recorded as expected")


def step_l_verify(api, state, student_api_factory=student_api):
    aid = owned_assessment(state)
    sid = state.get("l_submission_id")
    if not sid:
        raise Stop("Run l-submit first")
    tests = [tuple(r) for r in sql(
        "SELECT test_number, input_data, expected_output FROM dbo.TestCase "
        "WHERE assessment_id = ? ORDER BY test_number", aid)]
    published = "\n".join(v or "" for v in sql(
        "SELECT problem_statement, input_format, output_format FROM dbo.Assessment "
        "WHERE assessment_id = ?", aid)[0])
    checks = {}
    for student in E2E_STUDENTS:
        sapi = student_api_factory(student)
        status, mine = sapi.call("GET", f"/api/student/assessments/{aid}/submission",
                                 ok=(200, 404))
        _, listing = sapi.call("GET", "/api/student/assessments")
        checks[f"{student}: no hidden data"] = not hidden_leaks([mine, listing], tests, published)
        teacher_status, _ = sapi.call("GET", f"/api/submissions/{sid}/evaluation",
                                      ok=(200, 403, 404))
        checks[f"{student}: teacher evaluation route 403"] = teacher_status == 403
        sub = (mine or {}).get("submission") or {}
        result = sub.get("result") or {}
        if student == L_STUDENT:
            print(f"{student} result:", json.dumps(result, indent=1))
            checks[f"{student}: own finalized result"] = (
                status == 200 and sub.get("submission_id") == sid
                and sub.get("status") == "RESULT_PUBLISHED")
            checks[f"{student}: marks 3.00/10, not executed, no test counts"] = (
                result.get("outcome") == "CODE_REVIEWED_NOT_EXECUTED"
                and _dec(result.get("final_score")) == Decimal("3.00")
                and _dec(result.get("total_marks")) == Decimal("10")
                and result.get("tests_passed") is None and result.get("tests_run") is None
                and result.get("test_marks") is None
                and result.get("rubric_source") == "TEACHER_APPROVED"
                and [(c["criterion_name"], c["marks"]) for c in result.get("criteria", [])]
                == [("Logic & Correctness", "1.50"), ("Code Quality", "1.50")]
                and "NOT EXECUTED" in (result.get("instructor_feedback") or "")
                and any("not run against the hidden tests" in s
                        for s in result.get("summary", [])))
        elif student == W3_STUDENT:
            checks[f"{student}: manual 5.00 result unchanged"] = (
                status == 200 and sub.get("submission_id") == state.get("w3_submission_id")
                and result.get("outcome") == "MANUALLY_GRADED"
                and _dec(result.get("final_score")) == W3_FINAL_SCORE)
        else:
            checks[f"{student}: no submission (404)"] = status == 404
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    if not all(checks.values()):
        raise Stop("l-verify failed")
    print("l-verify: OK")


STEPS = {"check": step_check, "draft": step_draft, "assessment": step_assessment,
         "reference": step_reference, "generate": step_generate, "verify": step_verify,
         "rubric": step_rubric, "assign": step_assign, "teacher-view": step_teacher_view,
         "student-view": step_student_view, "w3-submit": step_w3_submit,
         "w3-manual-grade": step_w3_manual_grade, "w3-verify": step_w3_verify,
         "l-submit": step_l_submit, "l-report": step_l_report,
         "l-finalize": step_l_finalize, "l-verify": step_l_verify}


def main():
    parser = argparse.ArgumentParser(description="Structured array-sum E2E steps")
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--confirm")
    parser.add_argument("--student", choices=E2E_STUDENTS,
                        help="test student for student-view")
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
        if args.step == "student-view":
            step_student_view(api, load_state(), student=args.student)
        else:
            STEPS[args.step](api, load_state())
    except Stop as stop:
        sys.exit(f"STOPPED: {stop}")


if __name__ == "__main__":
    main()
