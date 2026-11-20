# Backend API Contract — Auto Test Case Generator

Last verified against the code: 2026-11-17 (37 routes, `app.url_map`); `GET /api/teacher/sections` added 2026-11-19; `GET /api/teacher/sections/<section_id>/students` added 2026-11-20.

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

`/api/ai/analyze` → `data` (fields added 2026-11-17, all optional for clients; older
fields unchanged):

```json
{
  "value_constraints": [{"text": "1 <= N <= 10^9", "status": "NEEDS_SEMANTIC_REVIEW",
                         "unsupported_numbers": [], "warnings": []}],
  "code_constraints": [{"type": "recursion_required", "evidence": "...", "status": "NEEDS_TEACHER_REVIEW"}],
  "teacher_approval_required": true,
  "warnings": [],
  "model": {"status": "GENERATED", "suggestion_used": true, "input_tokens": 153,
            "input_token_limit": 512, "generated_tokens": 61, "max_new_tokens": 384,
            "output_complete": true}
}
```

* Value-constraint `status`: `NEEDS_TEACHER_REVIEW` (explicit bound found in the statement),
  `NEEDS_SEMANTIC_REVIEW` (model text, all numbers appear in the statement, no warnings),
  `UNVERIFIED_SUGGESTION` (unsupported numbers or any warning).
* Item `warnings[].code`: `OUTPUT_TRUNCATED` (model hit the 384-token output limit),
  `DECODE_INVALID_BYTES`, `DECODE_INCOMPLETE` (malformed placeholder markers).
* Top-level `warnings[].code`: `INPUT_TOO_LONG` (`input_tokens` > 512). The model is **not run**
  and nothing is truncated; `value_constraints` then contains only bounds stated explicitly in
  the problem (possibly none), `model.status` = `NOT_RUN_INPUT_TOO_LONG`. Code constraints are
  still extracted. The teacher adds value constraints manually (draft action `ADD`).
* RAG (retrieved similar training problems) is **off** (`ai_constraint_service.RAG_MODE = "off"`):
  the response above is unchanged. If it is ever enabled server-side, `model.retrieval`
  (`status`, `method`, `k_used`, `context_tokens`, `skipped_for_budget`, `examples[{problem_id, score}]`)
  is added, and a retrieval failure adds top-level warning `RETRIEVAL_UNAVAILABLE` (the
  suggestion is then generated without examples). See `docs/AI_RAG_PHASE1_IMPLEMENTATION.md`.
* **Value constraints from explicit bounds (2026-11-20):** `value_constraints` lists each distinct `(variable, low, high)` once, even if the bound is repeated in the statement and the input format, or written both as `1 <= N <= 100` and as "N is between 1 and 100".
  - Indexed variables (`A[i]`, `grid[i][j]`), powers (`10^9`) and `≤` are recognised.
  - A number followed by `^`, `*` or a decimal part is never truncated (`10^9` is no longer read as `10`).
* **`code_constraints[]` (2026-11-20):**
  - Fields: `{type, evidence, status: "NEEDS_TEACHER_REVIEW", rule, enforcement}`. The new `rule` and `enforcement` fields are additive, and `evidence` is now the full sentence.
  - `rule` is `REQUIRED` or `PROHIBITED`. Prohibitions ("must not use", "do not", "cannot", "without", "avoid", "no X", "X is not allowed") are never reported as requirements.
  - Optional wording ("may use") and bare mentions ("defined recursively") produce nothing.
  - `enforcement` is `STATIC_CHECK` if `code_constraint_checker` verifies the key lexically, or `TEACHER_REVIEW_ONLY` if it cannot. In both cases the checker never deducts marks.

  | `type` | rule | enforcement |
  |---|---|---|
  | `recursion_required` / `recursion_prohibited` | REQUIRED / PROHIBITED | STATIC_CHECK |
  | `loop_required` / `loops_prohibited` (any for/while/do) | REQUIRED / PROHIBITED | STATIC_CHECK |
  | `for_loop_required` (NEW) | REQUIRED | STATIC_CHECK |
  | `conditional_required` (NEW; if/else, switch or `?:`) | REQUIRED | STATIC_CHECK |
  | `function_prohibited:sort`, `:stable_sort`, `:partial_sort` (existing structured form) | PROHIBITED | STATIC_CHECK |
  | `function_required`, `stl_container_required`, `hash_map_required` | REQUIRED | STATIC_CHECK |
  | `dynamic_programming_required` | REQUIRED | TEACHER_REVIEW_ONLY |
  | `gaussian_elimination_prohibited` | PROHIBITED | TEACHER_REVIEW_ONLY |

