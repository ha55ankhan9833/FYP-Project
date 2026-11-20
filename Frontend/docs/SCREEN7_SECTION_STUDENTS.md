# Screen 7 — Section Students List

## Files
Created:
- src/pages/SectionStudents.jsx
- src/pages/SectionStudents.css
- src/components/SectionStudentsView.jsx
- src/services/sectionRoster.js
Modified: src/App.jsx (protected roster and future student-detail routes).
Verification-only unit/browser/route scripts, isolated fixture previews and screenshots are under .verification/.

## Navigation and authorization
The existing View students link already uses #/instructor/sections/{section_id}/students. It now opens this screen. Back returns to exactly that section's details route. Cards, when an authorized roster becomes available, link to #/instructor/sections/{section_id}/students/{student_id}; this is an explicit temporary Student Details placeholder, not a performance screen. Both IDs are encoded/decoded using stable identifiers rather than names.

InstructorGuard still protects all routes. SectionStudents first reloads the authenticated teacher's sections in the existing course/semester/session scope, then matches the exact section ID. No section data, count or search results appear for a non-matching section. Invalid/expired sessions clear auth and return to login. Local section/roster/search state is unmounted on route/session/account changes through the existing session-generation mechanism. No new persistent data storage is used. Student placeholders require the same section validation, and a student must also match an authorized roster record before any personal fields can render. An arbitrary student ID in the URL never yields private records.

## Current API availability and actual schema facts
GET /api/teacher/sections supplies the selected section's real enrolled_students count but no student records. GET /api/students executes a global SELECT TOP 10 against Student_Export, without this section/course scope. It returns student_id, student_name, student_email, program, section, section_id, semester and enrollment_status. It is NOT called by this screen. The global API cannot serve a complete secure roster.

The known stable database identifier is student_id. No inspected authorized roster contract confirms a separate ARID/registration field or states that student_id is the registration number. The frontend does not guess or relabel that field as ARID. Missing registration metadata displays Registration number unavailable once real records are connected.

## Production data behavior
loadSectionRoster is an isolated connection boundary with no speculative request or endpoint path. For a verified real section with enrolled_students=0 it returns a confirmed empty state (No Students Enrolled). For a positive or missing count, the absent roster API produces Student list is currently unavailable. Search stays disabled when no real roster is available. The section enrollment heading still uses its real count if present, independently of search results. Unknown count is visibly unavailable, never zero.

The page separately retains a verified section/count when a future roster request fails. Unsupported, duplicate or out-of-section roster records are rejected. A missing API never produces an empty student list. The adapter accepts only an explicitly complete local view model and strips unneeded/private fields. No sample student names, registration numbers, emails or production arrays are fabricated.

## Search and cards
Once a complete authorized roster is available, the existing component filters name and registration number while typing, case-insensitively and with surrounding/repeated whitespace normalized. Clear search restores all records. No match has its own message. Enrolled count stays fixed; search shows Showing X of Y separately. Native anchors provide keyboard navigation. Avatars derive two initials from real names and use a stable four-color local palette; no external images are fetched.

## Required backend contract for Claude Code (not implemented/called)
Provide a JWT teacher-authorized, read-only roster operation scoped by section_id AND the current course/semester/academic-session, enforcing the SectionCourseTeacher allocation on the server. Confirm its actual path before wiring loadSectionRoster. The response must identify the exact scope, authoritative enrollment count, complete-list or pagination semantics and student records with stable student_id, actual student_name, section_id, confirmed registration/ARID mapping and optional enrollment_status. Exclude passwords, emails and unrelated private student data. Return proper auth/not-owned/error responses and a confirmed empty response only when appropriate.

The adapter's id/name/registrationNumber/sectionId/enrollmentStatus are LOCAL VIEW-MODEL names, not invented Flask response fields. Map the eventual documented response explicitly. If student_id itself is the real ARID, the backend contract must confirm that before it is displayed as registration information.

Client-side filtering is suitable only for a complete moderate roster. A large roster needs authorized server-side search/pagination with separate total-enrolled and filtered counts; partial pages must not be passed as a complete list or produce a misleading no-results message.

## Verification and limits
Production build and ESLint pass. Unit checks cover name/registration/case/whitespace search, initials, no-match/reset behavior, rejection of wrong section/duplicates, stripping extra private fields, confirmed zero vs missing API, and aborts. Browser component tests verify actual typing, clear action, stable count heading, avatar initials, keyboard card navigation, correct section-specific Back links, student placeholder and loading/empty/unavailable/authorization states.

1440,1280,768,390,375px layouts pass with no horizontal overflow, aligned search/count/cards and desktop sidebar/mobile navigation. Screenshots show isolated component-only unavailable-state previews with synthetic section context, not live student data. Search tests use explicitly isolated fictional fixture records that never enter production.

Built-app route regression checks cover Students and the student-detail placeholder along with prior protected screens, Welcome/Login interactions and Back rejection without authentication. All 257 tracked backend files match the pre-task baseline. No backend/auth/SQL/model changes, installs, write requests or commits occurred. The proxy remains port 5002. Multi-section flow state and all previous page source files are unchanged apart from routing to Screen 7.

Real enrollment-count rendering requires an interactive instructor session and remains unverified by this harness. Live roster/student search and live cross-account switching cannot be verified without the secure roster API and user-entered credentials; no credentials or successful login responses were fabricated.
