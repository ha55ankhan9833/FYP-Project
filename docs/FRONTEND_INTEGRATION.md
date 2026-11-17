# Frontend integration (development)

No backend package or code change is needed for local frontend development.
The frontend dev server **proxies** `/api/*` to Flask, so the browser sees a
single origin and CORS never applies.

## 1. Run the backend

```bash
.venv\Scripts\python.exe dev_tools\run_e2e_backend.py
```

```bash
.venv\Scripts\python.exe app.py
```

- `run_e2e_backend.py` is the **recommended** target for UI work. It serves
  `FYP_E2E_Test` (test students E2E-TEST-0001..0004, Assessment 5, finalized
  results), and refuses any other database.
- `app.py` serves `FYP_Database` (real data, read carefully).

Both listen on `http://127.0.0.1:5000`. Only one can run at a time.

## 2. Dev-server proxy

### Vite (React / Vue / Svelte), `vite.config.js`

```js
import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:5000", changeOrigin: false },
    },
  },
});
```

### Create React App, `package.json`

```json
{ "proxy": "http://127.0.0.1:5000" }
```

### Angular, `proxy.conf.json` (`ng serve --proxy-config proxy.conf.json`)

```json
{ "/api": { "target": "http://127.0.0.1:5000", "secure": false } }
```

Call the API with **relative** URLs (`fetch("/api/auth/me")`), never
`http://127.0.0.1:5000/...`. That keeps the same code working behind a
production reverse proxy.

## 3. Production

Serve the built frontend and the API from **one origin** through a reverse
proxy (IIS / nginx: `/` → static files, `/api` → waitress on
127.0.0.1:5000). If separate origins are ever required, `flask-cors` is
needed. That is a **new package and needs approval** (see
`BACKEND_GAP_ANALYSIS.md` G1). Restrict it to the frontend origin; never use `*`.

## 4. Authentication

- `POST /api/teacher/login` or `POST /api/student/login` → `access_token`
  (JWT, 2 hours, no refresh token yet).
- Send `Authorization: Bearer <token>` on every request.
- Keep the token in memory or `sessionStorage`. On `401` with
  `code: "TOKEN_EXPIRED"` or `"AUTH_REQUIRED"`, return to the login page.
- `GET /api/auth/me` → `{user_id, role}` tells the UI which shell to show
  (`teacher` / `student`).

## 5. Errors

Every error is JSON:

```json
{ "status": "error", "message": "Human-readable text", "code": "OPTIONAL_MACHINE_CODE" }
```

- **Display:** show `message` to the user.
- **Branching:** branch on the HTTP status, and on `code` where it is
  present (`AUTH_REQUIRED`, `TOKEN_EXPIRED`, `INVALID_TOKEN`, `NOT_FOUND`,
  `METHOD_NOT_ALLOWED`, `INTERNAL_SERVER_ERROR`).
- **Extra fields:** some routes add fields; for example, section assignment
  adds `errors: [{section_id, reason}]`.

## 6. Things the UI must handle

- **Results:** a student result is `null` until the teacher finalizes; the
  status is `SUBMITTED` / `GRADING` / `UNDER_REVIEW` / `RESULT_PUBLISHED`.
- **Grading:** with execution disabled, real submissions stay `QUEUED`. Offer
  the teacher an explicit "Grade manually" action
  (`POST …/evaluation/manual-grade`, with `confirm_manual_grade: true` and a
  justification). Label such results `MANUALLY_GRADED`, with no test counts.
- **Criterion editing:** show it only when the teacher evaluation has
  `criterion_approval_available: true` (it is false on FYP_Database until
  migration M3).
- **Finalized evaluations:** they are immutable; the API returns 409. Disable
  the finalize button once `result_status` is `FINALIZED`.
- **Deadlines:** `deadline_at` is SQL Server local time without a timezone
  suffix.
- **Hidden tests:** they are never returned to students. Do not try to show
  them.

The full route reference is in `API_CONTRACT.md`.
