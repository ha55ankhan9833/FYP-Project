"""
E2E driver: dynamic AI rubric + equal test-case marks, through the REAL API
on the FYP_E2E_Test backend (127.0.0.1:5000), using a NEW max/min assessment
with 50 total marks and a 60% test-case allocation (3.00 marks per case).

    python fixtures/run_dynamic_rubric_e2e.py <step> [--confirm FYP_E2E_Test]

Steps: draft, approve, assessment, allocation, suggest, approve-rubric,
reference, readiness, publish-guard, preview, generate, retry, verify; `all`
runs them in order. Write steps need --confirm FYP_E2E_Test.

Reuses the reviewed max/min steps of run_max_min_e2e.py (same problem,
schema, reference and verified oracle) with its own state file. The rubric
comes from the offline LLM, is EDITED by this script exactly as a teacher
would (rename one criterion, change marks, add one), and is then approved
explicitly; nothing the model produced is saved unreviewed. The teacher
token is read from dev_tools/.tokens/teacher.token and never printed.
Nothing is published.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_max_min_e2e as base  # noqa: E402

base.STATE = base.ROOT / "fixtures" / "dynamic_rubric_e2e" / "setup_state.json"
TOTAL_MARKS = 50
PERCENT = 60
EXPECTED = {"test_marks": "30.00", "rubric_marks": "20.00", "per_case_marks": "3.00"}
call, need, show, Stop, state, save_state = (base.call, base.need, base.show, base.Stop,
                                             base.state, base.save_state)


def step_assessment():
    if state().get("assessment_id"):
        print("assessment: already created", state()["assessment_id"])
        return
    body = {"course_code": "CSC-101", "assessment_type": "Assignment",
            "title": "Find Maximum and Minimum Using Loops (dynamic rubric)",
            "problem_statement": base.PROBLEM, "input_format": base.INPUT_FORMAT,
            "output_format": base.OUTPUT_FORMAT, "total_marks": TOTAL_MARKS,
            "deadline_at": "2026-12-31T23:59:00", "validation_percentage": 50,
            "allow_late": False, "late_penalty_marks": 0,
            "additional_constraints": "Use cin and cout.", "draft_id": need("draft_id")}
    bad = {**body, "validation_percentage": "33.33"}
    status, data = call("POST", "/api/assessments", bad)
    show("assessment with 33.33% (expected 400)", status, data)
    if status != 400 or data.get("code") != "MARKS_ALLOCATION_INVALID":
        raise Stop("inexact allocation was not refused")
    status, data = call("POST", "/api/assessments", body)
    show("assessment", status, data, ("assessment_id",))
    if status != 201:
        raise Stop("assessment creation failed")
    save_state(assessment_id=data["assessment_id"])


def step_allocation():
    for pct, expect in ((PERCENT, 200), ("33.33", 422), ("0.5", 422)):
        status, data = call("GET", f"/api/marks-allocation?total_marks={TOTAL_MARKS}"
                                   f"&validation_percentage={pct}")
        print(f"allocation {TOTAL_MARKS} @ {pct}%: HTTP {status} "
              f"{data.get('allocation') or {k: data.get(k) for k in ('code', 'details')}}")
        if status != expect:
            raise Stop("unexpected allocation response")


def step_suggest():
    aid = need("assessment_id")
    status, data = call("POST", f"/api/assessments/{aid}/rubric/suggest",
                        {"validation_percentage": PERCENT})
    s = data.get("suggestion", {})
    show("suggest", status, data)
    print(f"  status={s.get('status')} approved={s.get('approved')} "
          f"allocation={ {k: s.get('allocation', {}).get(k) for k in EXPECTED} }")
    for c in s.get("criteria", []):
        print(f"  {c['suggested_marks']:>6} (model {c['model_marks']}) {c['criterion_name']} "
              f"-- {c['description']}")
    for w in s.get("warnings", []):
        print("  warning:", w["code"], "-", w["message"])
    if status != 200:
        raise Stop("suggestion request failed")
    save_state(suggestion=s)


def teacher_edit(suggestion, rubric_marks):
    """What a teacher does on the review screen: keep, rename, re-mark, add."""
    criteria = [{"criterion_name": c["criterion_name"], "description": c["description"],
                 "max_marks": c["suggested_marks"]} for c in suggestion.get("criteria", [])]
    if not criteria:          # LLM unavailable: the teacher authors the rubric
        criteria = [{"criterion_name": "Correct for-loop use", "description": None,
                     "max_marks": "0"}]
    criteria[0]["criterion_name"] = "Correct for-loop use (teacher edited)"
    criteria.append({"criterion_name": "Maximum/minimum tracking logic",
                     "description": "Initialises max and min from the first value and "
                                    "updates both inside the loop", "max_marks": "0"})
    # Re-mark: 4.00 for the added logic criterion, the rest split evenly in cents.
    remaining = Decimal(rubric_marks) - Decimal("4.00")
    others = criteria[:-1]
    cents, extra = divmod(int(remaining * 100), len(others))
    for i, c in enumerate(others):
        c["max_marks"] = str(Decimal(cents + (1 if i < extra else 0)) / 100)
    criteria[-1]["max_marks"] = "4.00"
    return criteria


def step_approve_rubric():
    aid = need("assessment_id")
    criteria = teacher_edit(need("suggestion"), EXPECTED["rubric_marks"])
    wrong = [dict(c) for c in criteria]
    wrong[-1]["max_marks"] = "4.01"
    url = f"/api/assessments/{aid}/rubric/approve"
    status, data = call("POST", url, {"validation_percentage": PERCENT, "criteria": wrong})
    show("approve with wrong total (expected 422)", status, data)
    if status != 422 or data.get("code") != "RUBRIC_TOTAL_MISMATCH":
        raise Stop("wrong rubric total was not refused")
    for attempt in ("approve", "approve retry"):
        status, data = call("POST", url, {"validation_percentage": PERCENT, "criteria": criteria})
        r = data.get("rubric", {})
        show(attempt, status, data, ())
        print(f"  unchanged={r.get('unchanged')} saved={r.get('criteria_saved')} "
              f"allocation={ {k: r.get('allocation', {}).get(k) for k in EXPECTED} }")
        if status != 200:
            raise Stop("rubric approval failed")
    status, data = call("GET", f"/api/assessments/{aid}/rubric")
    rubric = data.get("rubric", {})
    print(f"rubric now: complete={rubric.get('rubric_complete')} "
          f"rubric_marks={rubric.get('rubric_marks_allocated')} "
          f"criteria={[(c['criterion_name'], c['max_marks']) for c in rubric.get('criteria', [])]}")
    save_state(approved_criteria=criteria)


def step_verify():
    base.step_verify()
    aid = need("assessment_id")
    status, data = call("GET", f"/api/assessments/{aid}/test-cases")
    marks = [Decimal(str(c["marks"])) for c in data.get("test_cases", [])]
    print(f"equal marks: {sorted(set(marks))} sum={sum(marks)} "
          f"(expected {EXPECTED['per_case_marks']} x 10 = {EXPECTED['test_marks']})")
    status, data = call("POST", f"/api/assessments/{aid}/rubric/approve",
                        {"validation_percentage": 70, "criteria": need("approved_criteria")})
    show("change % after save (expected 409)", status, data)


base.STEPS = {"draft": base.step_draft, "approve": base.step_approve,
              "assessment": step_assessment, "allocation": step_allocation,
              "suggest": step_suggest, "approve-rubric": step_approve_rubric,
              "reference": base.step_reference, "readiness": base.step_readiness,
              "publish-guard": base.step_publish_guard, "preview": base.step_preview,
              "generate": base.step_generate, "retry": base.step_retry, "verify": step_verify}
base.WRITE_STEPS = {"draft", "approve", "assessment", "approve-rubric", "reference",
                    "generate", "retry", "verify"}
base.__doc__ = __doc__

if __name__ == "__main__":
    base.main()
