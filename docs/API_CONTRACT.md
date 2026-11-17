# Backend API Contract — Auto Test Case Generator

Base URL (dev): `http://127.0.0.1:5000`
All JSON. Protected routes need `Authorization: Bearer <access_token>`.
Tokens last 2 hours and carry `role` = `teacher` | `student`; identity
(`teacher_id` / `student_id`) is **always taken from the token**, never the body.

## Common responses

| HTTP | Meaning | Body |
|---|---|---|
| 401 | Missing/invalid/expired JWT | `{"msg": "..."}` (Flask-JWT-Extended) |
| 403 | Wrong role / not enrolled / deadline passed | `{"status":"error","message":...}` |
| 404 | Not found **or not owned by caller** (no existence leak) | same |
| 409 | State conflict (duplicate, locked, already finalized) | same |
| 400 | Validation error (message is safe to show) | same |
| 503 | Feature waiting on infrastructure (rubric table not created) | same |
| 500 | Internal error (details only in server log) | same |

Marks are returned as **decimal strings** (`"4.00"`) from new endpoints and as
numbers from the older assessment endpoints. Treat both as decimals; do not do
float arithmetic for display totals.

---

## Auth (existing)

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/api/teacher/login` | `{email, password}` | 200 `{access_token, token_type, teacher:{teacher_id, teacher_name, teacher_email, department}}` |
| POST | `/api/student/login` | `{email, password}` | 200 `{access_token, token_type, student:{student_id, student_name, student_email}}` |
| GET | `/api/auth/me` | — | 200 `{user_id, role}` |

## Teacher — constraint drafts (existing, prerequisite for assessment creation)

`POST /api/ai/drafts`, `GET /api/ai/drafts/<draft_id>`,
`POST /api/ai/drafts/<draft_id>/actions`, `POST /api/ai/drafts/<draft_id>/approve`,
`POST /api/ai/drafts/<draft_id>/finalize` — error shape here is `{"error": ...}`.

## Teacher — assessments (existing)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/assessments` | Required: `course_code, assessment_type, title, problem_statement, input_format, output_format, total_marks, deadline_at (local ISO, no tz, future), validation_percentage (0–100), draft_id`. Optional: `allow_late, late_penalty_marks, additional_constraints`. 201 `{assessment_id}` |
| GET | `/api/assessments` | Own assessments |
| GET | `/api/assessments/<id>` | Own assessment |
| PUT | `/api/assessments/<id>` | Partial update. **New:** changing `total_marks`/`validation_percentage` → 409 if any submission exists, or if it would break an existing rubric (clear rubric first) |
| DELETE | `/api/assessments/<id>` | 409 if sections/notifications/test cases exist |

`validation_percentage` = share of marks for test cases; the rest is the rubric.
Split rule: `test = round_half_up(total × pct / 100, 0.01)`, `rubric = total − test`.

## Teacher — rubric (NEW — returns 503 until DB table is approved)

### `GET /api/assessments/<id>/rubric`
```json
{"status":"success","rubric":{
  "assessment_id":4,"total_marks":"10.00","validation_percentage":"50.00",
  "test_marks_allocated":"5.00","rubric_marks_allocated":"5.00",
  "rubric_required":true,"rubric_complete":false,
  "storage_available":false,"locked":false,
  "criteria":[{"rubric_criterion_id":1,"criterion_name":"Logic","max_marks":"3.00","description":"..."}]}}
```
GET works before the table exists (`storage_available:false`, empty criteria).

### `PUT /api/assessments/<id>/rubric` — replaces all criteria atomically
```json
{"criteria":[{"criterion_name":"Logic","max_marks":3,"description":"Correct algorithm"},
             {"criterion_name":"Code quality","max_marks":2}]}
```
Rules: 1–20 criteria; names non-empty, ≤150 chars, unique (case-insensitive);
`max_marks` > 0, ≤ 2 decimals; **sum must equal `rubric_marks_allocated`**;
if allocation is 0 (100% validation) no criteria allowed.
Errors: 400 validation, 404 not owner, **409 locked (submissions exist)**, 503 storage not enabled.

