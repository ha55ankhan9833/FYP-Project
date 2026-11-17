# End-to-end student grading test: execution plan (Option B, isolated database)

Status: **NOTHING CREATED**. Each gate (G1–G4) waits for explicit owner
authorization. Prepared 2026-11-17. FYP_Database fingerprint baseline:
`fyp_baseline_before_e2e_db.json`, unchanged after all preparation work.

## 1. Authentication and enrollment schema (verified read-only)

| Purpose | Table and columns actually used by the code |
|---|---|
| Student login (`/api/student/login`) | `Student_Export.student_email`, `student_password` (plain-text compare); JWT identity = `student_id` (varchar(14), PK) |
| Teacher login (`/api/teacher/login`) | `Teacher_Export.teacher_email`, `teacher_password`; identity = `teacher_id` (PK) |
| Section assignment | `Assessment.teacher_id/course_code` + `Section_Export.section_id` + `SectionCourseTeacher(section_id, teacher_id, course_no)` |
| Student sees and submits | `AssessmentSection(assessment_id, section_id)` joined to `Enrollment_Export(student_id, section_id, course_no)` and `SectionCourseTeacher(section_id, course_no = Assessment.course_code, teacher_id = Assessment.teacher_id)` |
| Not used for authorization | `Student_Export.section_id/section/program/semester/enrollment_status`, `Enrollment_Export.teacher_id/enrollment_year` |

`db.py` is the only place that opens a connection.

**Keys:**
- PK `Student_Export(student_id)` and PK `Enrollment_Export(enrollment_id)`. There is no unique email key, and `Enrollment_Export` has no foreign keys.
- `SectionCourseTeacher.allocation_id` is an identity column.

**Foreign keys relevant here** (all NO ACTION; there are no triggers, views or procedures):
- `Submission.student_id` → `Student_Export`
- `Submission.assessment_section_id` → `AssessmentSection`
- `AssessmentSection.assessment_id` → `Assessment`
- `AssessmentSection.section_id` → `Section_Export`
- `Assessment.teacher_id` → `Teacher_Export`
- `TestCase.assessment_id` → `Assessment`
- `AssessmentRubricCriterion.assessment_id` → `Assessment`
- `GradingJob.submission_id` → `Submission`
- `Evaluation.submission_id` → `Submission`
- `EvaluationCriterion.submission_id` → `Evaluation`
- `Deduction.submission_id` → `Evaluation`
- `Notification` → `Student_Export`, `Teacher_Export`, `Assessment`

## 2. Recommended: a separate test database, FYP_E2E_Test

The server is SQL Server 2025 Developer Edition, and your Windows login is
sysadmin, so a separate database is possible.

**G1 creates the test database as a schema-only clone:**

```sql
DBCC CLONEDATABASE (FYP_Database, FYP_E2E_Test) WITH NO_STATISTICS, NO_QUERYSTORE;
ALTER DATABASE [FYP_E2E_Test] SET READ_WRITE WITH ROLLBACK IMMEDIATE;
ALTER DATABASE [FYP_E2E_Test] SET RECOVERY SIMPLE;
```

- **Identical schema:** same tables, columns, collations, PKs, unique keys, FKs, CHECK and DEFAULT constraints, and identities.
- **No data:** no rows, and `NO_STATISTICS` means no data samples in histograms either.
- **Source untouched:** the clone only reads FYP_Database, through a brief internal snapshot.
- **Self-verifying:** the tool refuses to continue unless the clone is READ_WRITE and every table is empty.

The application code and its authorization rules are unchanged. The backend
is started against the test database by `dev_tools/run_e2e_backend.py`,
which selects the database in-process. It refuses to start unless it is
connected to FYP_E2E_Test, the database holds only E2E-TEST students, and the
execution gate is off. A normal `python app.py` always uses FYP_Database.
Environment variables cannot switch it.

**Write guards:**
- `fixture_submission_grader.py` works only on FYP_E2E_Test, and refuses `--grade` unless `SELECT DB_NAME()` is FYP_E2E_Test.
- `e2e_api_client.py` refuses every step unless the running backend reports `active_database = FYP_E2E_Test` and student execution is disabled.

