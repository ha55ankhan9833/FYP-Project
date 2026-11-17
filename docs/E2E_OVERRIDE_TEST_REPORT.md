# Criterion-override E2E report (O1–O4, FYP_E2E_Test)

Date: 2026-11-17. Scope: the owner-approved gates O1–O4, run in
**FYP_E2E_Test** only. FYP_Database was not written to (its full-row
fingerprint matches the baseline). M3, G4 and `cleanup-student` were not run.
No C++ was compiled or executed, and the production gate stayed disabled.

## Gates

| Gate | How | Outcome |
|---|---|---|
| O1 | `e2e_test_db.py add-student` (owner typed the password at a hidden prompt) | +1 Student_Export `E2E-TEST-0004`, +1 Enrollment_Export `E2E-ENR-0004`; no other table changed; owner logged in |
| O2 | identity checked with `/api/auth/me`; POST `/api/assessments/5/submissions` | 201; Submission 5 (SHA-256 = approved `prime_buggy_square`); exactly one GradingJob (4, PENDING); duplicate → 409 |
| O3 | `fixture_submission_grader.py` plan, then `--grade --confirm-job 4` | plan verified the fixture hash and recorded evidence; job 4 COMPLETED on attempt 1 (92 s, real Ollama, replay only) |
| O4 | BIIT235: POST `/api/submissions/5/evaluation/finalize` with `criteria` | 200, `criteria_approved: 2`; repeat → 409 |

## Grading of submission 5

| | AI recommendation (kept) | Teacher decision (published) |
|---|---|---|
| Test marks (Python, verified) | 4.00 / 5 (8/10; tests 6 and 9 failed) | 4.00 / 5 |
| Logic & Algorithm | 2.00 / 3 | **1.00 / 3** |
| Code Quality | 2.00 / 2 | **2.00 / 2** |
| Total | suggested 8.00, NEEDS_REVIEW | **final 7.00 / 10** |

- **Python verification:** all 7 deterministic checks passed.
- **Reliability flag:** `FAILURES_NOT_EXPLAINED`, kept for the teacher. The
  model named the failing inputs but not the `d * d < n` bug.
- **Reconciliation:** the API accepted the override only because
  4.00 + 1.00 + 2.00 = 7.00.

## Database state (FYP_E2E_Test)

| Table | Rows now | Change in O1–O4 |
|---|---|---|
| Student_Export / Enrollment_Export | 4 / 4 | +1 / +1 |
| Submission / GradingJob | 4 / 4 | +1 / +1 (job 4 COMPLETED, 1 attempt) |
| Evaluation | 4 | +1 (submission 5: suggested 8.00, final 7.00, FINALIZED) |
| EvaluationCriterion (AI) | 6 | +2 (submission 5: 2.00, 2.00; unchanged by finalization) |
| EvaluationCriterionApproval | 2 | +2 (Logic & Algorithm 1.00, Code Quality 2.00; approved_by BIIT235) |

Submissions 2, 3 and 4 are unchanged: their evaluations (10.00, 7.00, 0.00,
all FINALIZED), AI criterion rows and jobs are identical to before.

## Student view (E2E-TEST-0004)

- **Published:** RESULT_PUBLISHED; final 7.00/10; test marks 4.00/5;
  `rubric_source` TEACHER_APPROVED; Logic & Algorithm 1.00/3; Code Quality
  2.00/2; rubric 3.00/5; the teacher's feedback; and the line "8 of 10 hidden
  tests passed."
- **Absent:** the 8.00 suggestion, the AI marks, AI explanations, reliability
  flags, the verification report, provenance, failed test numbers, the
  approver, and any hidden inputs or expected outputs.
- **Refused (403):** the teacher evaluation, the submission list, the test
  cases and finalize.
- **Other students:** each still sees only their own unchanged result:

  | Student | Final | Breakdown source |
  |---|---|---|
  | E2E-TEST-0001 | 10.00 | AI_RECOMMENDATION_ACCEPTED_BY_TEACHER |
  | E2E-TEST-0002 | 7.00 | FINAL_TOTAL_ONLY |
  | E2E-TEST-0003 | 0.00 | FINAL_TOTAL_ONLY |

## Other verification

- **Tests:** 442/442 pass, and the mock compilation test passes.
- **FYP_Database:** its fingerprint matches the saved baseline, and the
  approval table does not exist there (M3 not applied).
- **Unchanged environment:** the gate is off, the trusted-execution approval
  file is empty, and there are no temporary build folders.

## Notes

- **Per-criterion comments:** `teacher_comment` is null for both approvals,
  because the driver sends marks only. The teacher's explanation is in
  `instructor_feedback`. The API already accepts a per-criterion `comment`.
- **Remaining approval gates:** M3 (approval table in FYP_Database), G4 (drop
  FYP_E2E_Test) and `cleanup-student --execute` each still need separate
  approval.