* **Top-level warning `CODE_REQUIREMENT_NEEDS_MANUAL_REVIEW` (NEW code; the top-level keys are unchanged):** the statement contains a coding rule that has no safe key. Examples:
  - "Use cin and cout";
  - "must use a while loop";
  - "must use sort()";
  - contradictory instructions, in which case neither rule is emitted.

  `message` quotes the sentence. The rule is **never** turned into a constraint. It appears in the draft's `ai_warnings`, and the teacher adds it manually (draft action `ADD`, category `code`) if wanted.
* `POST /api/ai/drafts`: the draft carries the same information for display — `ai_warnings`
  (top-level warnings) and `warnings` on AI value-constraint items. Review, approval and
  finalisation rules are unchanged.

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

## Teacher — my sections (NEW, 2026-11-19, read-only)

### `GET /api/teacher/sections?course_code=CSC-101&semester_no=1&academic_session=2025SM`

Used by the Instructor Dashboard (“My Sections”) and the Select Section screen.

**Which sections are returned:**
- the sections where `SectionCourseTeacher` links the **authenticated teacher** (from the JWT) to `course_code`;
- limited to those whose `Section_Export.semester_no` and `academic_session` match the query.

**Query parameters:** all three are required, and each may appear once.

| Param | Rule |
|---|---|
| `course_code` | letters, digits, `-` `_` `.`, at most 100 characters (case-insensitive) |
| `semester_no` | whole number, 1–20 |
| `academic_session` | 4 digits + 2 letters, for example `2025SM` |

Any other parameter, **including `teacher_id`**, returns 400. The teacher is never taken from the client.

**200 response:**

```json
{
  "status": "success",
  "teacher": {"teacher_id": "BIIT235", "teacher_name": "UMAR FAROOQ", "department": "CS"},
  "course": {"course_code": "CSC-101", "course_name": "PROGRAMMING FUNDAMENTALS"},
  "filters": {"course_code": "CSC-101", "semester_no": 1, "academic_session": "2025SM"},
  "total": 2,
  "total_enrolled_students": 89,
  "sections": [
    {"section_id": "SEC000001", "section_name": "A", "program": "BCS",
     "course_code": "CSC-101", "course_name": "PROGRAMMING FUNDAMENTALS",
     "semester_no": 1, "academic_session": "2025SM", "intake_year": 2025,
     "enrolled_students": 47, "teacher_id": "BIIT235", "teacher_name": "UMAR FAROOQ"},
    {"section_id": "SEC000004", "section_name": "D", "program": "BCS",
     "course_code": "CSC-101", "course_name": "PROGRAMMING FUNDAMENTALS",
     "semester_no": 1, "academic_session": "2025SM", "intake_year": 2025,
     "enrolled_students": 42, "teacher_id": "BIIT235", "teacher_name": "UMAR FAROOQ"}
  ]
}
```

**Fields:**
- Sections are ordered by `program`, then `section_name`, then `section_id`. There is one row per section; no duplicates come from joins.
- `enrolled_students` = `COUNT(DISTINCT student_id)` in `Enrollment_Export` for that section **and course**. This is the same number `assignable-sections` uses.
- `academic_session` is the stored code (`2025SM`). The database does not store words such as Spring, Fall, Morning or Evening, so show the code as-is.
- `intake_year` is the first four digits of that code.
- `filters` echoes the validated query.
- No student identifiers are returned, only counts.
- `teacher_name` comes from `Teacher_Export`, so this endpoint can also supply the dashboard header name (`/api/auth/me` returns only `user_id` and `role`).

**Errors** (all have `{status:"error", message, code}`):

| HTTP | `code` | When | UI state |
|---|---|---|---|
| 401 | `AUTH_REQUIRED` / `TOKEN_EXPIRED` | no or expired token | go to login |
| 422 | `INVALID_TOKEN` | malformed token | go to login |
| 403 | `TEACHER_ONLY` | student token | “not allowed” |
| 403 | `TEACHER_NOT_FOUND` | token's teacher no longer exists | go to login |
| 400 | `INVALID_PARAMETER` | missing, invalid, unknown or duplicate parameter | developer error |
| 404 | `COURSE_NOT_FOUND` | `course_code` not in `Course_Export` | “course not found” |
| 500 | `SECTIONS_UNAVAILABLE` | database or server failure (details are only in the server log) | error + retry |

**Empty state:** a teacher with no matching allocation gets **200** with `"sections": []` and `"total": 0`. That is not an error.