## 3. G2: exact INSERT statements (all in FYP_E2E_Test, one transaction)

Run by `python dev_tools/e2e_test_db.py seed --confirm FYP_E2E_Test`.

- **Expected row counts:** every statement is checked, and any difference rolls back the whole transaction.
- **Source reads only:** values come from FYP_Database through SELECT and never pass through Python or chat.
- **Passwords:** bound as parameters from hidden prompts, each entered twice.

| # | Statement (abridged; full text: `e2e_test_db.py plan`) | Rows |
|---|---|---|
| 1 | `INSERT dbo.Teacher_Export (...) SELECT teacher_id, teacher_name, teacher_email, ?<test-only password>, department FROM FYP_Database.dbo.Teacher_Export WHERE teacher_id='BIIT235'` | 1 |
| 2 | `INSERT dbo.Course_Export SELECT ... WHERE course_no='CSC-101'` | 1 |
| 3 | `INSERT dbo.Section_Export SELECT ... WHERE section_id='SEC000001'` | 1 |
| 4 | `INSERT dbo.SectionCourseTeacher (section_id, teacher_id, course_no, actual_semester) SELECT ... WHERE ('SEC000001','BIIT235','CSC-101')` | 1 |
| 5 | `SET IDENTITY_INSERT dbo.Assessment ON` / `INSERT ... SELECT * columns ... WHERE assessment_id=5` / `OFF` (keeps id 5) | 1 |
| 6 | Same pattern for `AssessmentRubricCriterion` (ids kept) | 2 |
| 7 | Same pattern for `TestCase` (ids 13–17, 22, 24–27 kept; all hidden) | 10 |
| 8–10 | `INSERT dbo.Student_Export (student_id, student_name, student_email, program, semester, section, enrollment_status, student_password, section_id) VALUES ('E2E-TEST-000n', 'E2E Test Student n', 'e2e.testn@e2e.test', 'BSCS', '2025SM', 'A', 'ENROLLED', ?<hidden>, 'SEC000001')` | 3 |
| 11–13 | `INSERT dbo.Enrollment_Export (enrollment_id, student_id, section_id, course_no, enrollment_year, teacher_id) VALUES ('E2E-ENR-000n', 'E2E-TEST-000n', 'SEC000001', 'CSC-101', 2025, 'BIIT235')` | 3 |

**Foreign keys these rows satisfy:**
- `Assessment.teacher_id` → `Teacher_Export` (row 1)
- `TestCase` and `AssessmentRubricCriterion` → `Assessment` 5 (row 5)
- `AssessmentSection` (created later through the API) → `Assessment` 5 and `Section_Export` SEC000001 (row 3)
- `Submission.student_id` → `Student_Export` E2E-TEST-000n (rows 8–10)

**Teacher password:** BIIT235's real password is **not** copied. You set a
test-only password for BIIT235 at the prompt.

**Authorization check (`e2e_test_db.py verify`):** this runs the same
predicates as `student_submission_service` for each test identity. Expected for
each of the three: `can_log_in`, `enrolled_in_SEC000001_CSC-101` and
`assessment_teacher_teaches_section` all true. It also confirms:
- `real_students_in_test_db` is 0;
- the test DB's 10 test cases are identical to FYP_Database's;
- the FYP_Database fingerprint is unchanged.

After the section is assigned (G3), the normal API eligibility checks are what
let each student see and submit.

## 4. What is guaranteed not to change

- **FYP_Database receives no INSERT, UPDATE or DELETE** at any gate. The
  student, teacher, course, section, enrollment, assessment and grading
  tables are all fingerprinted (every row hashed) before G1 and after every
  gate.
- **Not touched at all:** Assessments 2 and 4, BIITDBNew and all real
  passwords. Production code paths are unchanged apart from the
  database-selection function and the `active_database` field in the
  teacher-only `/api/grading/status`.

## 5. Keeping the 47 real students out

