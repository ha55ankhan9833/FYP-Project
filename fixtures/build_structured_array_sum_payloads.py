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


# Owner-approved demo setup (W1/W2, docs/A6_DEMO_SETUP_PROPOSAL.md).
# total 10 at 50 % validation -> tests 5.00 (10 x 0.50) + rubric 5.00.
RUBRIC_CRITERIA = [
    {"criterion_name": "Logic & Correctness", "max_marks": "3.00",
     "description": (
         "Reads exactly N values and sums them with a 64-bit type (long long) "
         "so sums up to 100 x 1e9 do not overflow; handles negative values "
         "and N = 1.")},
    {"criterion_name": "Code Quality", "max_marks": "2.00",
     "description": (
         "Readable structure and naming, appropriate types, no unnecessary "
         "work; reads standard input and prints only the sum.")},
]
SECTION_ID = "SEC000001"

# Owner-approved W3: one synthetic submission (E2E-TEST-0001), never
# compiled or run, graded through the existing manual-grade fallback.
# Score policy (owner-approved option 1, conservative): the test-case
# portion is NOT run and NOT awarded; only rubric marks from code review.
W3_STUDENT = "E2E-TEST-0001"
W3_SUBMISSION_FILE = "w3_synthetic_submission.cpp"
W3_MANUAL_GRADE = {
    "final_score": "5.00",
    "confirm_manual_grade": True,
    "justification": (
        "MANUAL / NOT AUTO-EXECUTED (W3 E2E demo). Secure student execution is "
        "disabled, so the 10 hidden test cases were NOT compiled or run; the "
        "test-case portion (5.00) is not awarded and nothing is claimed about "
        "test results. Rubric by code review only: Logic & Correctness "
        "3.00/3.00 (reads N, then N values into a long long accumulator; "
        "handles negative values and N = 1); Code Quality 2.00/2.00 (clear "
        "names, prints only the sum). Final 0.00 + 3.00 + 2.00 = 5.00 of 10.00. "
        "Breakdown is recorded as text only (no per-criterion approval rows)."),
    "instructor_feedback": (
        "MANUAL GRADE - NOT AUTO-EXECUTED. Your program was not compiled or run "
        "because automatic testing is currently unavailable, so the test-case "
        "portion was not awarded. Rubric marks from code review: Logic & "
        "Correctness 3.00 of 3.00, Code Quality 2.00 of 2.00. Final mark: "
        "5.00 of 10.00."),
}


# Owner-approved Gate L (automatic LLM analysis demo, no execution).
# The submitted bytes are the reviewed fixture
# dev_tools/trusted_fixtures/array_sum_int_accumulator.cpp (int accumulator).
GATE_L_STUDENT = "E2E-TEST-0002"
GATE_L_FIXTURE = "array_sum_int_accumulator"
# Teacher review, fixed BEFORE reading the model output (deterministic):
# - Logic & Correctness 1.50/3.00: reads N and exactly N values, loop and
#   negative values are handled, but the int accumulator overflows for sums
#   beyond the 32-bit range, which the statement explicitly warns about.
# - Code Quality 2.00 -> 1.50/2.00: short, readable, sensible names; the
#   rubric's "appropriate types" point is lost for the int accumulator.
# - Test-case portion (5.00): NOT RUN, not awarded; final = criteria only.
GATE_L_FINALIZE = {
    "final_score": "3.00",
    "criteria": [
        {"criterion_name": "Logic & Correctness", "marks": "1.50",
         "comment": "Reads input correctly; the int accumulator overflows for "
                    "sums beyond the 32-bit range, which the statement warns about."},
        {"criterion_name": "Code Quality", "marks": "1.50",
         "comment": "Clear and readable; use a 64-bit type (long long) for the sum."},
    ],
    "instructor_feedback": (
        "AUTOMATIC CODE REVIEW - NOT EXECUTED. No test cases were run on your "
        "program, so the test-case portion was not awarded. Marks come from an "
        "AI code review that your teacher checked and adjusted: Logic & "
        "Correctness 1.50 of 3.00, Code Quality 1.50 of 2.00. Main issue: the "
        "sum is stored in an int and can overflow; use long long."),
}


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
        "07_rubric.json": request(
            "PUT", "/api/assessments/<new_id>/rubric",
            {"criteria": RUBRIC_CRITERIA},
            "W1. Must precede section assignment (students cannot submit "
            "while the 5.00 rubric allocation is undefined)."),
        "08_section_assignment.json": request(
            "POST", "/api/assessments/<new_id>/sections",
            {"section_ids": [SECTION_ID]},
            "W2. FYP_E2E_Test only: SEC000001 holds only the E2E test students."),
        "09_w3_submission.json": request(
            "POST", "/api/assessments/<new_id>/submissions",
            {"file_name": W3_SUBMISSION_FILE,
             "source_code": (OUT / W3_SUBMISSION_FILE).read_bytes().decode("utf-8")},
            f"W3. Student {W3_STUDENT} token only. Creates a Submission and a "
            "PENDING GradingJob; the source is never compiled or executed."),
        "10_w3_manual_grade.json": request(
            "POST", "/api/submissions/<submission_id>/evaluation/manual-grade",
            W3_MANUAL_GRADE,
            "W3. Teacher BIIT235. Existing manual-grade fallback: finalizes in "
            "one call (MANUALLY_GRADED, tests_run 0). Do NOT call /finalize after it."),
        "11_gate_l_finalize.json": request(
            "POST", "/api/submissions/<submission_id>/evaluation/finalize",
            GATE_L_FINALIZE,
            "Gate L. Teacher BIIT235 approves the AI_ANALYSIS_UNVERIFIED "
            "evaluation with criterion marks; verified test marks are 0 (not "
            "run), so final_score must equal the criterion total."),
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
