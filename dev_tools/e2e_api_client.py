"""
DEVELOPMENT-ONLY driver for the real end-to-end test through the REAL
Flask API on http://127.0.0.1:5000, using tokens obtained by
fixtures/teacher_login.py and fixtures/student_login.py (typed by the
owner; tokens are read from files and never printed).

Each step is a separate command so every database write is deliberate:

  read-only  status | assignable | student-view | teacher-list |
             teacher-view <submission_id> | student-result
  WRITES     assign                      (AssessmentSection row)
             submit <fixture_key>        (Submission + GradingJob)
             finalize <submission_id> <final_score> [--feedback TEXT]
  no write   duplicate <fixture_key>     (expects HTTP 409)

Grading itself is NOT done here: dev_tools/fixture_submission_grader.py.
Student-facing responses are scanned for hidden test inputs / expected
outputs (values loaded read-only from the database, never printed).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dev_tools import trusted_execution_harness as teh  # noqa: E402

BASE = "http://127.0.0.1:5000"
ASSESSMENT_ID = teh.ASSESSMENT_ID
SECTION_ID = "SEC000001"
TOKEN_DIR = Path(__file__).resolve().parent / ".tokens"
TEACHER_TOKEN = TOKEN_DIR / "teacher.token"
# Approved plan: one reviewed fixture per dedicated test student.
STUDENT_FIXTURE = {"E2E-TEST-0001": "prime_correct",
                   "E2E-TEST-0002": "prime_buggy_square",
                   "E2E-TEST-0003": "prime_compile_error",
                   # criterion-override scenario (separately approved)
                   "E2E-TEST-0004": "prime_buggy_square"}


def parse_criteria(items):
    """['Logic & Algorithm=1.00', 'Code Quality=2'] -> API criteria list."""
    criteria = []
    for item in items or []:
        name, sep, marks = item.rpartition("=")
        if not sep or not name.strip() or not marks.strip():
            sys.exit(f"REFUSED: --criterion must be NAME=MARKS, got {item!r}")
        criteria.append({"criterion_name": name.strip(), "marks": marks.strip()})
    return criteria
STUDENT_STEPS = {"student-view", "submit", "duplicate", "student-result"}


def student_token(student_id):
    return TOKEN_DIR / f"{student_id}.token"


def require_identity(token_path, role, identity, me_fn=None):
    """The token must belong to exactly this user and role."""
    status, payload = (me_fn or (lambda: call("GET", "/api/auth/me", token_path)))()
    if status != 200 or payload.get("role") != role \
            or payload.get("user_id") != identity:
        sys.exit(f"REFUSED: token is not for {role} {identity}")


def _headers(path):
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        sys.exit(f"Missing token file {path.name}: run the login script first")
    return {"Authorization": f"Bearer {token}"}


def call(method, url, token_path, body=None):
    response = requests.request(method, BASE + url, json=body,
                                headers=_headers(token_path), timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    return response.status_code, payload


REQUIRED_DATABASE = "FYP_E2E_Test"


def require_test_backend(status_fn=None):
    """
    Every step must target the isolated E2E database: the running backend
    (teacher-only /api/grading/status) and this process (hidden-value scan).
    """
    import db
    if db.database_name() != REQUIRED_DATABASE:
        sys.exit(f"REFUSED: this driver must use {REQUIRED_DATABASE}")
    status, payload = (status_fn or (lambda: call(
        "GET", "/api/grading/status", TEACHER_TOKEN)))()
    if status != 200 or payload.get("active_database") != REQUIRED_DATABASE:
        sys.exit(f"REFUSED: backend is not running against {REQUIRED_DATABASE} "
                 f"(reports {payload.get('active_database')!r})")
    if payload.get("student_execution_enabled"):
        sys.exit("REFUSED: production student execution is enabled")


def hidden_values():
    """Hidden inputs and expected outputs (read-only DB), never printed."""
    from dev_tools.grading_preview_harness import install_read_only_guard
    import grading_test_case_loader
    install_read_only_guard()
    tests = grading_test_case_loader.load_grading_test_cases(ASSESSMENT_ID)
    return tests


def scan_student_payload(payload, tests):
    """Hidden data a student response must never contain."""
    text = json.dumps(payload)
    leaks = []
    for t in tests:
        for kind, value in (("input", t["input_data"].strip()),
                            ("expected_output", t["expected_output"].strip())):
            # Short public tokens (YES/NO, 1-2 digit numbers) appear in
            # ordinary text; same thresholds as the production redaction.
            minimum = 3 if kind == "input" else 6
            if len(value) >= minimum and re.search(
                    r"(?<![\w.])" + re.escape(value) + r"(?!\w|\.\d)", text):
                leaks.append(f"test {t['test_number']} {kind}")
    for key in ("input_data", "expected_output", "test_cases",
                "suggested_score", "auto_evaluation_feedback"):
        if f'"{key}"' in text:
            leaks.append(f"field {key}")
    return leaks


def show(status, payload):
    print("HTTP", status)
    print(json.dumps(payload, indent=2, default=str)[:6000])


def fixture_source(key):
    teh.fixture_path(key)                     # only the three fixture keys
    raw = teh.fixture_path(key).read_bytes()  # exact bytes: hash must match
    return raw.decode("utf-8"), teh.FIXTURES[key]


def main():
    parser = argparse.ArgumentParser(description="E2E API steps")
    parser.add_argument("--student", choices=sorted(STUDENT_FIXTURE),
                        help="test student for student-side steps")
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("status", "assignable", "assign", "student-view",
                 "teacher-list", "student-result"):
        sub.add_parser(name)
    for name in ("submit", "duplicate"):
        p = sub.add_parser(name)
        p.add_argument("fixture", choices=sorted(teh.FIXTURES))
    p = sub.add_parser("teacher-view")
    p.add_argument("submission_id", type=int)
    p = sub.add_parser("finalize")
    p.add_argument("submission_id", type=int)
    p.add_argument("final_score")
    p.add_argument("--feedback")
    p.add_argument("--criterion", action="append",
                   help="teacher-approved criterion mark NAME=MARKS (repeat)")
    args = parser.parse_args()

    import db
    db.select_database(REQUIRED_DATABASE)    # hidden-value scan reads here
    require_test_backend()
    require_identity(TEACHER_TOKEN, "teacher", "BIIT235")
    STUDENT_TOKEN = None
    if args.step in STUDENT_STEPS:
        if not args.student:
            sys.exit("REFUSED: --student is required for student steps")
        STUDENT_TOKEN = student_token(args.student)
        require_identity(STUDENT_TOKEN, "student", args.student)
        if args.step in ("submit", "duplicate") and \
                args.fixture != STUDENT_FIXTURE[args.student]:
            sys.exit(f"REFUSED: approved fixture for {args.student} is "
                     f"{STUDENT_FIXTURE[args.student]}")
    a = ASSESSMENT_ID
    if args.step == "status":
        show(*call("GET", "/api/grading/status", TEACHER_TOKEN))
    elif args.step == "assignable":
        show(*call("GET", f"/api/assessments/{a}/assignable-sections", TEACHER_TOKEN))
    elif args.step == "assign":
        show(*call("POST", f"/api/assessments/{a}/sections", TEACHER_TOKEN,
                   {"section_ids": [SECTION_ID]}))
    elif args.step == "student-view":
        status, payload = call("GET", "/api/student/assessments", STUDENT_TOKEN)
        items = [x for x in payload.get("assessments", [])
                 if x.get("assessment_id") == a]
        show(status, {"assessment_5": items})
        print("hidden-data leaks:", scan_student_payload(payload, hidden_values()))
    elif args.step in ("submit", "duplicate"):
        source, file_name = fixture_source(args.fixture)
        status, payload = call("POST", f"/api/assessments/{a}/submissions",
                               STUDENT_TOKEN,
                               {"source_code": source, "file_name": file_name})
        show(status, payload)
        if args.step == "duplicate" and status != 409:
            sys.exit("EXPECTED HTTP 409 for a duplicate submission")
    elif args.step == "teacher-list":
        show(*call("GET", f"/api/assessments/{a}/submissions", TEACHER_TOKEN))
    elif args.step == "teacher-view":
        show(*call("GET", f"/api/submissions/{args.submission_id}/evaluation",
                   TEACHER_TOKEN))
    elif args.step == "finalize":
        body = {"final_score": args.final_score}
        if args.feedback:
            body["instructor_feedback"] = args.feedback
        if args.criterion:
            body["criteria"] = parse_criteria(args.criterion)
        show(*call("POST",
                   f"/api/submissions/{args.submission_id}/evaluation/finalize",
                   TEACHER_TOKEN, body))
    elif args.step == "student-result":
        status, payload = call("GET", f"/api/student/assessments/{a}/submission",
                               STUDENT_TOKEN)
        show(status, payload)
        print("hidden-data leaks:", scan_student_payload(payload, hidden_values()))


if __name__ == "__main__":
    main()