**Select Section flow:**
1. Render one checkbox per `sections[]` item, labelled `{program}-{section_name} ({academic_session}) · {enrolled_students} students`.
2. After `POST /api/assessments` returns `assessment_id`, send the checked IDs to `POST /api/assessments/<assessment_id>/sections` with `{"section_ids": [...]}`.

This works because:
- the same `SectionCourseTeacher` rule authorizes that POST, so every listed section is assignable for an assessment of that course;
- `GET /api/assessments/<id>/assignable-sections` remains the per-assessment view, with `already_assigned` flags.

This endpoint never writes anything.

### `GET /api/teacher/sections/<section_id>/students?course_code=CSC-101&semester_no=1&academic_session=2025SM`

Used by the Section Details screen's “View all students”. It is read-only. It returns the students **enrolled in this course in this section**, meaning distinct `Enrollment_Export.student_id` for section + course, with names from `Student_Export`.

**Access:**
- Requires a teacher JWT. The teacher is taken from the token; a `teacher_id` parameter returns 400.
- The teacher must be allocated to the section for the course in `SectionCourseTeacher`. This is the same rule as the sections list and section assignment.
- A teacher who is not allocated gets **403** before anything about the section's term or students is read.

**Parameters:**
- `section_id` (path): up to 20 characters, for example `SEC000003`.
- Query parameters: the same three as `GET /api/teacher/sections`, all required.
- The section must have exactly that `semester_no` and `academic_session` (case-insensitive), otherwise **404**.

**Pagination:** none. The whole roster is returned (the largest current section has 47 students).

**200 response** (values are illustrative):

```json
{
  "status": "success",
  "section": {"section_id": "SEC000003", "section_name": "C", "program": "BCS",
              "semester_no": 1, "academic_session": "2025SM",
              "course_code": "CSC-101", "course_name": "PROGRAMMING FUNDAMENTALS"},
  "total": 33,
  "students": [
    {"student_id": "2025-ARID-XXXX", "registration_no": "2025-ARID-XXXX",
     "student_name": "FULL NAME", "section_id": "SEC000003",
     "enrollment_status": "ENROLLED"}
  ]
}
```

**Student fields** (only these five):

| Field | Source |
|---|---|
| `student_id` | `Student_Export.student_id`, the primary key and stable identifier |
| `registration_no` | The same value. In this database the ARID registration number *is* the student ID; there is no separate column. |
| `student_name` | `Student_Export.student_name`. `null` only if the enrolled student has no `Student_Export` row; none do today. |
| `section_id` | The requested section |
| `enrollment_status` | `Student_Export.enrollment_status`, for example `ENROLLED` |

**Rules:**
- Students are sorted by `student_id`, and each student appears once even if the enrollment table had duplicate rows.
- `total` equals that section's `enrolled_students` in `GET /api/teacher/sections`.
- **Never returned:** passwords, emails, phone numbers, program or intake details per student, or any other student field.

**Empty state:** **200** with `"total": 0, "students": []`, only for an authorized section that genuinely has no enrolled students in the course.

**Errors** (`{status:"error", message, code}`):

| HTTP | `code` | When |
|---|---|---|
| 401 / 422 | `AUTH_REQUIRED` · `TOKEN_EXPIRED` / `INVALID_TOKEN` | missing, expired or malformed token |
| 403 | `TEACHER_ONLY` | student token |
| 403 | `SECTION_ACCESS_DENIED` | teacher is not allocated to this section for this course |
| 404 | `COURSE_NOT_FOUND` | unknown `course_code` |
| 404 | `SECTION_NOT_FOUND` | unknown section, or section not in the requested semester/session |
| 400 | `INVALID_PARAMETER` | missing, invalid, unknown (including `teacher_id`) or duplicate parameter |
| 500 | `ROSTER_UNAVAILABLE` | database or server failure. A generic message; SQL details are only in the server log. |

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

## Teacher — test-input suggestions (NEW, 2026-11-18)

