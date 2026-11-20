"""
Builds the reviewable API payloads for the proposed CSC-101 assessment.
Writes JSON files only. No database access, no HTTP calls, no C++ run.

    python fixtures/build_new_assessment_payloads.py
"""

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generic_test_inputs import generate_inputs, parse_approved_value_constraints  # noqa: E402
from grading_allocation import split_allocation  # noqa: E402
from assessment_rubric_service import normalize_rubric_input  # noqa: E402
from test_case_builder import select_inputs  # noqa: E402

OUT = ROOT / "fixtures" / "new_assessment_csc101"
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "prime_check_reference.cpp"

VALUE_CONSTRAINT = "1 <= N <= 1000000"

ASSESSMENT = {
    "course_code": "CSC-101",
    "assessment_type": "Quiz",
    "title": "Prime Number Check",
    "problem_statement": (
        "Write a C++17 program that reads one integer N and decides "
        "whether N is a prime number. A prime number is an integer "
        "greater than 1 whose only positive divisors are 1 and itself "
        "(so 1 is NOT prime and 2 is the smallest prime). "
        "Print YES if N is prime, otherwise print NO."
    ),
    "input_format": "A single line containing one integer N.",
    "output_format": (
        "Print exactly YES if N is prime, otherwise print exactly NO, "
        "followed by a newline. Output is case-sensitive; print nothing else."
    ),
    "total_marks": 10,
    "validation_percentage": 50,
    "deadline_at": "2026-12-15T23:59:00",
    "allow_late": False,
    "late_penalty_marks": 0,
    "additional_constraints": (
        "Use C++17. Read from standard input and write to standard output. "
        "Your program must finish within 2 seconds for every valid N."
    ),
    "draft_id": "<DRAFT_ID returned by POST /api/ai/drafts, after finalize>"
}

RUBRIC = {"criteria": [
    {"criterion_name": "Logic & Algorithm", "max_marks": 3,
     "description": "Correct primality logic including N = 1 and N = 2; "
                    "efficient divisor search (e.g. up to sqrt(N)); "
                    "no incorrect early exits."},
    {"criterion_name": "Code Quality", "max_marks": 2,
     "description": "Readable structure and naming, appropriate types "
                    "(no overflow), no unnecessary code, follows the "
                    "required output format exactly."}
]}

ASSIGNMENT = {"section_ids": ["SEC000001"]}

# Random inputs in [1, 10^6] are almost all composite, so the teacher
# adds targeted cases: small primes, smallest composite, a prime square
# (catches `d * d < n` off-by-one), and the largest prime <= 10^6.
# Exactly 10 test cases (test_case_policy): the 5 boundary/edge cases the
# generator always produces + these 5 = 10, so no random fill is needed.
GENERATION = {
    "additional_inputs": ["4", "97", "7919", "994009", "999983"]
}


def is_prime(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d += 1
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = REFERENCE.read_bytes().decode("utf-8")
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    test_alloc, rubric_alloc = split_allocation(
        ASSESSMENT["total_marks"], ASSESSMENT["validation_percentage"])
    normalize_rubric_input(RUBRIC["criteria"], rubric_alloc)   # raises if invalid

    variables = parse_approved_value_constraints(VALUE_CONSTRAINT)
    planned = select_inputs(variables, GENERATION["additional_inputs"])
    oracle = [{
        "test_number": i,
        "test_name": c["test_name"],
        "category": c["category"],
        "input_data": c["input_data"],
        "expected_output_python_oracle": ("YES" if is_prime(int(c["input_data"])) else "NO") + "\n"
    } for i, c in enumerate(planned, start=1)]

    # Each request file is {"method", "path", "body"[, "notes"]}.
    # ONLY "body" is sent; several endpoints reject unknown fields.
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
            "Teacher BIIT235 JWT. Requires ai_local_api.py running on "
            "127.0.0.1:5001. Response contains draft.draft_id and the AI "
            "suggestions with their ids."),
        "02_draft_review_steps.json": {
            "notes": "Replace <draft_id>/<id> with values from step 01. "
                     "Send each body in order.",
            "requests": [
                request("POST", "/api/ai/drafts/<draft_id>/actions",
                        {"action": "DELETE", "category": "value",
                         "constraint_id": "<id>"},
                        "Repeat for EVERY AI value suggestion (even if it "
                        "looks right) so only the teacher-added rule remains."),
                request("POST", "/api/ai/drafts/<draft_id>/actions",
                        {"action": "ADD", "category": "value",
                         "text": VALUE_CONSTRAINT}),
                request("POST", "/api/ai/drafts/<draft_id>/actions",
                        {"action": "DELETE", "category": "code",
                         "constraint_id": "<id>"},
                        "Repeat for EVERY AI code suggestion, if any."),
                request("POST", "/api/ai/drafts/<draft_id>/approve",
                        {"category": "value"}),
                request("POST", "/api/ai/drafts/<draft_id>/approve",
                        {"category": "code"}),
                request("POST", "/api/ai/drafts/<draft_id>/finalize", {}),
                request("GET", "/api/ai/drafts/<draft_id>", None,
                        "Verify: finalized=true, approval value/code true, "
                        "exactly one ACCEPTED constraint with text "
                        f"'{VALUE_CONSTRAINT}', everything else REJECTED."),
            ],
            "result_required": {"value_constraints": VALUE_CONSTRAINT,
                                "code_constraints": ""}
        },
        "03_assessment_create.json": request(
            "POST", "/api/assessments", ASSESSMENT,
            "Replace draft_id. Response: assessment_id (= <new_id> below)."),
        "04_rubric.json": request(
            "PUT", "/api/assessments/<new_id>/rubric", RUBRIC),
        "05_reference_solution.json": request(
            "POST", "/api/assessments/<new_id>/reference-solution",
            {"source_code": source},
            "Saved as PENDING; nothing is compiled or executed."),
        "06_reference_approve.json": request(
            "POST", "/api/assessments/<new_id>/reference-solution/approve",
            {"source_hash": source_hash},
            "Teacher confirms the reviewed bytes. Approval does NOT run code."),
        "07_generate_test_cases.json": request(
            "POST", "/api/assessments/<new_id>/generate-test-cases",
            GENERATION,
            "Executes the approved reference to produce expected outputs. "
            "Refused unless the owner adds this exact source_hash to "
            "trusted_reference_hashes.json (host) or a secure sandbox exists."),
        "08_section_assignment.json": request(
            "POST", "/api/assessments/<new_id>/sections", ASSIGNMENT),
        "planned_test_inputs.json": {
            "note": "Inputs the generic generator will produce for the approved "
                    "constraint; expected outputs here are from an independent "
                    "Python oracle, used to cross-check the C++ reference.",
            "test_marks_allocated": str(test_alloc),
            "rubric_marks_allocated": str(rubric_alloc),
            "marks_per_test_display": str((test_alloc / Decimal(len(oracle))).quantize(Decimal("0.01"))),
            "cases": oracle}
    }

    for name, payload in files.items():
        (OUT / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("reference sha256:", source_hash)
    print("allocation: tests", test_alloc, "rubric", rubric_alloc)
    print("planned tests:", len(oracle))
    for c in oracle:
        print(f"  {c['test_number']:>2} {c['category']:<14} N={c['input_data']:<8} -> {c['expected_output_python_oracle'].strip()}")
    print("written to", OUT)


if __name__ == "__main__":
    main()
