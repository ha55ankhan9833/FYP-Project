# Step 1 → Step 2 → Step 3: Test-Case Generation Contract

Backend only, written 2026-11-20 for the React Step 1/2/3 screens. Every
path below exists in the backend (`app.url_map`). All of them require a
**teacher JWT**; the teacher is always the token identity and never comes
from the request body.

## 1. Capability map

| Need | Status | Where |
|---|---|---|
| AI constraint suggestions into a teacher-owned draft | ✅ existing | `POST /api/ai/drafts` (calls the local AI service on :5001) |
| Save the teacher's edits, approve both categories, finalize — in **one** action | ✅ **NEW** | `POST /api/ai/drafts/<id>/approve-reviewed` |
| Per-item review (older UI) | ✅ existing | `/actions`, `/approve`, `/input-schema`, `/finalize` |
| Count-prefixed array input (N, then N integers) | ✅ existing, verified | input schema v1: `ints` + `array` with `length: "N"` |
| Schema suggestion for the teacher to review | ✅ existing | `POST /api/test-inputs/suggestions` (inferred from `sample_input` + value constraints; nothing saved) |
| Assessment creation (Assignment / Quiz, marks, deadline, late flag) | ✅ existing | `POST /api/assessments` (a repeat click now returns **409 + existing `assessment_id`**) |
| Rubric | ✅ existing | `PUT /api/assessments/<id>/rubric` |
| Reference solution, submitted and approved by hash | ✅ existing | `POST` / `GET` / `POST …/approve` `/api/assessments/<id>/reference-solution` |
| What is still missing before generate / publish | ✅ **NEW** (read-only) | `GET /api/assessments/<id>/generation-readiness` |
| Generate exactly 10 cases with verified outputs | ✅ existing, ⚠ **blocked for a new problem** (see §2) | `POST /api/assessments/<id>/generate-test-cases` (or `/oracle`) |
| Read generated cases | ✅ existing | `GET /api/assessments/<id>/test-cases` |
| Assign to sections (= publish) | ✅ existing | `POST /api/assessments/<id>/sections` |
| Edit or delete a generated case | ❌ not supported | No endpoints. Generation runs once (409 once tests exist). Custom cases are sent **before** generation as `additional_inputs`. |
| Default sample input / output | ❌ not stored | No column on `Assessment` (needs a migration) |
| Cutoff time (separate from the deadline) | ❌ not stored | Only `deadline_at` |
| Late deduction "per 24 hours" | ❌ not supported | `late_penalty_marks` is one flat number (0..total, requires `allow_late`) and **no grading code subtracts it** |
| Publish / draft status | ❌ no column | Students see an assessment as soon as it is assigned to their section. Assignment is now **guarded** (10 test cases and a complete rubric). |
| Verified expected outputs for max/min | ✅ **NEW** | `max_min_v1` oracle via `…/generate-test-cases/oracle` (§2a) |

## 2a. Update 2026-11-20: Option B implemented (`max_min_v1`)

**The owner chose Option B.** Expected outputs for "Find Maximum and Minimum Using Loops" now come from a reviewed Python oracle. **No C++ is compiled or executed**, and the student-code execution gate stays disabled.

**Reference solution (reviewed, never compiled):**
- File: `fixtures/reference_solutions/max_min_reference.cpp`, SHA-256 `89aca8e6279d32de33fd2b65366ec813c55164743c3d4704604aef3c5ae17240`. It uses LF bytes, pinned by `.gitattributes`.
- The teacher must submit **exactly this text** as the reference solution and approve it. The oracle is eligible only when the assessment's APPROVED reference has this hash.

**Oracle `max_min_v1`** (`verified_oracles.py`):
- It serves only an approved schema of exactly this shape: a count line `N`, then an unrestricted array of N integers, with no relations.
- The domain is `1 <= N <= 100` and `-1000 <= A[i] <= 1000`; a narrower approved range is allowed.
- Every input is validated against the approved schema **before** either algorithm runs.
- Output is `"{max}\n{min}\n"`, computed by two independent algorithms that must agree exactly: built-in `max`/`min`, and a line-by-line transcription of the reference loop.

**Equivalence evidence:** `fixtures/verify_max_min_oracle.py`, with results in `fixtures/max_min_oracle/oracle_verification.json`. It found 0 disagreements over:
- all N=1 arrays and all N=2 arrays across the full value domain (4,004,001 arrays);
- all N=3 arrays over 21 boundary values;
- 200,000 seeded random arrays.

**Generation:** `POST /api/assessments/<id>/generate-test-cases/oracle`, body `{"oracle_id": "max_min_v1", "additional_inputs": [exact text, ≤10], "dry_run": true|false}`.
- It runs the same preflight as the other paths: owner, finalized approval, APPROVED reference with an intact hash, no existing tests.
- Inputs come from the existing structured selection: teacher cases first, then deterministic baseline cases, exactly 10, unique.
- Outputs are computed twice, then validated, then saved atomically with `structured=True`. The save re-checks the approval and schema under lock.

