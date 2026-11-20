# Automatic Evaluation — Implementation Summary and A6 Demo Plan

Date: 2026-11-18. FYP_E2E_Test only.

**Status (2026-11-18): Gate L executed with owner approval; Gate V NOT approved.**
Gate L result: submission 7 (E2E-TEST-0002) / job 6 analysed by Qwen
(AI_ANALYSIS_UNVERIFIED, suggested 0.00 + 0.00, tests NOT_RUN); teacher
finalized 3.00/10 with criterion approvals 3 and 4 (1.50 + 1.50); student sees
CODE_REVIEWED_NOT_EXECUTED. Only those 7 rows changed; FYP_Database unchanged.
Driver steps: l-submit, l-report, l-finalize, l-verify.

## 1. What was built (minimal changes, existing components reused)

| Piece | File | Change |
|---|---|---|
| LLM analysis mode | `llm_analysis_grading.py` (new) | Claims a PENDING job, reuses the pipeline's loaders, static constraint checker, Qwen criterion grader and Python validation; saves `AI_ANALYSIS_UNVERIFIED`, tests_run 0, rubric-only suggested marks. Never compiles or runs anything; refuses if a real sandbox exists. |
| Structured report | `evaluation_report.py` (new) | One report for every mode: mode, evidence basis, constraints compliance, AI logic analysis, rubric feedback, test status (VERIFIED / NOT_RUN), suggested marks with basis, live teacher-approval status. Embedded as JSON in the teacher-only feedback; derived for older evaluations. |
| Verified mode report | `grading_pipeline.py` | Appends the same report when real execution evidence exists (sandbox or approved recorded fixture). Scoring unchanged. |
| Qwen instruction | `rubric_ai_grader.py` | For no-execution evidence: "no tests were run — do not cite tests or claim pass/fail". |
| Hooks | `grading_job_completion_service.py`, `evaluation_review_service.py`, `student_result_builder.py`, `grading_worker.py` | New status allowed; shown to teacher as needs-review; student sees "not run against the hidden tests", never 0/0 or test marks; teacher view gains `evaluation_report`; `grading_worker.py --llm-analysis` (opt-in). |
| A6 verified-mode policy | `dev_tools/trusted_execution_harness.py`, `recorded_evidence_grading.py`, `fixture_submission_grader.py` | Fixtures are bound to their own assessment (A5 fixtures unchanged; new A6 fixture `array_sum_int_accumulator`, **not approved**). |
| Dev runner | `dev_tools/run_e2e_evaluation_worker.py` (new) | FYP_E2E_Test only: `--dry-run` (real Qwen, read-only data, writes captured in memory), plan, or `--analyse --confirm-job`. |

Unchanged: secure execution gate (still None), production worker default
behaviour, scoring rules, mark totals, teacher finalize/override API, schema.

## 2. Measured live check (read-only, real Qwen 2.5-Coder 3B, A6 data)

Source: `dev_tools/trusted_fixtures/array_sum_int_accumulator.cpp`
(sha256 `ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4`,
deliberate `int` accumulator). 45 s on CPU. Both databases byte-identical before/after.

- Mode LLM_ANALYSIS, tests NOT_RUN (10 configured), no test marks suggested.
- Qwen correctly found the bug: "uses a 32-bit int ... can overflow ... use long long".
- Suggested rubric marks 0.00/3 (Logic) and 0.00/2 (Code Quality, "not readable" —
  too harsh for this simple code). Teacher review is required.
- First run (before the instruction fix) the model invented "test 1 failing";
  the consistency checker flagged it. Now the report drops any such citation and
  the model is told no tests ran; the second run cited none.

## 3. Demo writes awaiting approval (FYP_E2E_Test only)

### Gate L — LLM analysis mode (no execution; no hash approval needed)
| Step | Action | Rows |
|---|---|---|
| L1 | E2E-TEST-0002 submits `array_sum_int_accumulator.cpp` to A6 (student API) | +1 Submission, +1 GradingJob (PENDING) |
| L2 | `run_e2e_evaluation_worker.py --submission-id <id> --analyse --confirm-job <job>` | +1 Evaluation (AI_ANALYSIS_UNVERIFIED), +2 EvaluationCriterion, job COMPLETED |
| L3 | Teacher reads `GET /api/submissions/<id>/evaluation` (report) and finalizes with criterion marks, e.g. Logic 1.50 + Quality 2.00 = 3.50 (test marks cannot be added through criteria because tests are unverified; a plain `final_score` override remains available) | Evaluation FINALIZED, +2 EvaluationCriterionApproval |
| L4 | Student E2E-TEST-0002 views result; other students checked; DB diff | read-only |

### Gate V — verified execution mode (optional; needs owner hash approval)
| Step | Action |
|---|---|
| V1 | Owner approves sha256 `ac95167f…d0e6f4` in `dev_tools/trusted_fixtures/approved_for_execution.json` |
| V2 | `trusted_execution_harness.py --fixture array_sum_int_accumulator --execute`: compiles and runs this reviewed developer file ONCE on the host (2 s timeout per test, minimal environment) against A6's 10 tests (read-only); writes a report to `dev_tools/reports/` |
| V3 | Review the recorded report; add its hashes to `REVIEWED_RECORDS` (code change) |
| V4 | E2E-TEST-0003 submits the same bytes; `fixture_submission_grader.py --submission-id <id> --grade --confirm-job <job>` replays the recorded evidence through the normal pipeline → real pass/fail, equal-weight test marks, Qwen rubric with failing-test evidence, VERIFIED report |
| V5 | Teacher finalizes; student views result |

Student code is never executed: V2 runs only the reviewed developer fixture,
whose exact bytes the student then submits (same policy as Assessment 5).
