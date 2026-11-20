# Secure publication implementation report — 2026-11-20

## Existing behavior retained

AssessmentSection is the publication mechanism. Teacher/student login already exists.
The evaluation module already supplies the student assessment listing and submission/result
responses; the existing submission transaction independently checks student JWT identity,
section/course enrollment, teacher allocation, duplicate submissions and deadlines.
No separate publish flag, database migration, frontend change or execution path was introduced.

## Changed files

- app.py — registers the student detail blueprint.
- assessment_publication_policy.py (new) — shared publication validation.
- assessment_section_service.py — atomic authorized publishing, safe retries, confirmed response metadata.
- assessment_section_routes.py — explicit safe error codes and 200 replay/201 created handling.
- assessment_readiness_service.py — the same publication policy powers ready_to_publish.
- student_assessment_service.py (new) — allowlisted, enrollment-scoped read service.
- student_assessment_routes.py (new) — authenticated student assessment detail.
- evaluation_review_service.py — retains the existing student list and adds stored type/student/section checks.
- evaluation_routes.py — rejects identity overrides/query parameters on that existing list.
- docs/API_CONTRACT.md — exact publishing/student contracts, retry rules and limitations.
- tests/test_secure_publication.py (new) — security and visibility regression coverage.
- tests/test_assessment_section_service.py, tests/test_generate_click_flow.py,
  tests/test_new_assessment_payloads.py, tests/test_e2e_student_grading_flow.py,
  tests/e2e_world.py — updated fixtures/contracts for stronger guards and idempotent retries.
- dev_tools/verify_publication_e2e.py (new) — explicit rollback-only isolated SQL verification.

Pre-existing untracked docs and fixtures were left alone. No automatic commit was made.

## API mapping

Teacher JWT: POST /api/assessments/{id}/sections with only {"section_ids":[...]}.
201 when assignments are inserted; 200 replay when all requested rows already exist.
Returns title/type/course/marks/deadline plus requested IDs, confirmed full published IDs,
created and unchanged assignment rows, publishing_status and replayed.
All requested sections are authorized before inserts; the assessment and authorization
ranges are locked. Any ordinary failure rolls the transaction back. After an ambiguous
connection/commit error, read confirmed assigned sections before retrying.

Student JWT: existing GET /api/student/assessments; new
GET /api/student/assessments/{id}. The list retains has_submitted/open_for_submissions.
Both require published section/course enrollment and current teacher allocation.
Detail denies unpublished, unrelated and guessed IDs with the same 404.
No saved tests, expected outputs, reference source, raw drafts or teacher rubric data are selected.
Teacher-only APIs remain inaccessible to student JWTs. Assignment and Quiz remain distinct.

## Verification

- Existing SQL metadata read only: stored types are Assignment and Quiz; no assessment-level
  semester/session columns exist. Section IDs bind Section_Export session records and course
  allocation. No client-supplied teacher/session is used to grant access.
- Full isolated suite: 1,013 tests run, OK, 18 skipped (unittest discover -s tests -p test_*.py -q).
- Focused publication/submission/privacy regressions passed.
- Python compile/import checks, unique Flask route registration, and git diff --check passed.
- Actual FYP_E2E_Test transaction: existing ready unpublished assessment and existing enrolled
  student used; unpublished detail denied, selected-section visibility allowed, type retained,
  duplicate publication replayed safely, no hidden-answer fields returned.
- Service commits were suppressed only by the explicit test wrapper; outer rollback discarded
  every SQL test change. Final Assessment, AssessmentSection, TestCase, Student_Export,
  Submission and AssessmentRubricCriterion counts matched the initial counts.
- No production writes, student/teacher creation, credential retrieval, migrations, grading,
  publishing to production or arbitrary C++ execution occurred.

## Limits and deployment state

The E2E copy contains only one allocated enrolled section and no outsider student/unrelated
section. Multiple-section and unauthorized/outsider scenarios pass isolated tests but remain
unverified against real SQL records; no students/allocations were fabricated to fill this gap.
The SQL check is rollback-only, not a concurrent or committed live HTTP publishing test.

Port 5002 currently returns 401 AUTH_REQUIRED for anonymous existing endpoints. It still
returns 404 NOT_FOUND for the new student detail path, so that running process must be
restarted to load these source changes. It was not restarted or used for authenticated writes.

Frontend publish wiring, the Figma success screen and the student portal UI remain outside
this backend-only task. TestCase has no persisted per-row verification attestation; output
origin relies on the existing secure generation/save paths, while publication revalidates
stored inputs/marks and requires outputs. Rubric approval uses the existing persisted criterion
rows rather than a separate approval flag. These schema limits were documented, not hidden.
