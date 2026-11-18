"""
Builds the reviewable API payloads for the structured-input E2E fixture
(count-prefixed integer array -> sum). Writes JSON files only.
No database access, no HTTP calls, no C++ compilation or execution.

    python fixtures/build_structured_array_sum_payloads.py

The expected outputs written to planned_test_cases.json come from an
independent Python oracle (sum of the parsed integers). They are used only
to CROSS-CHECK what the C++ reference produces later (after approvals S2-S4);
they are never saved as test cases by themselves.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from input_schema import check_schema, validate_input  # noqa: E402
from test_case_builder import select_structured_inputs  # noqa: E402

OUT = ROOT / "fixtures" / "structured_array_sum"
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "array_sum_reference.cpp"

N_MAX = 100
A_LIMIT = 10 ** 9

# Teacher-approved structured input schema (input_schema v1).
INPUT_SCHEMA = {
    "version": 1,
    "variables": {"N": {"min": 1, "max": N_MAX}},
    "items": [
        {"type": "ints", "vars": ["N"]},
        {"type": "array", "name": "A", "length": "N",
         "min": -A_LIMIT, "max": A_LIMIT},
    ],
    "relations": [],
}

# Human-readable value rule the teacher adds to the draft (the structured
# schema above is what generation and validation actually use).
VALUE_RULE = f"1 <= N <= {N_MAX}"

ASSESSMENT = {
    "course_code": "CSC-101",
    "assessment_type": "Quiz",
    "title": "Array Sum (Structured E2E)",
    "problem_statement": (
        "Write a C++17 program that reads an integer N followed by N "
        "integers and prints their sum."
    ),
    "input_format": (
        "The first line contains one integer N (1 <= N <= 100). The second "
        "line contains N integers A1 ... AN separated by single spaces "
        "(-1000000000 <= Ai <= 1000000000)."
    ),
    "output_format": (
        "Print the sum A1 + A2 + ... + AN as a decimal integer followed by "
        "a newline. Print nothing else."
    ),
    "total_marks": 10,
    "validation_percentage": 50,
    "deadline_at": "2026-12-20T23:59:00",
    "allow_late": False,
    "late_penalty_marks": 0,
    "additional_constraints": (
        "Use C++17. Read from standard input and write to standard output. "
        "The sum can exceed the 32-bit integer range."
    ),
    "draft_id": "<DRAFT_ID returned by POST /api/ai/drafts, after finalize>",
}

# Exact input text: each line ends with '\n'. Chosen to catch typical bugs:
# the statement sample, a single negative element, int32 overflow, a sum
# that cancels to zero, and an all-negative array. The remaining 5 cases
# are filled by the deterministic baseline generator (seed 42).
ADDITIONAL_INPUTS = [
    "3\n10 20 30\n",
    "1\n-1000000000\n",
    "5\n1000000000 1000000000 1000000000 1000000000 1000000000\n",
    "4\n-5 5 -7 7\n",
    "6\n-1 -2 -3 -4 -5 -6\n",
]


def oracle(text):
    """Independent Python oracle: parse N, then exactly N integers; sum them."""
    lines = text.split("\n")
    n = int(lines[0])
    values = [int(token) for token in lines[1].split()]
    if not 1 <= n <= N_MAX or len(values) != n \
            or any(not -A_LIMIT <= v <= A_LIMIT for v in values):
        raise ValueError("input outside the documented grammar")
    return f"{sum(values)}\n"


def reference_source():
    raw = REFERENCE.read_bytes()
    if b"\r" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise SystemExit("reference must be LF-only UTF-8 without BOM")
    return raw.decode("utf-8")


def plan():
    checked = check_schema(INPUT_SCHEMA)
    planned = select_structured_inputs(checked, ADDITIONAL_INPUTS)
    cases = []
    for number, item in enumerate(planned, start=1):
        validation = validate_input(checked, item["input_data"])
        if not validation["valid"]:
            raise SystemExit(f"planned input {number} is invalid: {validation['errors']}")
        cases.append({"test_number": number, "test_name": item["test_name"],
                      "category": item["category"], "input_data": item["input_data"],
                      "expected_output_python_oracle": oracle(item["input_data"])})
    return cases


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = reference_source()
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    cases = plan()

    def request(method, path, body, notes=None):
        item = {"method": method, "path": path, "body": body}
        if notes:
            item["notes"] = notes
        return item

    files = {
        "01_draft_create.json": request(
            "POST", "/api/ai/drafts",
            {"problem": ASSESSMENT["problem_statement"],
             "input_format": ASSESSMENT["input_format"],
             "output_format": ASSESSMENT["output_format"]},
            "Teacher BIIT235 JWT; FYP_E2E_Test backend only. Requires "
            "ai_local_api.py on 127.0.0.1:5001. Every AI suggestion is then "
            "rejected and only VALUE_RULE is added by the teacher."),
        "02_input_schema.json": request(
            "PUT", "/api/ai/drafts/<draft_id>/input-schema",
            {"input_schema": INPUT_SCHEMA},
            "Must be sent BEFORE approving the value category; locked after."),
        "02b_value_rule.json": {"value_rule": VALUE_RULE},
        "03_assessment_create.json": request(
            "POST", "/api/assessments", ASSESSMENT,
            "Replace draft_id with the finalized draft."),
        "04_reference_solution.json": request(
            "POST", "/api/assessments/<new_id>/reference-solution",
            {"source_code": source},
            "Saved as PENDING; nothing is compiled or executed."),
        "05_reference_approve.json": request(
            "POST", "/api/assessments/<new_id>/reference-solution/approve",
            {"source_hash": source_hash},
            "Teacher confirms the reviewed bytes. Approval does NOT run code."),
        "06_generate_test_cases.json": request(
            "POST", "/api/assessments/<new_id>/generate-test-cases",
            {"additional_inputs": ADDITIONAL_INPUTS},
            "Without a sandbox or allow-listed hash this returns HTTP 409 "
            "EXPECTED_OUTPUT_UNVERIFIABLE and saves nothing."),
        "planned_test_cases.json": {
            "note": "Exact inputs the structured generator selects for this "
                    "schema; expected outputs are from an independent Python "
                    "oracle, used only to cross-check the C++ reference.",
            "reference_sha256": source_hash,
            "cases": cases},
    }
    for name, payload in files.items():
        (OUT / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("reference sha256:", source_hash)
    print("planned tests:", len(cases))
    for c in cases:
        shown = c["input_data"].replace("\n", "\\n")
        if len(shown) > 60:
            shown = shown[:57] + "..."
        print(f"  {c['test_number']:>2} {c['category']:<22} {shown:<62} "
              f"-> {c['expected_output_python_oracle'].strip()}")
    print("written to", OUT)


if __name__ == "__main__":
    main()
