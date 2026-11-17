# Backend API Contract — Auto Test Case Generator

Last verified against the code: 2026-11-17 (37 routes, `app.url_map`).

Base URL: the backend listens on `http://127.0.0.1:5000`. Frontend code
should call **relative** `/api/...` URLs through a dev-server proxy (see
`FRONTEND_INTEGRATION.md`); the backend has no CORS support.
All JSON. Protected routes need `Authorization: Bearer <access_token>`.
Tokens last 2 hours (no refresh) and carry `role` = `teacher` | `student`;
identity (`teacher_id` / `student_id`) is **always taken from the token**,
never from the body.

## Error contract (all routes)

```json
{"status": "error", "message": "Human-readable, safe to show", "code": "OPTIONAL_CODE"}
```

`message` is always present. `code` is present on framework-level errors
(below) and may be absent on route-specific errors. Some routes add fields,
e.g. section assignment adds `errors: [...]`. Success bodies carry
`"status": "success"`.

| HTTP | Meaning | `code` |
|---|---|---|
| 400 | Validation error (message is safe to show) | — |
| 401 | Missing token / expired token | `AUTH_REQUIRED` / `TOKEN_EXPIRED` |
| 422 | Malformed token | `INVALID_TOKEN` |
| 403 | Wrong role / not enrolled / deadline passed | — |
| 404 | Not found **or not owned by the caller** (no existence leak); unknown route | — / `NOT_FOUND` |
| 405 | Method not allowed | `METHOD_NOT_ALLOWED` |
| 409 | State conflict (duplicate, locked, already finalized, feature not migrated) | — |
| 502 / 504 | AI constraint service unavailable / timed out | — |
| 500 | Internal error (details only in the server log) | — or `INTERNAL_SERVER_ERROR` |

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
| GET | `/api/students` | — | Teacher only. **Placeholder:** the first 10 students overall (not scoped to the teacher's sections). Do not build UI on it (gap G11). |

Login errors: 400 missing fields, 401 `Invalid email or password`.

## Teacher — AI constraint analysis and drafts (prerequisite for assessment creation)

These routes call the local AI constraint service (`ai_local_api.py`, port
5001), which must be running. They return 502 if it is unavailable, and 504
on timeout (analyze only).

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/api/ai/analyze` | `{problem, input_format, output_format}` (strings, ≤ 10 000 chars, problem non-empty) | 200 `{status, data}`: raw analysis, nothing saved |
| POST | `/api/ai/drafts` | same | 201 `{status, draft}`: draft saved for this teacher |
| GET | `/api/ai/drafts/<draft_id>` | — | 200 `{status, draft}` · 404 |
| POST | `/api/ai/drafts/<draft_id>/actions` | `{action: ACCEPT\|EDIT\|DELETE\|ADD, category: value\|code, constraint_id?, text?}` | 200 `{status, draft}` · 400 invalid action · 409 draft changed / finalized |
| POST | `/api/ai/drafts/<draft_id>/approve` | `{category}` | 200 `{status, draft}` · 400 · 409 |
| POST | `/api/ai/drafts/<draft_id>/finalize` | — | 200 `{status, draft, approved_constraints}` · 400 · 409 |

A finalized `draft_id` is required by `POST /api/assessments`.

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

## Teacher — rubric (`dbo.AssessmentRubricCriterion` exists in both databases)

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

### `GET /api/submissions/<submission_id>/evaluation` (teacher, owner only)
Returns:
- `submission_id, student_id, file_name, source_code, submitted_at, is_late`;
- `assessment{assessment_id, title, total_marks, validation_percentage, allow_late, late_penalty_marks}`;
- `job{job_status, attempt_count, last_error}`;
- `grading_status`;
- `evaluation` (or `null`), with:
  - `suggested_score, auto_evaluation_status, auto_evaluation_feedback`;
  - `final_score, result_status, instructor_feedback, tests_passed, tests_run`;
  - `verified_test_marks` (re-derived equal-weight test marks, or `null` for a manual grade);
  - `ai_criteria[{criterion_name, rating, explanation}]`: the AI recommendation (`criteria` is the same list, kept for compatibility);
  - `teacher_criteria[{criterion_name, approved_marks, teacher_comment, approved_by, approved_at}]`;
  - `criterion_approval_available`: false until the approval table exists in that database (migration M3 for FYP_Database).

`auto_evaluation_feedback` (teacher only) contains, in separate sections:
- the deterministic test results;
- the *provisional* AI rubric assessment, with reliability flags;
- the teacher-only Python verification report.

### `POST /api/submissions/<submission_id>/evaluation/finalize`
```json
{"final_score": "7.00",
 "instructor_feedback": "Optional text shown to the student",
 "criteria": [{"criterion_name": "Logic & Algorithm", "marks": "1.00", "comment": "optional"},
              {"criterion_name": "Code Quality", "marks": "2.00"}]}
```
- **`final_score`:** 0 … total_marks, at most 2 decimals. Late penalties are
  not applied automatically; the teacher decides.
- **`instructor_feedback`:** must not quote hidden test inputs (3 characters
  or more) or hidden expected outputs (6 characters or more); otherwise 400.
- **`criteria` (optional):** teacher-approved criterion marks.
  - They must cover every rubric criterion exactly once, each between 0 and
    its `max_marks`.
  - **`final_score` must equal `verified_test_marks` + Σ marks**; otherwise 400.
  - The AI's criteria are kept unchanged.
  - Returns 409 when `criterion_approval_available` is false.
- **Success:** 200 `{status, message, evaluation{submission_id, final_score, result_status: "FINALIZED", mode, criteria_approved}}`.
  - `mode` is `REVIEWED_AUTOMATIC_GRADE` when automatic grading had finished.
  - `mode` is `MANUAL_GRADE` when the job is `FAILED` (infrastructure) and no
    evaluation exists.
- **409** if the evaluation is already finalized (finalized marks are
  immutable), or if grading has not finished. For a job that stays
  `PENDING`, use the manual grading fallback below.
- **Errors:** 404 for another teacher, 403 for a student.

### `POST /api/submissions/<submission_id>/evaluation/manual-grade` (teacher, owner only)
An explicit **manual grading fallback** for a submission that automatic
grading has not processed (job `PENDING`, for example while secure execution
is disabled) or could not process (job `FAILED`).

```json
{"confirm_manual_grade": true, "final_score": "6.50",
 "justification": "Why this was graded manually (10–2000 characters, teacher-only audit)",
 "instructor_feedback": "Optional text shown to the student"}
```

**Success:** 200 `{status, message, evaluation{submission_id, final_score, result_status: "FINALIZED", mode: "MANUALLY_GRADED", previous_job_status}}`.

**What it records, in one transaction:**
- An Evaluation with `auto_evaluation_status` `MANUALLY_GRADED`, finalized,
  with no suggested score, 0/0 tests and no criteria. Its
  `auto_evaluation_feedback` holds a teacher-only audit record: teacher,
  UTC time, justification, final mark and the previous job state.
- The job is set to `COMPLETED` with `worker_id` `manual-grade:<teacher>`,
  so the automatic worker never picks it up afterwards.

Nothing is fabricated: no compilation result, test passes, verified test
marks or AI marks.

**The teacher view adds** `evaluation.manual_grading`, the parsed audit record.

**Students see** `outcome: "MANUALLY_GRADED"`, the final mark and the
instructor feedback. `tests_passed`, `tests_run` and `test_marks` are
`null`, and the justification is never shown to them.

**Errors:**
- **400:** `confirm_manual_grade` is not `true`; a missing or short
  justification; an out-of-range score or one with more than 2 decimals;
  unknown fields; or instructor feedback that quotes hidden test data.
- **403:** a student.
- **404:** another teacher, or an unknown submission.
- **409:**
  - an evaluation already exists (automatic, AI or manual; use finalize);
  - a worker holds the job (`PROCESSING`, active or expired lease);
  - the job is in any other state;
  - a concurrent change happened.

### `GET /api/grading/status` (teacher)
`{"status":"success","student_execution_enabled":false,"sandbox":null,"reason":"...","active_database":"FYP_Database"|"FYP_E2E_Test"}`.
Show a banner while execution is disabled.

## Student (existing + NEW)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/student/assessments` **NEW** | Assessments assigned to the student's enrolled sections: `assessment_id, course_code, assessment_type, title, problem_statement, input_format, output_format, total_marks, deadline_at, allow_late, late_penalty_marks, has_submitted, open_for_submissions`. Only sections where the student is enrolled for that course **and** the assessment's teacher teaches it. `open_for_submissions` = rubric complete + deadline open (or late allowed) + not yet submitted. **No test cases.** |
| POST | `/api/assessments/<id>/submissions` | `{source_code (≤100k chars), file_name?}`; `student_id` in body is rejected. 201. 409 duplicate; 403 not enrolled / deadline passed / **"Assessment is not open for submissions yet"** (rubric incomplete, once rubric storage is enabled). One submission per assessment. |
| GET | `/api/student/assessments/<id>/submission` **NEW** | `{submission_id, assessment_id, file_name, submitted_at, status: SUBMITTED|GRADING|UNDER_REVIEW|RESULT_PUBLISHED, result}`. `result` is `null` until the teacher finalizes; then a structured result built only from permitted fields: `{outcome, final_score, total_marks, tests_passed, tests_run, test_marks, test_marks_available, rubric_marks, rubric_marks_available, rubric_source, criteria[{criterion_name, marks, max_marks[, teacher_comment]}], instructor_feedback, compilation_diagnostics, summary[]}`. No automatic feedback text, AI explanations, suggested scores, reliability flags, provenance, failed test numbers, hidden inputs or expected outputs are returned (see docs/RESULT_PRIVACY_AND_OVERRIDES.md). |

`rubric_source` tells the UI how to present the breakdown:
- `TEACHER_APPROVED`: the teacher's criterion marks.
- `AI_RECOMMENDATION_ACCEPTED_BY_TEACHER`: AI marks that add up exactly to the
  final mark.
- `FINAL_TOTAL_ONLY`: no per-criterion marks; show `rubric_marks` only.

The published breakdown therefore never contradicts `final_score`.
`compilation_diagnostics` is set only when `outcome` is `COMPILATION_FAILED`.

Recommended polling for submission status: every 10–15 s while `SUBMITTED`/`GRADING`.
With execution disabled, real submissions remain `SUBMITTED` until the teacher
uses the manual grading fallback (`outcome: "MANUALLY_GRADED"`).
