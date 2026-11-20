# Assessment 6 Demo Setup — Proposed Writes (NOT executed)

Date: 2026-11-18. Database: **FYP_E2E_Test only**. FYP_Database is never touched.

**Status (2026-11-18): executed with owner approval.** W1 created rubric
criteria 3 and 4; W2 created assessment_section_id 3; W3 (manual-grade path)
created submission 6 / job 5, finalized 5.00 MANUALLY_GRADED. Driver steps:
`fixtures/run_structured_array_sum_e2e.py` (rubric, assign, teacher-view,
student-view, w3-*). The sections below are the plan as approved.

## 1. Current state (read-only inspection, 2026-11-18 20:5x)

| Item | Value |
|---|---|
| Assessment 6 | "Array Sum (Structured E2E)", teacher BIIT235, course CSC-101, Quiz, total 10.00, validation 50.00 %, deadline 2026-12-20 23:59, no late submissions |
| Mark split | tests 5.00 (10 × 0.50), rubric 5.00 |
| Test cases | 10 (ids 28–37, test_number 1–10, all hidden, 0.50 each); verified against the oracle in S4 |
| Reference solution | id 3, APPROVED (hash 4ea30854…) |
| Rubric | **none** (A6 has 0 `AssessmentRubricCriterion` rows) |
| Section assignment | **none** (only A5 → SEC000001, assessment_section_id 2) |
| Submissions to A6 | 0 |
| SEC000001 | taught by BIIT235 for CSC-101 (SectionCourseTeacher allocation 9843); 4 enrolled test students E2E-TEST-0001..0004 |
| Other E2E records | A5: 2 rubric criteria, 10 tests, 4 submissions (2–5), 4 COMPLETED jobs, 4 FINALIZED evaluations (10.00 / 7.00 / 0.00 / 7.00), 6 EvaluationCriterion, 2 EvaluationCriterionApproval |

Because the rubric allocation is 5.00 and no rubric exists, `check_rubric_ready` would refuse
student submissions to A6 ("Assessment is not open for submissions yet") even if it were assigned.
The rubric must therefore be written **before** the section assignment.

## 2. Minimum writes

### W1 — Rubric for Assessment 6 (2 inserts)

API (teacher BIIT235 token, E2E backend on 127.0.0.1:5000):

```
PUT /api/assessments/6/rubric
{
  "criteria": [
    {"criterion_name": "Logic & Correctness", "max_marks": "3.00",
     "description": "Reads exactly N values and sums them with a 64-bit type (long long) so sums up to 100 x 1e9 do not overflow; handles negative values and N = 1."},
    {"criterion_name": "Code Quality", "max_marks": "2.00",
     "description": "Readable structure and naming, appropriate types, no unnecessary work; reads standard input and prints only the sum."}
  ]
}
```

Validation the service applies: 3.00 + 2.00 = 5.00 = rubric allocation; names unique; owner check;
0 submissions so not locked. Equivalent SQL executed by `replace_assessment_rubric`
(one transaction):

```sql
SELECT total_marks, validation_percentage FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
 WHERE assessment_id = 6 AND teacher_id = 'BIIT235';
-- COUNT submissions for A6 = 0 (else 409 locked)
DELETE FROM dbo.AssessmentRubricCriterion WHERE assessment_id = 6;   -- deletes 0 rows today
INSERT INTO dbo.AssessmentRubricCriterion (assessment_id, criterion_name, max_marks, description)
VALUES (6, N'Logic & Correctness', 3.00, N'Reads exactly N values ...');
INSERT INTO dbo.AssessmentRubricCriterion (assessment_id, criterion_name, max_marks, description)
VALUES (6, N'Code Quality', 2.00, N'Readable structure ...');
COMMIT;
```

Affected: `AssessmentRubricCriterion` +2 rows (expected ids 3 and 4). No other table.

### W2 — Assign Assessment 6 to SEC000001 (1 insert)

```
POST /api/assessments/6/sections
{"section_ids": ["SEC000001"]}
```

