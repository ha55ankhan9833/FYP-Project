# Screen 5 — Instructor Profile and client-side logout

## Files
Created src/pages/InstructorProfile.jsx, src/pages/InstructorProfile.css and src/components/InstructorProfileView.jsx.
Modified src/services/auth.js, src/components/InstructorGuard.jsx, src/services/instructorDashboard.js and src/App.jsx.
Verification scripts, isolated profile-view preview build and screenshots are under .verification/ and excluded from the production entry.

## Field sources and ownership
The existing login response returns teacher.teacher_id, teacher.teacher_name, teacher.teacher_email and teacher.department. After /api/auth/me verifies matching user_id and role=teacher, only these allowed display fields are retained with the existing session token. Profile metadata is returned only for the verified matching teacher ID; arbitrary extra fields, other teachers' metadata and student roles are not exposed.

The teacher-sections response is already ownership/filter validated by the shared loader. Profile uses its teacher_name and department for current values, falling back to verified login metadata on loading/failure. Email comes only from login metadata: no email/profile endpoint exists. Older sessions created before this change did not store email/department, so missing fields honestly display unavailable until a new login. Instructor ID comes from verified /api/auth/me. The avatar initial comes from the real display name with no added title or fake photo.

## Course evidence and limitations
GET /api/teacher/sections uses the unchanged CSC-101 / semester 1 / 2025SM scope through port 5002. A course is displayed only when a validated response has actual allocated sections and those sections include a course name/code. The endpoint can return a valid course object even for teachers with no allocation; that object alone is never used as evidence. Empty allocations, missing course fields or backend failure show Course information unavailable. The UI labels confirmed courses as current-scope information. There is no complete authenticated all-courses endpoint; none is invented.

## Logout and session isolation
Log Out calls clearSession and replaces the hash with #/instructor/login. It removes the known fyp.accessToken and fyp.instructorProfile keys, without clearing unrelated browser data or making an API request. The auth service publishes a session-generation change on clearing a session or establishing a new verified login.

App's instructor route tree is keyed by that session generation. Consequently logout, expiry or a new verified login unmounts InstructorRoutes and its assessmentFlow state, guard identity, cached per-page teacher/section/course data and local selected-checkbox state. In-flight page requests are aborted by existing effect cleanup. Navigating between protected screens within the same session still preserves assessment-flow selections. Visiting public/login screens also unmounts the instructor route tree.

InstructorGuard re-verifies each protected route, window focus and pageshow. Missing authentication immediately clears visible guarded state and redirects. Browser Back may revisit an old URL but cannot restore its protected content without authentication. No successful session is simulated to make the route pass. The backend has no logout/revocation endpoint: logout is client-side only; no database account is modified.

## Visual layout
Reuses the current instructor sidebar/bottom navigation with Profile active, and a single 780px maximum desktop column. Figma's white background, initial avatar, blue badge, information rows and light-red logout action are retained. All new styling is scoped to profile classes. Prior screen CSS and the port-5002 proxy remain unchanged.

## Verification
Production build and ESLint pass. Unit checks cover whitelisted field mapping, teacher/role mismatches, clearing both stored keys, session-generation notifications and next-account isolation. Browser component checks cover initial generation, field rendering, missing/empty/allocated course evidence, rejection of mismatched-owner data, loading/error/retry, real clearSession callback and login redirect. The fixtures are isolated component tests, not fake login/API success responses.

Desktop/tablet/mobile sizes 1440, 1280, 768, 390 and 375px pass with no horizontal overflow and no logout/navigation overlap. Built-app route tests cover Profile protection, Welcome/Login interactions, previous instructor routes and Back rejection when authentication is absent. No write requests or SQL operations were performed.

Three-account live verification (BIIT235, BIIT187, BIIT233), live logout after an approved successful login and selection clearing across those actual account switches remain for the user to perform by entering credentials in the login form. No passwords or tokens were requested, copied, logged or displayed. Screenshots are isolated views with unavailable-field fallbacks and do not represent an authenticated account.
