# Students List — real roster API integration

This supersedes the missing-roster limitation in SCREEN7_SECTION_STUDENTS.md.

## Files changed
- src/services/sectionRoster.js
- src/pages/SectionStudents.jsx
- src/components/SectionStudentsView.jsx
No production CSS, routes, authentication implementation, proxy or backend files changed. Tests/previews/screenshots remain under .verification/.

## Request and authorization
The existing student-list and student-placeholder routes now call:
GET /api/teacher/sections/{encoded section_id}/students?course_code=CSC-101&semester_no=1&academic_session=2025SM
using the shared apiRequest client and current Bearer token. No teacher_id or request body is sent. The global /api/students endpoint is never used.

The new backend endpoint authorizes the JWT teacher's section/course allocation before returning metadata or records, so the roster response replaces the previous redundant teacher-sections precheck and is authoritative. The frontend validates response section_id, course, semester, session, total, unique student IDs and every student section_id. A mismatched or partial response becomes an error; no partial/wrong-section roster is rendered. Frontend validation supplements backend authorization and is not its replacement.

## Exact field mapping
students[].student_id -> stable card key, selected student route ID.
students[].student_name -> full name and generated initials; a documented null name displays Name unavailable and '?' without dropping the enrolled record.
students[].registration_no -> registration/ARID display and search. The new contract explicitly confirms it aliases student_id in this database; the frontend uses registration_no directly rather than guessing.
students[].enrollment_status -> retained normalized status, without inventing attendance/submission/grade fields or filtering students out.
students[].section_id -> exact scope check.
total -> fixed Students (N) heading via normalized section.studentCount, independent of search.
section.section_name/program/course_name/course_code/semester_no/academic_session -> real subtitle/context.

The response is a complete unpaginated roster. Total must equal returned array length; duplicate/out-of-section students are rejected. Only normalized allowed fields are passed to the view; emails/passwords/extra fields are not retained. No roster data enters persistent storage.

## UI, search and navigation
Existing Figma-inspired cards, initials palette, search, count, sidebar/bottom navigation and responsiveness are preserved. Every returned student renders. Search filters locally by name and registration, case-insensitively; names normalize repeated/outer whitespace, registration searches ignore whitespace. Clear restores all records; no matches has its own message. Filtering never changes the enrolled heading.

Back returns to the original section, including error states. Card links preserve section_id/student_id and open the existing explicit Student Details placeholder; it reloads the authorized roster before displaying a matched student and never invents marks or performance data.

## Errors and session lifecycle
- Missing token, HTTP 401, invalid-session 422: clear session and return to Instructor Login.
- 403 SECTION_ACCESS_DENIED: Access denied; no student data shown and a valid instructor session is not unnecessarily logged out.
- 403 TEACHER_ONLY: clear invalid-role session and return to login.
- 404: Section not found in current course/term.
- 500 ROSTER_UNAVAILABLE, network or malformed response: unavailable/error with Retry, never an empty roster.
- Valid 200 with total=0/students=[]: No Students Enrolled.

Requests are aborted/ignored on page cleanup. The existing session-generation-keyed route tree unmounts roster/search state on logout/account replacement. No new cache/storage or authentication weakening is introduced.

## Verification
Production build and ESLint passed during implementation. Service tests cover exact GET URL/filter keys/Bearer/no body, missing and cleared tokens, 401/422/403/404/500/network errors, response mapping, authoritative total/empty response, nullable names, privacy stripping, mismatched scope and name/registration search. No populated real roster or successful login response is fabricated.

Browser component tests use the existing isolated Screen 7 fixtures only, never production data. They pass at 1440,1280,768,390,375px: all cards rendered, no overflow/navigation overlap, fixed count under filtering, case/whitespace/registration matching, clear/no matches, Back targets, detail placeholder and separate access/not-found/error/empty/loading states. Screenshots are UI-only fixture previews and do not claim to show live SQL Server records.

A real unauthenticated GET through the actual Vite config reached port 5002 and returned 401 AUTH_REQUIRED. The existing frontend server on both localhost:5173 and 127.0.0.1:5173 also returned that expected response. Built-app route/Welcome/Login/Back regression checks pass. All 258 tracked backend files match the pre-integration hash baseline. No SQL operations, write requests, installs, commits or backend edits were performed.

## Interactive count verification
The user agreed to test BIIT233 and BIIT235 using credentials entered only in the login form. Results are pending: BIIT233 BCS-C expected 33; BIIT233 BSSE-B expected 10; BIIT235 BCS-A expected 47. These values are verification targets from the user, never application constants. Until the user reports results, actual account-specific counts, live search and cross-teacher denial are not marked verified. No passwords or tokens were requested in chat, read from SQL Server or printed.
