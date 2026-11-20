"""
E2E driver: max/min WITHOUT a teacher reference solution, through the REAL
API on the FYP_E2E_Test backend (127.0.0.1:5000).

    python fixtures/run_reference_free_e2e.py <step> [--confirm FYP_E2E_Test]

Steps: draft, approve, assessment, suggest, approve-rubric, readiness,
publish-guard, preview, generate, retry, verify; `all` runs them in order.
Write steps need --confirm FYP_E2E_Test.

Same reviewed problem, schema and rubric flow as run_dynamic_rubric_e2e.py
(50 marks, 60% tests -> 3.00 per case), but NO reference-solution step:
expected outputs come from the max_min_v1 oracle selected through the
owner-reviewed problem spec (verified_problem_specs.json), via the single
endpoint POST /api/assessments/<id>/generate-test-cases/verified.
Nothing is published. The token is never printed.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dynamic_rubric_e2e as dyn  # noqa: E402

base = dyn.base
base.STATE = base.ROOT / "fixtures" / "reference_free_e2e" / "setup_state.json"
call, need, show, Stop, state = base.call, base.need, base.show, base.Stop, base.state
VERIFIED = "/api/assessments/{aid}/generate-test-cases/verified"


def step_readiness():
    aid = need("assessment_id")
    status, data = call("GET", f"/api/assessments/{aid}/generation-readiness")
    d = data.get("data", {})
    checks = d.get("checks", {})
    print(f"readiness: HTTP {status} generate={d.get('ready_to_generate')} "
          f"missing={[m['code'] for m in d.get('missing_for_generation', [])]} "
          f"publish={d.get('ready_to_publish')} "
          f"{[m['code'] for m in d.get('missing_for_publish', [])]}")
    print("  verification:", d.get("verification"))
    print("  output source:", checks.get("expected_output_source"))
    print("  reference_solution:", checks.get("reference_solution"))
    return d


def step_preview():
    aid = need("assessment_id")
    status, data = call("POST", VERIFIED.format(aid=aid),
                        {"additional_inputs": base.TEACHER_CASES, "dry_run": True})
    d = data.get("data", {})
    show("verified preview", status, data)
    print("  ", {k: d.get(k) for k in ("output_source", "reference_required",
                                       "reference_solution_id", "oracle_id", "spec_id",
                                       "generated_count", "saved_count", "input_format")})
    cases = d.get("test_cases", [])
    print("  problems:", base.check_cases(cases) or "none")
    base.save_state(preview=[{"input_data": c["input_data"], "expected_output": c["expected_output"]}
                             for c in cases])
    if status != 200:
        raise Stop("preview failed")


def step_generate():
    aid = need("assessment_id")
    status, data = call("POST", VERIFIED.format(aid=aid),
                        {"additional_inputs": base.TEACHER_CASES, "dry_run": False})
    show("verified generate", status, data)
    print("  ", {k: data.get("data", {}).get(k) for k in
                 ("output_source", "reference_required", "spec_id", "saved_count",
                  "marks_per_test", "test_marks_allocated", "rubric_marks_allocated",
                  "weighting")})
    if status != 201:
        raise Stop("generation failed")


def step_retry():
    aid = need("assessment_id")
    status, data = call("POST", VERIFIED.format(aid=aid),
                        {"additional_inputs": base.TEACHER_CASES, "dry_run": False})
    show("verified generate retry", status, data)
    if status != 409:
        raise Stop("retry was not refused")


def step_verify():
    aid = need("assessment_id")
    status, data = call("GET", f"/api/assessments/{aid}/reference-solution")
    print(f"reference-solution GET: HTTP {status} {data.get('code', '')} {data.get('message', '')}")
    status, data = call("GET", f"/api/assessments/{aid}/test-cases")
    cases = data.get("test_cases", [])
    marks = [Decimal(str(c["marks"])) for c in cases]
    print(f"test-cases: HTTP {status} total={data.get('total')} "
          f"visibility={sorted({c['visibility'] for c in cases})} marks={sorted(set(marks))} "
          f"sum={sum(marks)}")
    print("  problems:", base.check_cases(cases) or "none")
    preview = state().get("preview")
    if preview:
        print("  identical to preview:", [{"input_data": c["input_data"],
                                           "expected_output": c["expected_output"]}
                                          for c in cases] == preview)
    step_readiness()


base.STEPS = {"draft": base.step_draft, "approve": base.step_approve,
              "assessment": dyn.step_assessment, "suggest": dyn.step_suggest,
              "approve-rubric": dyn.step_approve_rubric, "readiness": step_readiness,
              "publish-guard": base.step_publish_guard, "preview": step_preview,
              "generate": step_generate, "retry": step_retry, "verify": step_verify}
base.WRITE_STEPS = {"draft", "approve", "assessment", "approve-rubric", "generate", "retry"}
base.__doc__ = __doc__

if __name__ == "__main__":
    base.main()