### `DELETE /api/assessments/<id>/rubric` — clear (only before submissions)

## Teacher — section assignment (NEW)

A teacher can assign their assessment only to sections where
`SectionCourseTeacher` links **that teacher + the assessment's `course_code` + the section**
(the same rule the submission endpoint enforces).

| Method | Path | Body | Success / errors |
|---|---|---|---|
| GET | `/api/assessments/<id>/assignable-sections` | — | 200 `{course_code, total, sections:[{section_id, section_name, semester_no, program, academic_session, enrolled_students, already_assigned}]}` · 404 not owner |
| GET | `/api/assessments/<id>/sections` | — | 200 `{total, sections:[{assessment_section_id, section_id, section_name, semester_no, program, academic_session, enrolled_students, submission_count, removable}]}` · 404 |
| POST | `/api/assessments/<id>/sections` | `{"section_ids": ["SEC-A", "SEC-B"]}` (1–50, ≤20 chars, no duplicates) | 201 `{assessment_id, course_code, assigned:[{assessment_section_id, section_id}], deadline_at}` · **all-or-nothing**: on any problem nothing is saved and the body has `errors:[{section_id, reason}]` with status 404 `SECTION_NOT_FOUND` › 403 `TEACHER_NOT_ASSIGNED_TO_SECTION` › 409 `ALREADY_ASSIGNED` · 400 validation · 404 not owner |
| DELETE | `/api/assessments/<id>/sections/<section_id>` | — | 200 `{removed:true}` · 409 if any submission exists for that section · 404 not assigned / not owner |

`DELETE /api/assessments/<id>` now also returns **409** (not 500) when a rubric,
reference solution or constraint review draft is linked to the assessment, or on
any other foreign-key conflict.

## Teacher — test cases (existing)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/assessments/<id>/reference-solution` **NEW** | `{source_code}` → 201 `{reference_solution_id, source_hash, approval_status:"PENDING"}` · 409 if one exists · 404 not owner |
| GET | `/api/assessments/<id>/reference-solution` **NEW** | Teacher reviews `source_code` + `source_hash` |
| POST | `/api/assessments/<id>/reference-solution/approve` **NEW** | `{source_hash}` (the hash the teacher reviewed) → 200 APPROVED · 409 not PENDING · 400 hash mismatch |
| — | **Fixed rule (2026-11-17)** | Every assessment has **exactly 10** equally weighted test cases. Selection order: boundary/edge cases the generator always includes → `additional_inputs` → seeded random cases to fill to 10. `random_count` is **rejected** (400). More than 10 boundary + additional inputs → 400. Test score = ROUND_HALF_UP(test allocation × passed / 10, 2); `marks` column = allocation / 10 (display only). |
| POST | `/api/assessments/<id>/generate-test-cases` | Body `{additional_inputs?: [≤10 strings]}`. Works for **any owned assessment** with finalized approved constraints and an APPROVED reference. `additional_inputs` are teacher edge cases, validated against the approved constraints; expected outputs always come from the reference. 201 `{execution_mode, generated_count, saved_count, marks_per_test, test_marks_allocated}`. 409 already generated. 400 if constraints are not a supported integer-range form, or if the reference is neither allow-listed for host execution nor runnable in a verified sandbox. |
| POST | `/api/assessments/<id>/generate-test-cases/oracle` **NEW** | Teacher only. Body `{oracle_id, additional_inputs?, dry_run? (default true)}` — **no expected outputs accepted**. Outputs are computed server-side by a reviewed oracle in `verified_oracles.py` that is bound to the assessment's APPROVED reference hash and checked by two independent algorithms. Same preflight and atomic save as the C++ path. `dry_run:true` → 200 preview with `test_cases` (teacher only); `dry_run:false` → 201 saved. 400 unknown oracle / wrong reference / constraints outside verified domain · 404 not owner · 409 tests already exist. |
| GET | `/api/assessments/<id>/test-cases` | Teacher only. Includes hidden inputs/outputs. The `marks` column is display-only; grading always uses **equal weight**. |