**Responses:**
- `dry_run:true` → **200** `{data: {execution_mode: "verified_python_oracle", oracle_id, input_format: "approved_input_schema", generated_count: 10, saved_count: 0, test_cases: [{test_number, test_name, category, input_data, expected_output}]}}`.
- `dry_run:false` → **201** `{data: {…, saved_count: 10, marks_per_test, test_marks_allocated}}`.
- A retry after saving → **409** "Assessment already has test cases". Nothing is duplicated; read them with `GET …/test-cases`.

**Errors:**
- 400 `INVALID_TEST_INPUT` `errors[]` for a bad custom case;
- 400 for a wrong reference hash, a schema outside the domain, or the wrong input format;
- 404 for not the owner.

**Publishing guard:** `POST /api/assessments/<id>/sections` returns **409 `ASSESSMENT_NOT_READY`** with `missing[]` unless there are exactly 10 saved test cases and the rubric is complete.

## 2. Why Step 3 is blocked, and the precise fixes

These are the root causes found in the code.

**B1 — Expected outputs for a NEW problem have no approved source.**
This is the hard blocker. `generate-test-cases` produces outputs only from:
1. a verified secure sandbox: `secure_execution_gate.get_secure_sandbox()` returns `None` (no Docker or virtualization on this machine);
2. an **owner-approved** host allowlist entry for the reference's exact SHA-256 (`trusted_reference_hashes.json`, currently factorial and array-sum only);
3. a reviewed Python oracle bound to that hash (`verified_oracles.py`, currently `prime_check_v1` only, and only for the single-integer format).

For "Find Maximum and Minimum" none of these exists. The endpoint therefore returns **409 `EXPECTED_OUTPUT_UNVERIFIABLE`** together with the 10 validated inputs, and executes and saves nothing. That is correct and must not be bypassed.

Each fix needs your explicit approval:

| Option | What happens | What you approve |
|---|---|---|
| **A. Allowlisted reference** (existing mechanism, used for Array Sum) | A reviewed `max_min_reference.cpp` is added; its SHA-256 goes into `trusted_reference_hashes.json`. The teacher pastes that exact file as the reference solution. Generation compiles and runs only that reviewed file on the host. | (1) the reference source, (2) its hash in the allowlist, (3) host compilation/execution of that one file |
| **B. Verified Python oracle** (no C++ execution) | `oracle_test_case_service` gains structured-schema support. A `max_min_v1` oracle is registered: two independent algorithms (built-in `max`/`min` vs a single-pass loop) must agree, and it is bound to the reviewed reference hash, with an equivalence check like `prime_check_v1`'s. | the oracle code, its reference hash, and adding it to `VERIFIED_ORACLES` |
| C. Secure sandbox | Docker or a VM-based runner | installing software and changing system settings |

**B2 — Constraint approval took many calls and could leave items PENDING.**
Fixed by `approve-reviewed`: one call, using exactly what the teacher saw.

**B3 — A repeat click on create gave a generic 400.**
Fixed: it now returns 409 `DRAFT_ALREADY_USED` with the existing `assessment_id`.

**B4 — Step 3 could not tell *what* was missing until generation failed.**
Fixed by `generation-readiness`.

## 3. The "Generate Test Cases" click — exact sequence

The frontend keeps `draft_id` and `assessment_id` in state, so a retry never starts over. **Stop at the first failure, keep every field the teacher entered, and show the error.** The click must never publish.