Checks: section exists; BIIT235 teaches SEC000001 for CSC-101; not already assigned. Equivalent SQL
(`assign_sections`, one transaction):

```sql
SELECT assessment_id, course_code, deadline_at, allow_late FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
 WHERE assessment_id = 6 AND teacher_id = 'BIIT235';
INSERT INTO dbo.AssessmentSection (assessment_id, section_id)
OUTPUT INSERTED.assessment_section_id VALUES (6, 'SEC000001');
COMMIT;
```

Affected: `AssessmentSection` +1 row (expected assessment_section_id 3). Visible to the 4 E2E test
students only (FYP_E2E_Test contains no real students).

### Not written

- Test cases 28–37: untouched (W1/W2 never touch `TestCase`; the rubric lock and assignment do not modify tests).
- Assessment 6 row, reference solution 3, draft a731f6b1…: untouched.
- Assessment 5 and all its submissions, jobs, evaluations, criteria, approvals: untouched. The
  `UQ_AssessmentSection` key is (assessment_id, section_id), so a second row for SEC000001 does not
  conflict with A5's row 2. Rubric DELETE is scoped to `assessment_id = 6`.

## 3. Read-only verification after W1 + W2

| Check | Call | Expected |
|---|---|---|
| Teacher rubric | `GET /api/assessments/6/rubric` | rubric_required true, rubric_complete true, locked false, 2 criteria |
| Teacher sections | `GET /api/assessments/6/sections` | 1 section SEC000001, enrolled_students 4, submission_count 0 |
| Teacher assessment | `GET /api/assessments/6` | 10 test cases, marks 5.00 total |
| Teacher test cases | `GET /api/assessments/6/test-cases` | ids 28–37 unchanged |
| Student list | `GET /api/student/assessments` as E2E-TEST-0001 | A6 listed; no hidden input/expected output in the payload (driver leak scan) |
| Student result | `GET /api/student/assessments/6/submission` | 404 "No submission found" |
| Non-owner / role | student token on teacher routes | 403 |
| DB | `db_snapshot.py` before/after | only `AssessmentRubricCriterion` (+2) and `AssessmentSection` (+1) change in FYP_E2E_Test; FYP_Database identical |

## 4. Optional W3 — student submission demo for A6 (separate approval)

Automatic grading of a new A6 submission is blocked: the worker never claims jobs while
`get_secure_sandbox()` is None, and the recorded-evidence grader only replays Assessment 5 fixtures.
The working demo path is **manual grading** (no code execution):

1. E2E-TEST-0001 submits a fixture source to `POST /api/assessments/6/submissions`
   → +1 `Submission`, +1 `GradingJob` (PENDING). Not compiled or run.
2. Teacher `POST /api/submissions/<id>/evaluation/manual-grade` → job COMPLETED (`manual-grade:BIIT235`), +1 `Evaluation` (MANUALLY_GRADED).
3. Teacher `POST /api/submissions/<id>/evaluation/finalize` → FINALIZED; student sees the result.

Effect: the rubric becomes locked for A6 (by design). Needs its own approval plus your student login.

## 5. Execution prerequisites

1. Start the E2E backend (`dev_tools/run_e2e_backend.py`); it refuses to start unless connected to FYP_E2E_Test.
2. Fresh teacher token: you type the BIIT235 login in `fixtures/teacher_login.py` (the saved token expired 2026-11-18 16:37).
3. For the student checks: a fresh E2E-TEST-0001 token via `fixtures/student_login.py`.
4. Driver: `fixtures/run_structured_array_sum_e2e.py` currently has check / draft / assessment /
   reference / generate / verify. It needs four small steps — `rubric` and `assign` (writes, gated by
   `--confirm FYP_E2E_Test`, refuse if A6 has submissions), `teacher-view` and `student-view`
   (read-only, hidden-value leak scan). `dev_tools/e2e_api_client.py` is hard-wired to Assessment 5
   and is not reused.