These endpoints are **teacher only**.
- **Nothing is saved.** No route writes to the database or produces expected outputs.
- **Nothing is executed.** No compiler is invoked and no code runs.
- **Results are candidates, not test cases.** Every candidate has `status: "CANDIDATE"`. The teacher passes chosen inputs to `generate-test-cases`, where approval, reference execution and the exactly-10 rule still apply.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/test-inputs/suggestions` | Body `{problem: {problem_statement, input_format?, output_format?, value_constraints?, sample_input?}, input_schema?, method? ("deterministic_baseline" default \| "model"), count? (1–50, default 20), seed?}`. 200 `{data: {supported, schema_source, shape, input_schema, generation, candidates[], rejected[], additional_inputs, note}}`. |
| POST | `/api/assessments/<id>/test-inputs/suggestions` | Body `{sample_input?, input_schema?, method?, count?, seed?}`. Reads the owned assessment's statement and value constraints (one SELECT); 404 if the teacher does not own it. `data.additional_inputs` is filled only when the value constraints use the legacy 1–3 integer form (`supported, slots, structural_inputs, suggested`). `suggested` has already been checked against the real 10-case rule and can be sent as `additional_inputs` to `generate-test-cases`. |
| POST | `/api/test-inputs/validate` | Body `{problem? , input_schema?, inputs: [1–50 strings]}`. 200 `{data: {schema_source, shape, valid_count, results: [{index, valid, syntax_valid, constraints_valid, semantic_valid, duplicate_of, errors[]}]}}`. |

**Schema source**, tried in this order:
1. an explicit `input_schema`;
2. the legacy integer grammar of `value_constraints`;
3. conservative inference from `sample_input`: one line of scalars; a count line plus an array; a single string with a stated length and character set; a graph or tree with a stated direction; multi-test lines of scalars.

**`input_schema`** (version 1) declares `variables` (integer ranges), then `items`, then optional `relations`:
- **Items:** `ints`, `array` (with `distinct` and `order`), `string` (with `charset` or `chars`), `grid`, `edges` and `repeat`.
- **Edges must state every graph property:** `directed`, `self_loops`, `multi_edges` and `connected`. `tree` is optional.

**Errors.** All use the shared error body.

| Status | Code | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | Bad field types, unknown fields or bad limits |
| 400 | `INVALID_INPUT_SCHEMA` | The `input_schema` is malformed |
| 422 | `UNSUPPORTED_INPUT_FORMAT` | The format is not supported. Includes `reasons[]`; unknown formats are never treated as valid |
| 503 | `AI_MODEL_UNAVAILABLE` | `method: "model"` was requested while `TEST_INPUT_AI_MODE = "off"`, the default |
| 401 / 403 | — | JWT missing / caller is not a teacher |

**Validation results** report three layers separately: `syntax`, `constraints` and `semantic`. The semantic layer covers rules such as distinct or sorted arrays, graph rules and variable relations.

### Structured test cases with an approved input schema (2026-11-18)

**Where the schema lives.** The approved schema is stored inside the approval draft (`ConstraintReviewDraft.draft_json` → `input_schema`). No database migration is needed. Drafts without one keep the legacy 1–3 integer behaviour exactly.

| Method | Path | Notes |
|---|---|---|
| PUT | `/api/ai/drafts/<draft_id>/input-schema` **NEW** | Body `{input_schema: object \| null}`. Teacher-owned drafts only; another teacher's draft → 404. Allowed only **before the value category is approved**, so approval and finalization lock it together with the value constraints. Responses: 400 `INVALID_INPUT_SCHEMA` · 422 `UNSUPPORTED_INPUT_FORMAT` · 409 draft changed/finalized. |
| POST | `/api/assessments/<id>/generate-test-cases` | Unchanged for legacy assessments. With an approved schema, the structured behaviour below applies. |
| POST | `/api/assessments/<id>/generate-test-cases/oracle` | A structured assessment → 400. The verified oracles cover the legacy integer form only. |
| POST | `/api/assessments/<id>/test-inputs/suggestions` | Reuses the approved schema automatically (`schema_source: "approved_input_schema"`). `additional_inputs.format = "exact_text"`; `suggested` has been pre-checked with the real selection rule. |

**`generate-test-cases` with an approved schema:**
- `additional_inputs` are kept as **exact text**: spaces and newlines are preserved, and each input is at most 10,000 characters.
- Each input is validated against the approved schema. Duplicates are exact text, where a final newline is optional.
- Teacher inputs come first, then deterministic baseline cases fill the set to **exactly 10**.
- The reference and grading receive the identical stored text.

| Status | Code | Body / meaning |
|---|---|---|
| 400 | `INVALID_TEST_INPUT` | `errors: [{index, errors: [...]}]`. Nothing runs. |
| 409 | `EXPECTED_OUTPUT_UNVERIFIABLE` | `finalization_status: "NOT_FINALIZED"`, `reason`, `ways_to_finalize`, `validated_inputs` (the exact 10 inputs). Returned when neither a verified sandbox nor an allow-listed reference is available. Nothing is executed or saved, and no expected outputs are produced. |
| 201 | — | `input_format: "approved_input_schema"`. The locked save re-reads the approved schema and refuses if it changed. |

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