The test database contains **no real students**, so none of the 47 SEC000001
students can log in to the test backend at all. In FYP_Database, Assessment 5
stays unassigned, so they see nothing there either.

Only one backend can listen on port 5000. During the run, the normal backend is
stopped and the E2E backend runs on 127.0.0.1 only.

## 6. Passwords

- **Prompts:** typed by you at hidden `getpass` prompts, each entered twice. This covers 1 test-only teacher password (in `seed`) and the student logins (`fixtures/student_login.py`).
- **Storage:** bound as SQL parameters. Never printed, logged, hard-coded or written to a file.
- **Tokens:** written only to `dev_tools/.tokens/` (gitignored) and never displayed.
- **Unit tests:** they verify that no password appears in any SQL text or output.

## 7. Gates and run order

| Gate | Command | Writes |
|---|---|---|
| G1 | `python dev_tools/e2e_test_db.py create --confirm FYP_E2E_Test` | creates database FYP_E2E_Test (empty clone) |
| G2 | `python dev_tools/e2e_test_db.py seed --confirm FYP_E2E_Test`, then `verify --baseline <file>` | §3 rows in FYP_E2E_Test |
| G3 | E2E run (below) | test-DB rows only: 1 AssessmentSection, 3 Submission, 3 GradingJob, 3 Evaluation, 4 EvaluationCriterion, then final scores |
| G4 | `python dev_tools/e2e_test_db.py drop --confirm FYP_E2E_Test` | drops the test database |

**G3 run** (each step through the real API or normal services):
1. `python dev_tools/run_e2e_backend.py` (normal backend stopped).
2. You log in BIIT235 with `fixtures/teacher_login.py`, using the test-only password.
3. `e2e_api_client.py assign` assigns SEC000001 in the test DB.
4. For each test student:
   1. You log in with `fixtures/student_login.py`.
   2. `student-view`, then `submit <fixture>`, then `duplicate <fixture>` (expects 409).

   The fixtures are: E2E-TEST-0001 → `prime_correct`, E2E-TEST-0002 →
   `prime_buggy_square`, E2E-TEST-0003 → `prime_compile_error`.
5. For each submission, run `fixture_submission_grader.py --submission-id N`
   (plan), then `--grade --confirm-job J`.
   - It uses the recorded evidence, with no compilation or execution.
   - Ollama is called for the correct and buggy fixtures only.
6. `teacher-list` and `teacher-view N`. You review the warnings, choose each
   final mark, and run `finalize N <score>`.
7. `student-result` for each student shows only the finalized result, and the
   hidden-data scan must be empty.

**Expected grading:**

| Student | Fixture | Result |
|---|---|---|
| E2E-TEST-0001 | `prime_correct` | 10/10 tests, test marks 5.00 + rubric |
| E2E-TEST-0002 | `prime_buggy_square` | 8/10 tests, 4.00 + rubric, NEEDS_REVIEW |
| E2E-TEST-0003 | `prime_compile_error` | COMPILATION_FAILED, 0, Ollama not called |

## 8. Cleanup and rollback

- **During G2:** any failure rolls back the whole transaction. If you want to start over, run G4 and then G1 again.
- **After the run (G4):** dropping FYP_E2E_Test removes every test row at once. FYP_Database is unaffected, so there is nothing to clean up there.
- **Irreversible:** only the drop itself, which deletes test data only. If you want to keep the evidence, the test DB can be kept until you decide.
- **Backups:** existing backups of FYP_Database are not affected. Its recovery model stays FULL; only the clone is set to SIMPLE.

## 9. Fallback if a separate database is not wanted

In FYP_Database itself, create a dedicated test section instead of using
SEC000001:
- new rows: `Section_Export` 'E2E-SEC-01', `SectionCourseTeacher` (E2E-SEC-01, BIIT235, CSC-101), 3 `Student_Export` and 3 `Enrollment_Export` rows;
- assign Assessment 5 only to E2E-SEC-01.

The 47 students then never see it, because they aren't in that section. This
adds rows to real export tables, and its cleanup is a scoped DELETE script, so
it is not recommended over §2.
