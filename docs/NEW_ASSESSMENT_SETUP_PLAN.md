# CSC-101 "Prime Number Check" — setup plan (NOT YET EXECUTED)

Teacher `BIIT235` · course `CSC-101` · section `SEC000001` (47 enrolled students).
Request files: `fixtures/new_assessment_csc101/` (send only each file's `body`).
Every step except 0 writes to `FYP_Database` and needs owner approval.

| # | Action | Endpoint / command | Database effect | Check before the next step |
|---|---|---|---|---|
| 0a | Start constraint-AI service | `.venv_ai\Scripts\python.exe ai_local_api.py` (port 5001; local model, no DB, no network) | none | `GET http://127.0.0.1:5001/health` → running |
| 0b | Start backend | `.venv\Scripts\python.exe app.py` (port 5000) | none | `GET /` → running |
| 0c | Teacher login | `POST /api/teacher/login` — **done by you/the teacher**; Claude does not enter passwords | none | token role = teacher, id = BIIT235 |
| 1 | Create draft | `POST /api/ai/drafts` (file 01) | INSERT 1 `ConstraintReviewDraft` | note `draft_id` and suggestion ids |
| 2 | Review draft | file 02: DELETE every AI suggestion, ADD `1 <= N <= 1000000`, approve value, approve code, finalize | UPDATE that draft row (several times) | GET draft: finalized, only one ACCEPTED rule |
| 3 | Create assessment | `POST /api/assessments` (file 03 + draft_id) | INSERT 1 `Assessment`; UPDATE draft `assessment_id` | GET assessment: value_constraints = rule, code_constraints empty |
| 4 | Save rubric | `PUT /api/assessments/<id>/rubric` (file 04) | INSERT 2 `AssessmentRubricCriterion` | GET rubric: `rubric_complete: true` |
| 5 | Save reference | `POST .../reference-solution` (file 05) | INSERT 1 `ReferenceSolution` (PENDING) | returned `source_hash` = `529e00e0…aac9ab` |
| 6 | Approve reference | `POST .../reference-solution/approve` (file 06) | UPDATE it to APPROVED | GET shows APPROVED |
| 7a | Authorize execution method | owner adds hash `529e00e0a8e4000826d288f293e33e0e4753ecc49426156b9c936bb730aac9ab` to `trusted_reference_hashes.json` (host) — or waits for a secure sandbox | none | `choose_execution_mode` → trusted_host |
| 7b | Generate tests | `POST .../generate-test-cases` (file 07) — compiles + runs the reference on the host | INSERT 15 `TestCase` (hidden, 0.33 each) | GET test-cases: 15 rows, outputs = `planned_test_inputs.json` |
| 8 | Assign section | `POST .../sections` (file 08) | INSERT 1 `AssessmentSection` | GET sections: SEC000001, 47 enrolled |

After step 8 the assessment is visible and open to 47 real student accounts.
Their submissions will queue as `PENDING` grading jobs because student-code
execution is still disabled (no secure sandbox).

## Rollback reality
After step 3 the assessment cannot be removed through the API (linked draft,
reference solution, rubric and test cases all return 409 by design). Removing
it would need hand-written SQL in dependency order — itself a destructive
operation requiring approval.
