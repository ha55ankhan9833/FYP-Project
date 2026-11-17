# Student result privacy and teacher criterion approval

Status (2026-11-17): **code-only fixes implemented and tested; schema
change prepared but NOT applied.** FYP_Database and FYP_E2E_Test are
unchanged.

## 1. Root causes

| Issue found in G3 | Root cause |
|---|---|
| AI suggested total and reliability flags shown to students | `get_student_submission` returned the stored `auto_evaluation_feedback` (the full internal text) and tried to remove sensitive parts afterwards. `student_safe_feedback` cut only the teacher-only verification section, so the AI section's "Suggested total" and "Reliability flags" lines, and the development provenance label, reached students. |
| Published criterion marks disagreed with the final mark | Students always received the AI's `EvaluationCriterion` rows. The finalize API stored only a final total, and nothing tied the published criteria to it. |
| Over-redaction ("iterating from [hidden test input]") | The response contained free text written by the AI, which had seen the hidden tests. `redact_hidden_inputs` therefore replaced every whole-token hidden value, including 1, 2 and 4, inside ordinary prose. |

## 2. Code-only fixes (implemented)

### 2.1 How the student result is built

`student_result_builder.build_student_result` builds the student result only
from permitted fields. The stored automatic feedback is **never** forwarded.
The only exception is sanitized compiler diagnostics for a genuine compilation
failure, with the development label removed.

`GET /api/student/assessments/<id>/submission` → `result` (still `null`
until the teacher finalizes):

| Field | Source |
|---|---|
| `outcome` | GRADED / COMPILATION_FAILED / MANUAL_GRADE |
| `final_score`, `total_marks` | teacher-approved final, assessment total |
| `tests_passed`, `tests_run` | counts only (no test numbers or identifiers) |
| `test_marks`, `test_marks_available` | re-derived equal-weight test marks |
| `rubric_marks`, `rubric_marks_available` | approved rubric marks (see below) |
| `rubric_source`, `criteria[]` | see below; marks only, no AI explanations |
| `instructor_feedback` | the teacher's text |
| `compilation_diagnostics` | sanitized compiler output, compile failures only |
| `summary[]` | fixed sentences, e.g. "8 of 10 hidden tests passed." |

**Removed from the student response:** `automated_feedback`, the AI criterion
explanations, the suggested total, reliability flags, the verification
report, evidence provenance and failed test numbers.

**Rubric breakdown rule** (published marks can never disagree with the final
mark):

| `rubric_source` | When | Criteria shown |
|---|---|---|
| `TEACHER_APPROVED` | teacher-approved criterion marks exist | teacher marks |
| `AI_RECOMMENDATION_ACCEPTED_BY_TEACHER` | test marks + Σ AI marks = final | AI marks (numbers only) |
| `FINAL_TOTAL_ONLY` | otherwise | none; `rubric_marks` = final − test marks |

### 2.2 Redaction

Students no longer receive any AI-written text, so no hidden-value
substitution is applied to student responses. The over-redaction problem
disappears for students. The old helpers remain only for the development
harness.

### 2.3 Teacher view

`GET /api/submissions/<id>/evaluation` now also returns:
- `ai_criteria`, the AI recommendation (`criteria` is kept as an alias);
- `teacher_criteria`, the approved marks (an empty list until the migration);
- `verified_test_marks`;
- `criterion_approval_available`.

The full AI feedback, suggested score and reliability warnings stay visible to
the teacher.

### 2.4 Finalize

`POST /api/submissions/<id>/evaluation/finalize` accepts an optional
`criteria` field:

```json
{"final_score": "7.00",
 "instructor_feedback": "...",
 "criteria": [{"criterion_name": "Logic & Algorithm", "marks": "1.00", "comment": "d*d < n"},
              {"criterion_name": "Code Quality", "marks": "2.00"}]}
```

**Validation** happens in one transaction, with the existing ownership locks:
- every rubric criterion appears exactly once (no unknown names, no
  duplicates);
- each mark is between 0 and that criterion's maximum, with at most 2 decimal
  places;
- `final_score` = verified test marks + Σ criteria. There is no late-penalty
  exception, because no grading policy for one is documented.

