# Screen 3 integration status

The production dashboard uses only verified instructor identity metadata. There are no seeded sections, students, badges, notification counts or draft counts.

## Identity
POST /api/teacher/login returns teacher.teacher_id and teacher.teacher_name. After GET /api/auth/me confirms role=teacher and the same user_id, auth.js saves the display name and user ID in sessionStorage alongside the existing token. InstructorGuard re-verifies on mount, window focus and every 60 seconds, then provides the matched display name through InstructorSessionContext. Missing, corrupt or mismatched metadata uses Hello, Instructor! Expired/invalid tokens clear both token and profile and redirect to login. Network failures hide protected content and offer retry.

## Missing complete teacher-section API (backend work for Claude Code)
No registered endpoint returns the complete set of sections/courses taught by the JWT teacher, independently of whether the teacher has created assessments.

GET /api/assessments/{id}/assignable-sections is restricted to that assessment's course. GET /api/assessments/{id}/sections lists sections assigned to that assessment. Neither can prove a complete teacher roster. GET /api/students is a global top-ten placeholder and is not used.

The existing assessment-scoped endpoints expose section_id, section_name, semester_no and enrolled_students. Their counts are distinct enrolled students for the assessment's course, not necessarily a whole-section count across courses. There is no dashboard-wide count or course-name contract. Do not sum course-specific counts or conflate assessment assignment with teaching assignment.

A future complete-list endpoint must derive teacher identity from JWT and enforce teacher ownership. Confirm record granularity (section or section/course), unique identifiers, course codes/names, semester fields and student-count scope before wiring it up. No endpoint path is assumed or requested by the current frontend.

services/instructorDashboard.js is the connection boundary. loadAssignedSections currently returns unavailable without a network request. Wire that function only after the backend contract exists; map the confirmed complete response through adaptAssignedSections with complete:true. Unknown completeness remains unavailable. Duplicate section identifiers are rejected, so a section/course response must first be reconciled under an agreed contract.

Only an explicitly confirmed complete empty array displays No Sections Assigned Yet / 0 Assigned Sections. Missing API support displays Sections unavailable with no count. Missing student counts display Student count unavailable. Only real available semester/course labels become badges. Check again reruns the adapter; it currently cannot discover a missing endpoint and does not issue speculative requests.

## Navigation
Create New Assignment / Quiz -> #/instructor/assessments/new (placeholder only, no write request).
Home -> #/instructor/dashboard.
Results, Notifications, Drafts, Profile -> their matching protected instructor placeholder routes.
Section cards, once supported data exists -> #/instructor/sections/{encoded section ID}, a protected placeholder.
No notifications API, list-drafts API, dashboard summary API or section-detail API is invented. No next screen is implemented.

## Verification artifacts
The isolated dashboard preview renders the production component without session data or fake records. It displays the real generic-name fallback and API-unavailable state. It is outside the application build and is not an authentication bypass route. Its screenshots are previews, not proof of a successful live login. Unit-only fixtures exercise name isolation, confirmed empty lists, populated adapter shape, missing counts and malformed input. Real populated teacher sections and live successful login cannot be verified without an approved instructor session and a complete-list API.

## File inventory
Created production files:
- src/pages/InstructorDashboard.jsx
- src/pages/InstructorDashboard.css
- src/pages/InstructorDestination.jsx
- src/components/InstructorNavigation.jsx
- src/components/InstructorSections.jsx
- src/services/instructorDashboard.js
- src/services/instructorSession.js

Modified production/configuration files:
- src/App.jsx
- src/components/InstructorGuard.jsx
- src/components/Icon.jsx
- src/services/auth.js
- eslint.config.js (excludes generated .verification artifacts)

Documentation: docs/SCREEN3_INTEGRATION.md.
Verification scripts, isolated-preview source/build, hash baseline and screenshots are under .verification/. Chrome's verification profile is in the existing node_modules/.cache directory. These assets are not part of the production app entry or production dist build.

## Final validation
- Production Vite build and ESLint: pass.
- Adapter/identity unit checks: pass. Synthetic unit records are confined to the test script and are never supplied to the running dashboard.
- Dashboard isolated previews: 1440x900, 1280x720, 768x1024, 390x844, 375x812; no horizontal overflow; desktop sidebar/mobile bottom navigation verified.
- Screens 1/2 regression checks: navigation, password visibility, empty-field validation, loading, isolated HTTP/network failures and real Flask invalid-credential rejection pass.
- Protected-session checks: missing session rejection, actual Flask malformed-token rejection, isolated network error/retry and expired-token rejection pass. No successful login response was fabricated.
- Backend: all 254 tracked files match pre-screen hashes; existing untracked audit documents remain untouched.
- Not verified live: successful named instructor login, student-role token rejection, real expired instructor token, complete empty or populated teacher roster, student counts. No credentials or complete teacher-list API are available. Empty/populated/missing-count cases were verified at the adapter/component boundary only.
