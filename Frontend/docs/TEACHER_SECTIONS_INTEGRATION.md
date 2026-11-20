# Real teacher-sections integration

This supersedes the missing-endpoint notes in SCREEN3_INTEGRATION.md and SCREEN4_INTEGRATION.md.

## Shared read-only request
Both InstructorDashboard and SelectSection call loadAssignedSections in src/services/instructorDashboard.js with the verified /api/auth/me user_id. It obtains the existing session token and calls the shared apiRequest client:

GET /api/teacher/sections?course_code=CSC-101&semester_no=1&academic_session=2025SM
Authorization: Bearer (existing session token)

No teacher_id parameter, request body or write method is sent. Vite still proxies /api to http://127.0.0.1:5002. These three values are the workflow scope provided by the user, not invented teacher/section data. This is a complete list for that scope, not all of an instructor's courses or academic sessions. Future filter selection should update the single shared TEACHER_SECTION_FILTERS configuration under an agreed UI workflow.

## Mapping
The response teacher.teacher_id must match the server-verified user_id. Every section's teacher_id must match that owner; rows with inconsistent course, semester or session are rejected. Echoed filters and total must match the request/array. Duplicate section IDs are rejected. Invalid data never becomes an empty list.

teacher.teacher_name -> dashboard greeting (verified login metadata remains the loading/failure fallback).
sections[].section_id -> React key, checkbox value and local selected ID.
program + section_name -> BCS-A-style display label, using actual response values only.
enrolled_students -> actual course-specific enrolled count; missing counts remain unavailable, never assumed zero.
semester_no -> Semester label.
academic_session -> session code displayed verbatim, without invented season labels.
course_name (or actual course_code) -> dashboard course badge.
sections.length -> assigned-section heading count for this scope.

## States and selection
Loading indicators stay in place while GET runs. A validated 200 response with [] becomes the confirmed empty state. Transport/server/unsupported-response failures show error/unavailable with Retry, not an empty roster. Missing token, HTTP 401 or invalid-session 422 clear the session and redirect through the existing login handling. Teacher-only/owner failures are rejected before rendering records.

Selection behavior is unchanged: native multi-select checkboxes, valid IDs only, Continue disabled without a current valid selection, IDs preserved in React flow memory for the same instructor and carried to the temporary next-step placeholder. No assessment or section-assignment POST exists in this flow.

## Files changed
- src/services/instructorDashboard.js
- src/pages/InstructorDashboard.jsx
- src/pages/SelectSection.jsx
- src/components/InstructorSections.jsx
- src/components/SectionSelectionForm.jsx

No CSS, authentication implementation, Vite proxy, backend or SQL configuration was changed.

## Automated checks
Build and ESLint pass. Isolated tests verify GET-only shape, exact filters, Bearer presence without printing it, absence of teacher_id/body, missing/expired/invalid sessions, backend/network failures, confirmed-empty normalization, owner mismatch, malformed totals and filter mismatch. No populated section records or successful authentication responses are fabricated. A real unauthenticated GET through the configured Vite proxy reached port 5002 and returned HTTP 401 AUTH_REQUIRED. All 257 tracked backend files matched the pre-integration hashes.

Live BIIT235 login, populated records and selection-to-Continue validation are being tested interactively by the user; no password or JWT is requested in chat or diagnostic output.