**Requests without `criteria`** behave as before. They also gain one check:
`instructor_feedback` may not quote a hidden input (3 characters or more) or a
hidden expected output (6 characters or more); such a request gets 400.

**Response codes:** a criteria request is refused with 409 while the table
does not exist. An already finalized evaluation is still refused with 409, so
finalized grades are never changed. Other teachers get 404 and students get
403.

**AI rows are never modified**; the teacher's decisions go to their own rows.

## 3. Proposed schema change (needs approval)

One new table; nothing existing is altered. The scripts are:
- `migrations/2026_11_17_evaluation_criterion_approval_up.sql`
- `migrations/2026_11_17_evaluation_criterion_approval_down.sql`

Both were parse-checked (`SET PARSEONLY ON`) against FYP_E2E_Test and not
executed.

```sql
CREATE TABLE dbo.EvaluationCriterionApproval (
    approval_id     INT IDENTITY PRIMARY KEY,
    submission_id   INT NOT NULL REFERENCES dbo.Evaluation(submission_id),
    criterion_name  NVARCHAR(150) NOT NULL CHECK (LEN(LTRIM(RTRIM(criterion_name))) > 0),
    approved_marks  DECIMAL(5,2) NOT NULL CHECK (approved_marks >= 0),
    teacher_comment NVARCHAR(1000) NULL,
    approved_by     VARCHAR(50) NOT NULL REFERENCES dbo.Teacher_Export(teacher_id),
    approved_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    UNIQUE (submission_id, criterion_name)
);
```

**Why a new table** rather than new columns on `EvaluationCriterion`:
- the AI audit rows stay immutable;
- approvals also work for manual grades, which have no AI rows;
- you earlier declined changes to `EvaluationCriterion`.

## 4. Migration and rollback plan

| Gate | Action | Effect |
|---|---|---|
| M1 | Run the *up* script in **FYP_E2E_Test** | +1 empty table; no row changes |
| M2 | Verify there: approve criteria for a new test submission is impossible (all 3 are finalized, which is correct), so verify with unit tests + a read-only teacher view; optionally a new E2E run would need a 4th test student (separate approval) | none |
| M3 | Run the *up* script in **FYP_DB** after approval | +1 empty table |
| Rollback | *down* script: drops the table, and refuses while it holds approvals | app falls back to 409 for criteria requests |

- **Existing finalized evaluations:** they gain no approval rows and are
  unaffected. Their student breakdown follows the reconciliation rule, so G3
  submission 3 shows `FINAL_TOTAL_ONLY` with rubric marks 3.00.
- **Retroactive approvals:** there is deliberately no API to add criterion
  approvals to an evaluation that is already finalized.

## 5. Verification (2026-11-17)

- **Tests:** 436/436 pass, with 15 new ones in
  `tests/test_student_result_privacy.py` and updated fakes and assertions in
  the existing suites.
- **Live, read-only check** against the real G3 data in FYP_E2E_Test (test
  backend restarted with the new code):

  | Student | Result |
  |---|---|
  | E2E-TEST-0001 | 10.00, AI marks accepted (3.00 + 2.00) |
  | E2E-TEST-0002 | 7.00, `FINAL_TOTAL_ONLY`, rubric 3.00, no AI marks |
  | E2E-TEST-0003 | 0.00, compiler diagnostics with no label |

  No suggested total, flags, verification report, provenance or hidden data
  appeared. The teacher view still shows suggested 8.00, AI criteria 2.00 and
  2.00, and the FAILURES_NOT_EXPLAINED flag.
- **Databases:** the FYP_Database fingerprint is unchanged. FYP_E2E_Test is
  unchanged (same rows, and the approval table is absent).

## 6. Remaining risks

- **Teacher-written text is published as written.** It is checked for hidden
  values of 3 or more characters, but short values such as "4" can't be
  distinguished from ordinary numbers.
- **Less automated feedback for students.** They no longer see AI
  explanations. If useful AI feedback is wanted later, it needs a
  teacher-approved feedback field rather than forwarding AI text.
- **Late penalties are not part of reconciliation.** If a penalty policy is
  introduced, the rule `final = test + rubric` must be extended.
- **The approval table exists only after the migration.** Until then, teachers
  can set only a final total, and published breakdowns fall back to
  `FINAL_TOTAL_ONLY`.