## Teacher — submissions & evaluation review (NEW)

### `GET /api/assessments/<id>/submissions`
```json
{"status":"success","assessment_id":4,"total":1,"submissions":[{
  "submission_id":5,"student_id":"...","student_name":"...","file_name":"a.cpp",
  "submitted_at":"2026-11-20T10:00:00","is_late":false,"section_id":"...",
  "job_status":"PENDING|PROCESSING|COMPLETED|FAILED","attempt_count":0,"last_error":null,
  "grading_status":"QUEUED|GRADING|AUTO_GRADED_AWAITING_REVIEW|AUTO_GRADED_NEEDS_REVIEW|NEEDS_MANUAL_GRADING|RESULT_PUBLISHED",
  "suggested_score":"8.00","auto_evaluation_status":"COMPLETED|NEEDS_REVIEW|COMPILATION_FAILED|MANUAL_GRADING",
  "final_score":null,"result_status":null,"tests_passed":8,"tests_run":10}]}
```

### `GET /api/submissions/<submission_id>/evaluation`
Returns `submission_id, student_id, file_name, source_code, submitted_at, is_late,
assessment{assessment_id,title,total_marks,validation_percentage,allow_late,late_penalty_marks},
job{job_status,attempt_count,last_error}, grading_status,
evaluation{suggested_score, auto_evaluation_status, auto_evaluation_feedback, final_score,
result_status, instructor_feedback, tests_passed, tests_run, criteria[{criterion_name,rating,explanation}]} | null`.

`auto_evaluation_feedback` has two clearly separated sections:
deterministic test results, then *provisional* AI rubric feedback.

### `POST /api/submissions/<submission_id>/evaluation/finalize`
```json
{"final_score": 8, "instructor_feedback": "Good work"}
```
- `final_score`: 0 … total_marks, ≤ 2 decimals. Teacher decides late penalties.
- If automatic grading finished → sets final score (`mode: REVIEWED_AUTOMATIC_GRADE`).
- If the job is `FAILED` (infrastructure) and no evaluation exists → manual grade (`mode: MANUAL_GRADE`).
- 409 if already finalized, or grading still pending.

### `GET /api/grading/status`
`{"student_execution_enabled":false,"sandbox":null,"reason":"..."}` — show a banner while disabled.

## Student (existing + NEW)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/student/assessments` **NEW** | Assessments assigned to the student's enrolled sections: `assessment_id, course_code, assessment_type, title, problem_statement, input_format, output_format, total_marks, deadline_at, allow_late, late_penalty_marks, has_submitted, open_for_submissions`. Only sections where the student is enrolled for that course **and** the assessment's teacher teaches it. `open_for_submissions` = rubric complete + deadline open (or late allowed) + not yet submitted. **No test cases.** |
| POST | `/api/assessments/<id>/submissions` | `{source_code (≤100k chars), file_name?}`; `student_id` in body is rejected. 201. 409 duplicate; 403 not enrolled / deadline passed / **"Assessment is not open for submissions yet"** (rubric incomplete, once rubric storage is enabled). One submission per assessment. |
| GET | `/api/student/assessments/<id>/submission` **NEW** | `{submission_id, assessment_id, file_name, submitted_at, status: SUBMITTED|GRADING|UNDER_REVIEW|RESULT_PUBLISHED, result}`. `result` is `null` until the teacher finalizes; then a structured result built only from permitted fields: `{outcome, final_score, total_marks, tests_passed, tests_run, test_marks, test_marks_available, rubric_marks, rubric_marks_available, rubric_source, criteria[{criterion_name, marks, max_marks[, teacher_comment]}], instructor_feedback, compilation_diagnostics, summary[]}`. No automatic feedback text, AI explanations, suggested scores, reliability flags, provenance, failed test numbers, hidden inputs or expected outputs are returned (see docs/RESULT_PRIVACY_AND_OVERRIDES.md). |

Recommended polling for submission status: every 10–15 s while `SUBMITTED`/`GRADING`.
