# Screen 6 — Section Details and multi-section flow

## Files
Created:
- src/pages/SectionDetails.jsx
- src/pages/SectionDetails.css
- src/components/SectionDetailsView.jsx
- src/services/sectionDetails.js
- src/services/assessmentFlow.js
Modified:
- src/App.jsx (protected section/details/students routes and shared flow transition)
- src/services/instructorDashboard.js (retains actual course_code in normalized section)
- src/pages/SelectSection.jsx (same-owner flow selection helper)
- src/pages/AssessmentNextStep.jsx (lists every selected stable ID)
Verification-only scripts, fixture previews, screenshots and baseline hashes are in .verification/.

## Ownership and details mapping
Dashboard links already use #/instructor/sections/{encoded section_id}; names are never keys. The page first calls the existing authenticated teacher-sections loader for CSC-101 / semester 1 / 2025SM through port 5002, then exactly matches the decoded section ID in that validated teacher-scoped response. Unmatched IDs show Section not available, including a section outside this teacher's current scope. No details, create action or roster are displayed for an unmatched section. Loading and backend failure have separate states and retry. JWT/role errors use the existing session-clear/login redirect.

The actual program + section_name form the display heading; semester_no, course_code, course_name and academic_session form the subtitle. enrolled_students supplies the Students statistic and link count. Missing student count is shown as a dash, not zero. Frontend membership checks supplement, and do not replace, backend authorization.

## Reliable section-scoped assessments (GET only)
The backend has no dedicated section-summary endpoint, but its existing owned-assessments and assigned-sections APIs provide reliable association data:
1. GET /api/assessments: validate response and teacher ownership; select the actual section's course.
2. GET /api/assessments/{assessment_id}/sections: validate each complete list and include the assessment only if its section_id matches.
3. Classify case-insensitive Assignment and Quiz values, then count/render only matched records.

Requests are batched with at most four section lookups in flight. No unrelated sections/courses contribute to counts. No partial count is shown when any lookup fails. A fully successful empty result is zero; backend/transport failure is an error with unavailable counts and retry. Unknown matched assessment types produce unavailable counts instead of inventing classification or zeros. Malformed payloads are rejected. Read fan-out scales with the teacher's assessment count for the course; a dedicated server-side section summary/list would improve efficiency.

Tabs use accessible native buttons with aria-pressed. They switch actual assigned Assignment vs Quiz records, titles and totals. The backend does not supply authoritative active/open submission status: the UI says Assignments/Quizzes and explicitly labels active submission status unavailable rather than calling all records active. It does not invent deadlines or statuses.

## Students
No secure teacher-authorized section roster/submission-status endpoint exists. The Students link opens #/instructor/sections/{id}/students, revalidates the teacher's section membership and displays an explicit future-screen placeholder. /api/students is not called. No student emails or private roster data are exposed.

## Creation flow
Section-specific Create initializes exactly [current section_id] with the verified owner, replacing any older selection, and opens the existing next-step placeholder. Dashboard -> Select Section -> Continue keeps every selected ID. createAssessmentFlow copies and validates the array; getFlowSectionIds returns data only to the same owner. The placeholder lists every ID, not only a count or first entry. Back to Select Section initializes all current valid selections. One flow targets multiple sections, not multiple forms.

The existing session-generation-keyed InstructorRoutes tree still clears the entire in-memory flow and page caches on logout/session replacement. No storage persistence is introduced. No assessment is created and POST /api/assessments/{id}/sections is NOT called. Future creation must first obtain a real assessment_id, then call that existing endpoint with {section_ids:[...]}; it remains outside Screen 6.

## Validation
Production build and ESLint pass. Service/flow unit checks pass for exact IDs, unknown owner, section/course joins, confirmed zero vs error, unknown classification, missing token, single/multiple ID copying and cross-owner isolation. Component browser checks pass for tabs, error/loading/not-authorized states, single-section Create callback, roster placeholder, two-ID summary and hiding another owner's summary. No successful login was fabricated. Tests use explicit isolated fixtures outside production; screenshots depict component-only preview data, not live instructor records.

All five widths (1440,1280,768,390,375) pass: single column, desktop sidebar/mobile navigation, correct content order and no horizontal overflow/navigation overlap. Built-app regression checks cover Welcome/Login interactions and protection/Back rejection for prior routes, section details and roster placeholders. All 257 tracked backend files match the pre-task hash baseline; Vite's port-5002 target is unchanged. No SQL operations, write requests, installs or commits were performed.

Actual BIIT235 access to SEC000001/SEC000004 and real enrolled counts require interactive instructor login and remain unverified by this harness. Logout and real cross-account switching likewise require the user's own credentials in the form; none were requested or read. Previous session-teardown and metadata-isolation logic is preserved.

## Backend follow-up for Claude Code
- An authoritative section assessment summary/list with type guarantees and active/open status would remove the read fan-out and enable the mockup's Active Assignments semantics.
- A secure JWT teacher-owned section roster with per-assessment submission-status contract is required for the future Students screen.
- No backend implementation or connection change was made here.
