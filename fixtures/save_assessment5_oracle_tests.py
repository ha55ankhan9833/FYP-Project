"""
HISTORICAL - superseded by the 10-test rule (2026-11-17). This script
performed the approved 15-case save; it now only works against the
'superseded_15_case_preview' and will be refused by the 10-test save rule.

Owner-approved save of Assessment 5's 15 verified-oracle test cases,
through the authenticated teacher API only.

    python fixtures/save_assessment5_oracle_tests.py <token_file> preview|save|verify

preview : identity + read-only DB checks + API dry run; compares the 15
          pairs with the reviewed preview (oracle_verification.json).
save    : repeats ALL preview checks, then sends dry_run=false ONCE.
          Never retries. On an ambiguous result it only reports DB state.
verify  : read-only post-save verification.

No C++ is compiled or executed. Token is never printed.
"""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import get_connection  # noqa: E402  (read-only checks)

BASE = "http://127.0.0.1:5000"
URL = BASE + "/api/assessments/5/generate-test-cases/oracle"
TEACHER = "BIIT235"
REF_ID = 2
REF_HASH = "529e00e0a8e4000826d288f293e33e0e4753ecc49426156b9c936bb730aac9ab"
BODY = {"oracle_id": "prime_check_v1", "random_count": 3,
        "additional_inputs": ["3", "4", "25", "97", "7919", "994009", "999983"]}
REVIEWED = json.loads((ROOT / "fixtures/new_assessment_csc101/"
                       "oracle_verification.json").read_text())[
    "superseded_15_case_preview"]
REVIEWED_PAIRS = [(c["input_data"], c["expected_output"]) for c in REVIEWED]


class Stop(Exception):
    pass


def query(sql, *params):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, *params)
        # pyodbc Row objects do not compare equal to tuples
        return [tuple(row) for row in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def testcase_count(assessment_id):
    return query("SELECT COUNT(*) FROM dbo.TestCase WHERE assessment_id = ?",
                 assessment_id)[0][0]


def session(token_file):
    s = requests.Session()
    s.headers["Authorization"] = "Bearer " + Path(token_file).read_text().strip()
    me = s.get(BASE + "/api/auth/me", timeout=30).json()
    if me.get("role") != "teacher" or me.get("user_id") != TEACHER:
        raise Stop("Token is not a BIIT235 teacher token")
    print("identity OK:", TEACHER)
    return s


def db_preconditions():
    a = query("SELECT teacher_id, course_code, value_constraints, "
              "ISNULL(code_constraints,'') FROM dbo.Assessment WHERE assessment_id = 5")
    if a != [(TEACHER, "CSC-101", "1 <= N <= 1000000", "")]:
        raise Stop(f"Assessment 5 not as reviewed: {a}")
    r = query("SELECT reference_solution_id, approval_status, source_hash FROM "
              "dbo.ReferenceSolution WHERE assessment_id = 5")
    if r != [(REF_ID, "APPROVED", REF_HASH)]:
        raise Stop(f"Reference solution not as reviewed: {r}")
    if testcase_count(5):
        raise Stop("Assessment 5 already has test cases; not writing")
    print("DB preconditions OK: assessment 5 owner/constraints, reference 2 "
          "APPROVED with reviewed hash, 0 existing test cases")


def preview(s):
    r = s.post(URL, json={**BODY, "dry_run": True}, timeout=120)
    if r.status_code != 200:
        raise Stop(f"Preview failed HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()["data"]
    checks = {
        "dry_run": data["dry_run"] is True and data["saved_count"] == 0,
        "mode": data["execution_mode"] == "verified_python_oracle",
        "reference": (data["reference_solution_id"], data["source_hash"]) == (REF_ID, REF_HASH),
        "count": data["generated_count"] == 15,
        "pairs_match_reviewed": [(c["input_data"], c["expected_output"])
                                 for c in data["test_cases"]] == REVIEWED_PAIRS,
    }
    print("preview checks:", checks)
    if not all(checks.values()):
        raise Stop("Preview differs from the reviewed results")


def do_save(s):
    db_preconditions()
    preview(s)
    if testcase_count(5):
        raise Stop("Test cases appeared during preview; not writing")
    print("sending save request ONCE (dry_run=false)...")
    try:
        r = s.post(URL, json={**BODY, "dry_run": False}, timeout=120)
        print("save response:", r.status_code,
              {k: v for k, v in r.json().get("data", {}).items()
               if k != "test_cases"} if r.headers.get("content-type", "").startswith(
                  "application/json") else r.text[:200])
    except requests.RequestException as error:
        print("AMBIGUOUS: request error", type(error).__name__,
              "- NOT retrying. Database state:")
    print("TestCase rows for assessment 5 now:", testcase_count(5))


def verify():
    rows = query("""SELECT test_number, test_name, category, visibility, input_data,
                           expected_output, CAST(marks AS varchar(20))
                    FROM dbo.TestCase WHERE assessment_id = 5 ORDER BY test_number""")
    pairs = [(r[4], r[5]) for r in rows]
    result = {
        "count_15": len(rows) == 15,
        "numbers_1_to_15": [r[0] for r in rows] == list(range(1, 16)),
        "pairs_match_reviewed": pairs == REVIEWED_PAIRS,
        "all_hidden": all(r[3] == "hidden" for r in rows),
        "equal_marks": {r[6] for r in rows} == {"0.33"},
        "inputs_unique": len({r[4] for r in rows}) == len(rows),
    }
    for r in rows:
        print(f"  {r[0]:>2} {r[2]:<17} {r[3]:<6} N={r[4]:<8} -> {r[5].strip():<3} marks={r[6]}")
    print("verification:", result)
    if not all(result.values()):
        raise Stop("Post-save verification failed")


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in ("preview", "save", "verify"):
        sys.exit("usage: save_assessment5_oracle_tests.py <token_file> preview|save|verify")
    try:
        if sys.argv[2] == "verify":
            verify()
            return
        s = session(sys.argv[1])
        if sys.argv[2] == "preview":
            db_preconditions()
            preview(s)
        else:
            do_save(s)
    except Stop as stop:
        sys.exit(f"STOPPED: {stop}")


if __name__ == "__main__":
    main()
