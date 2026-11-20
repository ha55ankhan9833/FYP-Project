# Screen 8 — Create Assignment / Quiz, Basic Details

## Files
Created:
- src/pages/AssessmentBasicDetails.jsx
- src/pages/AssessmentBasicDetails.css
- src/components/AssessmentBasicForm.jsx
- src/services/assessmentBasicDetails.js
- src/pages/ConstraintsPlaceholder.jsx
Modified: src/App.jsx (Step 1/Step 2 placeholder routing and session-local basicDraft state).
The old AssessmentNextStep placeholder file is retained but replaced at its route by the real Step 1 screen. Verification-only scripts/previews/screenshots are in .verification/.

## Routes, section scope and ownership
Section Details and Combined Sections Create buttons already open #/instructor/assessments/new/details with the complete owner-bound selection flow. That route now renders Step 1. It reloads the authenticated teacher-sections list, resolves every selected ID and requires one confirmed shared course before rendering the form. Missing, stale or unassigned selections redirect to Select Section; no unassigned draft is created. Course code/name come from those real normalized sections, not the mockup's example. The read-only field, subtitle and expandable selected-section summary show that context.

Back returns to the existing single/combined section dashboard. All selected IDs remain in assessmentFlow. Continue validates, preserves fields/course metadata and opens #/instructor/assessments/new/constraints, a clearly labeled Step 2 placeholder only. No Constraints UI or approval workflow is implemented.

## Draft preservation
InstructorRoutes now owns basicDraft alongside assessmentFlow. It stores only verified owner ID, field values and confirmed course code/name, in React memory. Every change updates the controlled form's shared draft, so incomplete input survives Back as well as valid input surviving Continue/Back/Forward. Assignment/Quiz changes replace only type. Textareas retain whitespace/newlines; marks remain the exact entered string with no numeric rounding or forced default.

Discard draft clears both basicDraft and assessmentFlow and returns to Select Section. Existing logout/session-generation changes unmount the whole instructor route tree, clearing both states and page caches. Another owner receives empty defaults. No browser storage or backend draft is introduced. A full refresh or leaving the instructor route tree loses the in-memory draft; it is not advertised as a saved server draft.

## Backend findings and field mapping
POST /api/assessments requires course_code, assessment_type, title, problem_statement, input_format, output_format, total_marks, deadline_at, validation_percentage and a finalized teacher-owned draft_id. Direct value/code-constraint submission is rejected. Later section assignment must happen only after successful creation returns assessment_id, using POST /api/assessments/{id}/sections with all selected section_ids. Neither POST is called now.

UI type uses ASSIGNMENT / QUIZ locally. Actual assessment_type is free text: Flask checks text and stores the stripped value, without a documented enum, canonical capitalization or reliable enforced type distinction. No database type or supported-save claim is invented. Confirm the persisted type convention before any future submission.

UI mappings for future integration:
- course.code -> course_code
- type -> assessment_type after canonical contract confirmation
- title -> title (60-character UI maximum)
- totalMarks -> total_marks (backend Decimal parsing)
- problemStatement -> problem_statement on assessment creation; problem on constraint-draft creation
- inputFormat -> input_format
- outputFormat -> output_format
- sampleInput/sampleOutput -> frontend-only fields: creation has no confirmed sample persistence fields.

GET/read sections is the only API use from Step 1. POST /api/ai/drafts itself saves a backend review draft and calls the AI service; it is not called. Its inputs are problem, input_format and output_format, each capped at 10,000 characters. Finalization/approval is required before assessment creation. Later steps must obtain real deadline/validation percentage and rubric configuration; none is guessed here.

Flask creation requires non-empty input/output formats even though the Figma does not mark them required. The frontend adds required indicators and validation accordingly. Samples have no creation requirement and stay optional/local. The API currently strips outer whitespace from statement/input/output when eventually creating an assessment; Step 1 preserves input exactly and does not claim backend round-trip preservation.

## Validation
Title is required, no more than 60 characters, with a live counter. Problem statement and input/output formats are required; the constraint-analysis flow's 10k per-field limit is validated without truncating text. Marks must be a finite positive standard numeric value, with no mandatory 100-mark default and no invented 2-decimal total cap. Backend grading allocates test marks at 0.01 precision; rubric criteria/final marks have separate 2-decimal constraints. The backend creation route does not document a total-mark maximum/scale, so storage-range confirmation is still needed before real persistence.

All labels are associated, errors inline, first invalid field focused, toggle buttons keyboard accessible, focus visible. Enter in single-line fields is prevented from implicitly submitting; textarea Enter remains newline and explicit button Enter still works. Read-only course cannot be changed to an unsupported course.

## Verification
Production Vite build and ESLint pass. Unit checks cover both modes, all required fields, title length, numeric marks, analysis limits, optional samples, whitespace and owner-isolated draft retrieval. Browser checks pass at 1440,1280,768,390,375px with a 780px desktop form, mobile navigation and no horizontal overflow. Tests cover type toggling without data loss, maxLength, inline errors, implicit Enter prevention, literal multiline/sample values, Step 2 placeholder, Back/Forward and explicit discard; no write requests occur.

Screenshots are blank isolated UI previews with no authenticated course data or hardcoded title/marks/sample/course records. Long-form captures use a full-height viewport so fixed navigation appears at the image bottom; separate original-height scrolling checks confirm the actual bottom action clears navigation.

Built-app auth/Back/Welcome/Login route regressions pass, including Step 1 and Step 2 placeholder. Existing real-roster mapping/error/search tests still pass. All 258 tracked Backend files match pre-task hashes; the port-5002 proxy is unchanged. No backend/SQL/model modifications, installs, commits or writes were performed.

Live authenticated Step 1 course/section rendering and cross-account draft clearing remain for interactive user verification; isolated tests do not fabricate a successful login or backend records. The prior real-roster account count targets also remain unconfirmed by the user.