| # | Call | Notes |
|---|---|---|
| 0 | *(Step 2 load, once)* `POST /api/ai/drafts` `{problem, input_format, output_format}` → `draft` | Store `draft.draft_id`. Show AI items for editing. Never reload suggestions over the teacher's edits. |
| 0b | *(Step 2 load)* `POST /api/test-inputs/suggestions` `{problem: {problem_statement, input_format, output_format, value_constraints, sample_input}}` → `data.input_schema` | Show the inferred schema for **review**. The teacher confirms or edits it. Nothing is saved. |
| 1 | Client-side validation of Step 1/2 fields | |
| 2 | `POST /api/ai/drafts/<draft_id>/approve-reviewed` `{value_constraints:[…visible…], code_constraints:[…visible, may be []…], input_schema: {…reviewed…} \| null}` | **This is the approval.** On 200 (including `replay:true`) continue. On 409 `DRAFT_CHANGED`, retry once. |
| 3 | `POST /api/assessments` `{course_code, assessment_type: "Assignment"\|"Quiz", title, problem_statement, input_format, output_format, total_marks, deadline_at (local ISO, future), validation_percentage, allow_late, late_penalty_marks, additional_constraints, draft_id}` | On 201, store `assessment_id`. On **409 `DRAFT_ALREADY_USED`**, use `assessment_id` from the body (no duplicate). |
| 4 | `PUT /api/assessments/<id>/rubric` `{criteria:[…]}` | Required if `validation_percentage < 100`. The sum must equal `rubric_marks_allocated` (`GET …/rubric` shows it). |
| 5 | Reference solution: `POST /api/assessments/<id>/reference-solution` `{source_code}`, then `GET` it, then `POST …/reference-solution/approve` `{source_hash}` | **A separate teacher review item**: show the code and hash and ask for approval. Never auto-approve. On 409 "exists", `GET` and continue. |
| 6 | `GET /api/assessments/<id>/generation-readiness` | If `ready_to_generate` is false, show `missing_for_generation[]` and stop. Use `checks.expected_output_source.endpoint` for step 7. |
| 7 | `POST /api/assessments/<id>/generate-test-cases` `{additional_inputs: [exact text, ≤10]}` (or `…/oracle` `{oracle_id, additional_inputs, dry_run:false}` when readiness says so) | On 201, go to step 8. On 409 `EXPECTED_OUTPUT_UNVERIFIABLE`, show `validated_inputs` as "pending verification": **no outputs, not Step 3 success**. |
| 8 | `GET /api/assessments/<id>/test-cases` → Step 3 | Show the cases exactly as stored. Navigate to Step 3 **only** when `total == 10`. |
| Publish (separate button) | `POST /api/assessments/<id>/sections` `{section_ids:[…]}` | Only when readiness `ready_to_publish` is true. The server also refuses with **409 `ASSESSMENT_NOT_READY`** (`missing[]`) otherwise. |

**For max/min,** step 6 reports `expected_output_source: {source: "VERIFIED_ORACLE", endpoint: "generate-test-cases/oracle", oracle_id: "max_min_v1"}`. Step 7 is then:
1. `POST …/generate-test-cases/oracle {"oracle_id": "max_min_v1", "additional_inputs": [...], "dry_run": true}` to preview;
2. the same call with `"dry_run": false` to save.

Show the preview in Step 3, and save only when the teacher confirms. Custom cases go in `additional_inputs` on **both** calls.

**Custom cases (Step 3 "Add Custom Case").** Because generation saves once, custom inputs must be collected **before** step 7 and sent as `additional_inputs`. Expected outputs always come from the approved source, never from the client. Check them first with `POST /api/test-inputs/validate {input_schema, inputs}`.

## 4. New endpoints

### `POST /api/ai/drafts/<draft_id>/approve-reviewed`

The body must contain exactly these three keys:

```json
{"value_constraints": ["1 <= N <= 100", "-1000 <= A[i] <= 1000"],
 "code_constraints": ["for_loop_required", "conditional_required",
                      "function_prohibited:sort", "recursion_prohibited"],
 "input_schema": {"version": 1, "variables": {"N": {"min": 1, "max": 100}},
                  "items": [{"type": "ints", "vars": ["N"]},
                            {"type": "array", "name": "A", "length": "N",
                             "min": -1000, "max": 1000}],
                  "relations": []}}
```

**What it does,** in one optimistic-concurrency write and using only the existing review operations:
- matching AI items become ACCEPTED (or EDITED when the text changed);
- AI items not in the list become REJECTED;
- new texts are ADDED by the teacher;
- the schema is stored;
- both categories are approved and the draft is finalized.

`code_constraints: []` is an explicit "no code rules" approval.

**200 response:**

```json
{"status": "success", "replay": false, "draft": {…finalized…},
 "approved_constraints": [{"id", "category", "text", "source", "review_status": "ACCEPTED", …}],
 "input_schema": {…}, "notes": [{"code": "CODE_CONSTRAINT_NOT_AUTO_CHECKED", "constraint", "message"}]}
```

- A retry with identical content returns `replay: true` and writes nothing.
- `notes` lists code texts that grading cannot check automatically. They are saved as written and reported as INCONCLUSIVE for teacher review.

**Errors:**

| HTTP | `code` | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | wrong keys (including `teacher_id`), empty value list, duplicates, text over 500 chars, more than 50 items |
| 400 / 422 | `INVALID_INPUT_SCHEMA` / `UNSUPPORTED_INPUT_FORMAT` | malformed or unsupported schema |
| 422 | `SCHEMA_CONSTRAINT_MISMATCH` | a visible bound contradicts the schema (`details: [{constraint, schema_name, schema_range}]`) |
| 422 | `INPUT_SCHEMA_REQUIRED` | `input_schema: null`, but the constraints are not the 1–3 integer form. The schema must be reviewed; it is never inferred silently. |
| 404 | `DRAFT_NOT_FOUND` | not this teacher's draft |
| 409 | `DRAFT_REVIEW_CONFLICT` | already approved or finalized with **different** content |
| 409 | `DRAFT_CHANGED` | concurrent change; reload and retry |

