# G3 end-to-end integration report (FYP_E2E_Test)

Date: 2026-11-17. Scope: the controlled end-to-end test approved by the owner,
run in the isolated **FYP_E2E_Test** database only. FYP_Database was not
written to (its full-row fingerprint matches the pre-test baseline). No C++
was compiled or executed. The production execution gate stayed disabled.

## Flow and outcomes

| Step | How | Outcome |
|---|---|---|
| Test backend | `dev_tools/run_e2e_backend.py` | preflight: DB = FYP_E2E_Test, 0 real students, gate off; 127.0.0.1:5000 only; all Python sessions on FYP_E2E_Test |
| Logins | `fixtures/teacher_login.py`, `fixtures/student_login.py` (owner typed the credentials) | BIIT235 and E2E-TEST-0001..0003; each token checked against `/api/auth/me` before every step |
| Assign | POST `/api/assessments/5/sections` | 201; AssessmentSection 2 = (5, SEC000001); 3 enrolled students |
| Student view | GET `/api/student/assessments` | Assessment 5 open for all 3; no hidden data |
| Submit | POST `/api/assessments/5/submissions` | 201 ×3; submissions 2/3/4 + jobs 1/2/3 created atomically; stored source SHA-256 = approved fixture hash |
| Duplicate | same POST again | 409 "Student already has a submission"; no rows added |
| Plan | `fixture_submission_grader.py --submission-id N` | all 3 passed (hash, recorded report, 10 tests, expected outcome) |
| Grade | `... --grade --confirm-job J` | each job claimed once and COMPLETED on attempt 1; replay only (no compile or run) |
| Teacher review | GET `/api/assessments/5/submissions`, `/api/submissions/N/evaluation` | suggested marks, warnings and the teacher-only verification report visible to the teacher |
| Finalize | POST `/api/submissions/N/evaluation/finalize` | 200 ×3 (REVIEWED_AUTOMATIC_GRADE); repeat refused 409 |
| Student results | GET `/api/student/assessments/5/submission` | each student sees only their own finalized result |

The first finalize attempt was refused by the driver before any request was
sent, because the 2-hour JWTs had expired. After re-login it succeeded.

## Grading results

| Submission | Student | Fixture | Tests | Test marks | AI criteria (Logic / Quality) | Suggested | Status | **Final** |
|---|---|---|---|---|---|---|---|---|
| 2 | E2E-TEST-0001 | prime_correct | 10/10 | 5.00 | 3.00 / 2.00 | 10.00 | COMPLETED | **10.00** |
| 3 | E2E-TEST-0002 | prime_buggy_square | 8/10 (failed 6, 9) | 4.00 | 2.00 / 2.00 | 8.00 | NEEDS_REVIEW | **7.00** |
| 4 | E2E-TEST-0003 | prime_compile_error | 0/0 | 0 | none (no Ollama call) | 0.00 | COMPILATION_FAILED | **0.00** |

**Ollama:** `qwen2.5-coder:3b`, about 93–97 s per graded submission. The
compilation failure took about 1 s and made no Ollama call.

**Python verification (verification_v1):** all 7 deterministic checks passed
for submissions 2 and 3, and no marks were adjusted.

**Submission 3:** flagged `FAILURES_NOT_EXPLAINED`. The model named both
failures correctly but did not identify the `d * d < n` bug. The teacher
corrected Logic & Algorithm to 1.00/3.

**Overrides:** the finalize API supports only a final-total override
(`final_score` and `instructor_feedback`; other fields are rejected). The
correction was therefore recorded in `instructor_feedback`, and the AI
criterion rows were kept unchanged as the original recommendation.

## Final FYP_E2E_Test grading state

| Table | Rows |
|---|---|
| AssessmentSection | 1 |
| Submission | 3 |
| GradingJob | 3, all COMPLETED |
| Evaluation | 3, all FINALIZED (10.00, 7.00, 0.00) |
| EvaluationCriterion | 4 (AI recommendations, unchanged) |
| Notification | 0 |

## Security verification

- **Writes:** only in FYP_E2E_Test. The FYP_Database full-row fingerprint
  (16 protected tables) and its row-count snapshot are both unchanged.
- **Students:** each sees only their own submission. A student token gets 403
  on:
  - the teacher submission list;
  - every `/api/submissions/N/evaluation` (including their own);
  - finalize;
  - `/api/assessments/5/test-cases`;
  - `/api/grading/status`.
- **Hidden data:** no hidden inputs of 3 or more characters, no expected
  outputs, no other students' records, and no teacher-only verification
  section appear in any student response.
- **Unchanged infrastructure:** the gate is off, the trusted-execution approval
  file is empty, and there are no temporary build folders.
- **Regression:** 421/421 tests pass, and the compilation mock test passes.

## Findings (not fixed; for owner decision)

1. **Internal review text is shown to students after finalization.**
   - `student_safe_feedback` removes only the teacher-only verification
     section. The AI section still contains "Suggested total: … (not final…)"
     and, for submission 3, the "Reliability flags (manual review required)"
     block.
   - This contradicts the service rule that suggested scores are never shown
     to students.
2. **AI criterion marks contradict a teacher override.** Students still see the
   AI's criterion marks (Logic 2.00 for submission 3) next to the teacher's
   final 7.00. With no criterion-level override API, the published criteria
   don't match the final mark.
3. **Over-redaction.** Short hidden inputs (1, 2) are replaced wherever they
   appear in the AI text, for example "iterating from [hidden test input]".
   This is safe but makes the text harder to read.

## Not done (by instruction)

- G4 (drop FYP_E2E_Test) was not run, and the test data is kept.
- Frontend development has not started.
