# Criterion-override E2E test: database-write plan (FYP_E2E_Test only)

Status: **NOTHING WRITTEN.** Prepared 2026-11-17. Every write gate (O1–O4)
needs explicit owner approval. FYP_Database is never written to. Submissions
2, 3 and 4 are never touched, and M3 and G4 are out of scope.

## 1. Verified state (read-only)

| Check | Result |
|---|---|
| Database | `FYP_E2E_Test`; every Python session is on FYP_E2E_Test; the backend reports `active_database = FYP_E2E_Test` with execution disabled |
| `E2E-TEST-0004`, `E2E-ENR-0004`, `e2e.test4@e2e.test` | none exist |
| Schema fit | `Student_Export.student_id` (max 14) and `Submission.student_id` (max 14): 13 characters; `student_email` (max 30): 18; `enrollment_id` (max 20): 12; `student_name` (max 77): 18 |
| Prerequisites | AssessmentSection (5, SEC000001) exists; SectionCourseTeacher (SEC000001, BIIT235, CSC-101) exists; Assessment 5 is open until 2026-12-15 with tests 5.00 and rubric 5.00 |
| Rubric | Logic & Algorithm 3.00, Code Quality 2.00 |
| Approval table | `dbo.EvaluationCriterionApproval` exists (M1), with 0 rows |
| Fixture | `prime_buggy_square` sha256 `d52fba8b…3bdd` = approved |
| Recorded evidence | the report hash is pinned; it re-verifies against the 10 tests in the database as COMPILED, 8/10, tests 6 and 9 failed, test marks 4.00 |
| Existing data | FYP_E2E_Test is unchanged since M1 (full row hashes of 18 tables); the FYP_Database fingerprint matches its baseline |

**Workflow support:** the existing APIs and tools cover every step, and nothing
in the application code changes for this test.
- Student: login, view, submit.
- Grading: the fixture grader's plan, then grade.
- Teacher: review, then finalize with `criteria`.
- Student: view the result.

Only two development tools gained small options:
- `e2e_test_db.py add-student` / `cleanup-student`;
- `e2e_api_client.py finalize --criterion NAME=MARKS`, with E2E-TEST-0004 allowed to submit `prime_buggy_square` only.

Tests: 442/442 pass.

## 2. Exact writes

### O1: test student and enrollment (one transaction)

`python dev_tools/e2e_test_db.py add-student --confirm FYP_E2E_Test`

The password is typed twice at a hidden prompt and bound as a parameter. Before
writing, the tool checks:
- `DB_NAME()` = FYP_E2E_Test;
- the student, email and enrollment do not exist;
- (5, SEC000001) is assigned, and BIIT235 teaches it.

```sql
INSERT INTO dbo.Student_Export (student_id, student_name, student_email,
    program, semester, section, enrollment_status, student_password, section_id)
VALUES ('E2E-TEST-0004', 'E2E Test Student 4', 'e2e.test4@e2e.test',
        'BSCS', '2025SM', 'A', 'ENROLLED', ?<hidden>, 'SEC000001');   -- 1 row

INSERT INTO dbo.Enrollment_Export (enrollment_id, student_id, section_id,
    course_no, enrollment_year, teacher_id)
VALUES ('E2E-ENR-0004', 'E2E-TEST-0004', 'SEC000001', 'CSC-101', 2025, 'BIIT235'); -- 1 row
```

**Relationships:**
- `Enrollment_Export` has no foreign keys.
- The later `Submission.student_id` → `Student_Export` (this row).
- Access comes from Enrollment (E2E-TEST-0004, SEC000001, CSC-101) +
  SectionCourseTeacher + AssessmentSection 2.
- No existing row is updated.

### O2: submission (student API, one transaction)

`e2e_api_client.py --student E2E-TEST-0004 submit prime_buggy_square`:
- writes `Submission` +1 (expected id 5) and `GradingJob` +1 (expected id 4, PENDING);
- then `duplicate` is expected to return 409, with no write.

### O3: grading (fixture grader, recorded evidence, real Ollama)

1. `fixture_submission_grader.py --submission-id 5` (plan only).
2. `fixture_submission_grader.py --submission-id 5 --grade --confirm-job 4`.

Expected writes:
- the job is claimed, then COMPLETED;
- `Evaluation` +1: suggested = 4.00 + the AI rubric marks, probably NEEDS_REVIEW;
- `EvaluationCriterion` +2: the AI's marks.

There is no compilation or execution.

### O4: teacher finalization with criterion override (teacher API)

```bash
e2e_api_client.py finalize 5 7.00 --criterion "Logic & Algorithm=1.00" --criterion "Code Quality=2.00" --feedback "<your wording>"
```

The API checks:
- both rubric criteria are present, each within its maximum;
- 4.00 (verified test marks) + 1.00 + 2.00 = 7.00.

Expected writes, in one transaction:
- `Evaluation` 5 updated: final 7.00, FINALIZED;
- `EvaluationCriterionApproval` +2, with `approved_by` BIIT235;
- the AI rows are unchanged.

**Verification (read-only):**
- **Student result:** `student-result --student E2E-TEST-0004` must show:
  - `rubric_source` TEACHER_APPROVED;
  - criteria Logic & Algorithm 1.00/3 and Code Quality 2.00/2;
  - test marks 4.00 and final 7.00;
  - no AI marks, suggested total, flags or hidden data.
- **Teacher view:** shows `ai_criteria` (the AI's actual marks) alongside
  `teacher_criteria`.
- **Isolation:** submissions 2–4 and their results are unchanged.

**Total expected new rows in FYP_E2E_Test:**

| Table | New rows |
|---|---|
| Student_Export | 1 |
| Enrollment_Export | 1 |
| Submission | 1 |
| GradingJob | 1 |
| Evaluation | 1 |
| EvaluationCriterion | 2 |
| EvaluationCriterionApproval | 2 |

## 3. Logins (typed by you, hidden)

- **Tokens:** the existing teacher and E2E-TEST-0001..0003 tokens may have
  expired; re-run the login scripts if needed.
- **New login:** `fixtures/student_login.py dev_tools/.tokens/E2E-TEST-0004.token E2E-TEST-0004`.

## 4. Rollback and cleanup

| Situation | Action |
|---|---|
| Failure inside O1, O2 or O4 | the transaction rolls back automatically; nothing is left |
| Ollama down or verification failure in O3 | the job is released (PENDING, attempt 1 of 3) and no Evaluation is written; retry once the cause is fixed |
| Remove the fourth student's data afterwards | `e2e_test_db.py cleanup-student --confirm FYP_E2E_Test` (dry run: counts only), then add `--execute` (needs approval) |

**What `cleanup-student --execute` does:**
- deletes children first, in one transaction: approvals → criteria →
  Evaluation → GradingJob → Submission → Enrollment → Student;
- deletes only rows of E2E-TEST-0004 / E2E-ENR-0004;
- checks every row count against the dry-run counts, and rolls back on any
  difference;
- refuses if any affected submission is 2, 3 or 4, and refuses for the three
  seeded students.

**Whole test database:** G4 (drop FYP_E2E_Test) remains a separate, unapproved
gate.

**Irreversible effects (test database only):**
- identity counters advance;
- the finalization and approvals cannot be changed through the API (409 by
  design), only removed by the cleanup above.