**Database effect:** a single `UPDATE dbo.ConstraintReviewDraft` (JSON and `is_finalized`). Nothing else.

### `POST /api/assessments` — repeat click (changed)

If the `draft_id` is already linked to an assessment **owned by this teacher**, the response is **409**:

```json
{"status": "error", "code": "DRAFT_ALREADY_USED", "assessment_id": 42, "message": "…"}
```

Nothing is inserted. Other errors are unchanged: 400 validation, and 400 when the draft is linked to someone else's assessment or is not finalized.

### `GET /api/assessments/<id>/generation-readiness` (read-only)

```json
{"status": "success", "data": {
  "assessment_id": 7,
  "ready_to_generate": false,
  "missing_for_generation": [{"code": "EXPECTED_OUTPUT_SOURCE_UNAVAILABLE", "detail": "…"}],
  "ready_to_publish": false,
  "missing_for_publish": [{"code": "TEST_CASES_NOT_GENERATED", "detail": "0 of 10"}],
  "checks": {"constraints_approved": true, "input_format": "APPROVED_INPUT_SCHEMA",
             "reference_solution": {"reference_solution_id": 3, "status": "APPROVED",
                                    "source_hash": "…", "language": "cpp", "hash_intact": true},
             "expected_output_source": null,
             "test_case_count": 0, "required_test_case_count": 10,
             "rubric_ready": true, "assigned_section_count": 0}}}
```

**`missing_for_generation` codes:**
- `CONSTRAINTS_NOT_APPROVED`
- `INPUT_SCHEMA_REQUIRED`
- `REFERENCE_SOLUTION_MISSING`
- `REFERENCE_SOLUTION_NOT_APPROVED`
- `REFERENCE_SOLUTION_INVALID`
- `EXPECTED_OUTPUT_SOURCE_UNAVAILABLE`
- `TEST_CASES_ALREADY_EXIST`

**`missing_for_publish` codes:** `TEST_CASES_NOT_GENERATED`, `RUBRIC_INCOMPLETE`.

**`expected_output_source`:**
- `{source: "SECURE_SANDBOX" | "TRUSTED_HOST_ALLOWLIST", endpoint: "generate-test-cases"}`;
- or `{source: "VERIFIED_ORACLE", endpoint: "generate-test-cases/oracle", oracle_id}`;
- or `null`.

These are decided exactly as the generation endpoints decide. Errors: 401/422 token, 403 `TEACHER_ONLY`, 404 `ASSESSMENT_NOT_FOUND`, 500 `READINESS_UNAVAILABLE`.

## 5. Assessment, marks and policy facts (existing rules, unchanged)

- **`assessment_type`:** free text, at most 20 characters, no database check. Existing rows use `Quiz`. Send exactly `"Assignment"` or `"Quiz"`.
- **Test cases and marks:**
  - Exactly **10** equal-weight test cases per assessment.
  - `test = ROUND_HALF_UP(total × validation_percentage / 100, 0.01)` and `rubric = total − test`.
  - Test score = `test × passed / 10`, rounded to 2 decimal places.
- **Rubric before publishing:** rubric criteria must sum to the rubric allocation before students can submit (`check_rubric_ready`).
- **Sections:** one or more, all-or-nothing. A teacher can assign only sections where `SectionCourseTeacher` links them to the assessment's course.
- **Deadline:**
  - `deadline_at` must be local time with no time zone, and in the future.
  - Students submitting after it are refused unless `allow_late` is on, in which case the submission is stored with `is_late`.
  - `late_penalty_marks` is stored and shown but **not deducted** by grading.

## 6. Decisions still needed (nothing below was changed)

1. ~~Expected-output source for max/min~~ **Decided 2026-11-20:** Option B, implemented (§2a).
2. **Late deduction per 24 h and a cutoff time:**
   - needs new `Assessment` columns (for example `cutoff_at datetime2 NULL` and `late_penalty_per_day decimal(9,2) NULL`), a migration, and grading logic that applies the deduction;
   - until then, send the flat `late_penalty_marks` and do not claim it is deducted.
3. **Default sample input / output:** needs columns or a visible "sample" `TestCase` category (migration or policy decision). Until then the values are **not stored**, so the frontend must say so rather than dropping them silently.
4. **Edit or delete generated cases before publishing:** needs a new "discard unpublished test cases" endpoint, allowed only with 0 sections and 0 submissions, so cases can be regenerated.
5. ~~Server-side publish guard~~ **Done 2026-11-20:** `POST …/sections` returns 409 `ASSESSMENT_NOT_READY` unless there are 10 test cases and the rubric is complete.
