# Instructor login diagnosis

## Confirmed cause
The live listener on port 5000 was launched with Backend/dev_tools/run_e2e_backend.py. That launcher explicitly selects FYP_E2E_Test and refuses startup if SELECT DB_NAME() differs. It does not use the normal FYP_Database connection selection.

Read-only diagnostics checked the teacher email visible in the user's screenshot. FYP_Database contained 1 matching teacher_email record; FYP_E2E_Test contained 0. No teacher_password column was queried, retrieved or printed. No supplied password or JWT was collected.

The account cannot authenticate against the currently running E2E database because its email is absent there. This establishes the database mismatch; it does not test or assert whether the user's password matches the production record.

## Request and actual response
Frontend: POST /api/teacher/login, Content-Type application/json, body {email, password}. Email is trimmed; password is neither trimmed, lowercased, hashed nor otherwise transformed. Backend expects teacher_email (not teacher_id) and the same two JSON field names.

Flask queries Teacher_Export by teacher_email and uses a direct Python comparison of the returned teacher_password with the submitted string. A missing record OR unequal password returns HTTP 401 with {status:error,message:Invalid email or password}, without code. Database exceptions and all other caught login exceptions both return HTTP 500 with {status:error,message:Login failed}; there is no distinct database-failure API code.

Live direct and Vite-proxy probes using the screenshot email and a freshly generated test password returned HTTP 401, message Invalid email or password, no code and no token. These were not replays of the user's password or original browser request. The Vite probe used the actual vite.config.js proxy to 127.0.0.1:5000 and closed its temporary server afterwards. Existing Vite instances were not reliably reachable from diagnostics, so the original browser transaction was not captured.

## Frontend-only improvements
- src/services/api.js: attaches a safe operation identifier to HTTP errors; never stores request credentials or headers in errors.
- src/services/loginErrors.js: explicit mappings for login 401, session errors, request format errors, unavailable endpoint/service, network/timeouts, generic database-or-server 500 and coded unexpected-server 500.
- src/pages/InstructorLogin.jsx: uses the shared mapping instead of guessing the failed step from whether an error code exists.

The frontend cannot definitively distinguish database connection failure from another uncoded login HTTP 500 because the backend intentionally returns the same response. The message expresses that uncertainty rather than falsely reporting invalid credentials.

## Verification and limits
Production build, ESLint, error-mapping/request-shape checks and existing browser regression checks passed. Screen 1 source and global styles plus Screen 2 CSS remain byte-for-byte unchanged; responsive login checks passed at 1440, 1280, 768, 390 and 375px. All backend tracked files were checked against a pre-investigation hash baseline. No backend edits, database writes, password resets, authentication changes, commits or connection changes were performed.

Real instructor login remains unverified and blocked for this email while the backend uses FYP_E2E_Test. Starting the backend against FYP_Database requires the user's approval and is outside this frontend-only change. No reset or credential-copy operation is needed to resolve the identified database mismatch.
