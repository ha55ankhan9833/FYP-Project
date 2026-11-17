# Backend gap analysis (read-only audit, 2026-11-17)

**Basis:**
- **Code:** the 37 registered Flask routes (from `app.url_map`) and every
  application module.
- **Tests:** 24 test files (442 tests; tests counted per file).
- **Environment:** installed packages, `.env` keys only (no values), and
  repository hygiene.

Nothing was modified or executed.

**Legend:**
- **Status:** ✅ fully implemented and tested · 🟡 implemented, partially
  tested · ❌ missing · ⛔ blocked by security or deployment.
- **Priority:** P0 = before frontend development · P1 = before a
  development demo with the frontend · P2 = production.
- **Complexity:** S (< ½ day) · M (1–2 days) · L (> 2 days or needs
  infrastructure).

## 1. Feature status by area

| # | Area | Feature | Status | Evidence / note |
|---|---|---|---|---|
| 1 | Auth and assessments | Teacher and student login (JWT, 2 h) | 🟡 | Exercised in the E2E tests (`test_e2e_student_grading_flow`). Passwords are compared in plain text. |
| 1 | | `/api/auth/me` | ✅ | Used by every E2E driver step. |
| 1 | | Create assessment (requires a finalized constraint draft) | 🟡 | Payload validation tested (`test_new_assessment_payloads`); no full happy-path route test. |
| 1 | | List, get and update assessment (GET, GET/:id, PUT) | 🟡 | Implemented, including rubric-allocation conflict checks; **no route tests**. |
| 1 | | Delete assessment (409 on dependants) | ✅ | `test_assessment_delete_route` (13 tests). |
| 1 | | `/api/students` | 🟡 | Stub: `TOP 10` of all students, not scoped to the teacher's sections; untested. |
| 2 | AI constraints | `/api/ai/analyze`, draft create / actions / approve / finalize | 🟡 | Implemented. Depends on `ai_local_api.py` (port 5001, `.venv_ai`, 296 MB FLAN-T5). **No tests** for these routes or for `teacher_approval_service`, `constraint_draft_db` or `constraint_parser`. Error format `{"error": …}` differs from the rest of the API. |
| 2 | Test generation | Exactly 10 equal-weight tests, rule and save | ✅ | `test_ten_case_policy`, `test_generic_test_generation`. |
| 2 | | Generation from a trusted reference (host execution) | 🟡 | Only for allowlisted reference hashes (currently factorial only, owner-approved). Tested with mocks. |
| 2 | | Verified-oracle import (prime) | ✅ | `test_oracle_import` (29). Only one oracle exists. |
| 2 | | Teacher-authored test cases (manual input / expected) | ❌ | No endpoint. A new problem cannot get tests without host execution or a new oracle. |
| 2 | | Reference solution save / approve | ✅ | Routes plus `test_generic_test_generation`. |
| 3 | Rubric and assignment | Rubric PUT / GET / DELETE with allocation checks | ✅ | `test_rubric_service`, `test_api_routes`. |
| 3 | | Section assignment / removal / assignable list | ✅ | `test_assessment_section_service` (24) and live G3. |
| 4 | Student | Assessment list (enrollment + teacher rule) | ✅ | Unit tests, E2E and live G3. |
| 4 | | Submit (atomic Submission + GradingJob, duplicate 409, deadline, rubric-ready) | ✅ | `test_student_submission_flow`, E2E, live. |
| 4 | | Resubmission / file upload (multipart) | ❌ | One submission per assessment; JSON `source_code` only (by design so far). |
| 5 | C++ execution | Production student execution (sandbox) | ⛔ | The gate is disabled: firmware virtualization is off and there is no Docker (`docs/EXECUTION_ISOLATION_ASSESSMENT.md`). |
| 5 | | Compilation contract, diagnostics sanitizer | ✅ | `test_compilation_contract`, `test_diagnostics_sanitizer`. |
| 5 | | Trusted fixture execution and recorded-evidence replay (dev only) | ✅ | Harness tests plus a live run. Not available for real student code. |
| 5 | | Worker loop: claim / lease / retry / release / ownership | ✅ | `test_job_transactions`, `test_grading_pipeline` and the E2E world. No deployed worker process. |
| 6 | Ollama | Criterion-only contract, parsing, consistency flags, constraint checker | ✅ | `test_rubric_ai_grader`, `test_code_constraints_and_consistency`, live. |
| 6 | | Model quality | 🟡 | `qwen2.5-coder:3b` repeatedly misses the root cause (FAILURES_NOT_EXPLAINED); about 95 s per submission, one at a time. |
| 7 | Python verification | Equal-weight scoring, allocation, verification layer | ✅ | `test_scoring_rules`, `test_grading_verification`. |
| 7 | | Late penalty | ❌ | Stored (`late_penalty_marks`) and `is_late` is shown to teachers, but it is **never applied** to suggested or final scores, and not reconciled. |
| 8 | Teacher review | List / view / finalize (total), 409 on repeat | ✅ | Unit tests, E2E, live G3. |
| 8 | | Criterion-level approvals | ✅ (test DB) | `test_student_result_privacy` and live O4. In FYP_Database the table is missing (M3), so the API returns 409. |
| 8 | | Grading a submission whose job stays PENDING | ❌ | `finalize_evaluation` refuses unless an Evaluation exists or the job is FAILED. With execution disabled, real submissions stay PENDING forever, so **no grade is possible.** |
| 9 | Student results | Structured, privacy-safe result | ✅ | `test_student_result_privacy` (15) and live checks on 4 students. |
| 10 | Schema | AssessmentRubricCriterion (created), approval table (M1 test only) | 🟡 | Ad-hoc SQL scripts; no migration history table or versioning. |
| 11 | Security | Parameterized SQL | ✅ | The only f-string SQL (`app.py:902`) builds column names from a server allowlist; values are parameters. |
| 11 | | Plain-text passwords in `Student_Export` / `Teacher_Export`, compared with `!=` | ⛔ | Imported data; hashing needs a schema/data change and a package. |
| 11 | | Login rate limiting / lockout | ❌ | None. |
| 11 | | `app.run(debug=True)` | ❌ | The Werkzeug debugger allows code execution if the server is reachable over the network. |
| 11 | | CORS | ❌ | No CORS extension (`app.extensions` = jwt only). A browser frontend on another origin is blocked. |
| 11 | | Token refresh / logout / revocation | ❌ | 2 h access token only. |
| 11 | | Failure recovery (infrastructure failure → release, never zero; lease expiry) | ✅ | Pipeline tests and E2E. |
| 12 | Frontend support | API contract document | 🟡 | `docs/API_CONTRACT.md` is partly out of date: new teacher fields, `criteria` on finalize, AI draft routes and `/api/grading/status.active_database`. |
| 12 | | Pagination / filtering on lists | ❌ | All lists are unbounded (except the `/api/students` stub). |
| 12 | | Notifications | ❌ | The `Notification` table exists; no API writes or reads it. |
| — | Repository | Version control | ❌ | The project is **not a git repository**. |
| — | | Dependency manifest | ❌ | No `requirements.txt` for `.venv` or `.venv_ai`. |
| — | | Root clutter | 🟡 | 27 backup and variant files in the root (`*.bak`, `ollama_grading_v4–v6.py`, …). |

## 2. Remaining gaps

| ID | Pri | Gap | Affected files | Recommended solution | Cx |
|---|---|---|---|---|---|
| G1 | **P0** | No CORS: the browser frontend cannot call the API cross-origin | `app.py` | Use a dev proxy in the frontend tooling (no backend change), **or** add `flask-cors` (package install needs approval) restricted to the frontend origin | S |
| G2 | **P0** | Submissions stay PENDING forever while execution is disabled; the teacher cannot grade them | `evaluation_review_service.py` (`finalize_evaluation`), `grading_job_*` | Add a teacher "grade manually" path: in one transaction, set the owned PENDING job to FAILED/`MANUAL_REVIEW` (when the gate is disabled), then use the existing manual-grade insert. Tests. **Needs your policy decision** | M |
| G3 | **P0** | Inconsistent error shape (`{"error"}` in the AI draft routes vs `{"status","message"}`) | `teacher_approval_routes.py` | Normalize to `{"status":"error","message"}` (backward-compatible field plus tests) | S |
| G4 | **P0** | API contract out of date | `docs/API_CONTRACT.md` | Document every route: request and response, error codes, role, and new fields (`ai_criteria`, `teacher_criteria`, `criterion_approval_available`, `verified_test_marks`, finalize `criteria`, structured student result, `active_database`) | S |
| G5 | **P0** | No version control | repository | `git init` with a `.gitignore` covering `.env`, `dev_tools/.tokens/`, `dev_tools/reports/`, `.venv*`, `models/` and `_backups/`. **Needs your authorization** to initialize and commit | S |
| G6 | **P0** | No dependency manifest | new `requirements.txt`, `requirements-ai.txt` | Freeze the current versions (no installs) | S |
| G7 | **P0** | Decide M3 (approval table in FYP_Database) | `migrations/…_up.sql` | Apply M3 after approval, **or** the frontend hides criterion editing when `criterion_approval_available` is false | S |
| G8 | P1 | AI constraint and draft routes untested | `ai_backend_routes.py`, `teacher_approval_*`, `constraint_draft_db.py`, `constraint_parser.py` | Route and service tests with the AI service mocked; document starting `ai_local_api.py` with `.venv_ai` | M |
| G9 | P1 | Assessment list / get / update routes untested | `app.py` | Route tests with fakes (ownership, validation, 409 allocation conflicts) | M |
| G10 | P1 | No teacher-authored test cases; only one oracle; host execution allowlisted per hash | `test_case_routes.py`, `secure_test_case_save.py`, `test_case_policy.py` | Add `POST /api/assessments/<id>/test-cases` (exactly 10 teacher-supplied input/expected pairs, validated, dry-run first) using the existing secure save | M |
| G11 | P1 | `/api/students` is a TOP-10 stub, not scoped to the teacher | `app.py` | Replace with `GET /api/assessments/<id>/roster` (enrolled students in assigned sections) or remove | S |
| G12 | P1 | Late penalty never applied | `grading_pipeline.py`, `student_result_builder.py`, `evaluation_review_service.py` | Decide the policy: apply it to the suggested score and include it in reconciliation (`final = test + rubric − penalty`), **or** document that it is manual | S–M |
| G13 | P1 | Root clutter | 27 root files | Move them to `_backups/legacy/` (needs approval; no deletion) | S |
| G14 | P2 | Production student C++ execution | `secure_execution_gate.py` | Verified sandbox (Docker or a VM on a host with virtualization, or a separate grading server) | L ⛔ |
| G15 | P2 | Plain-text passwords | `app.py` logins, `Student_Export`, `Teacher_Export` | Hash passwords (argon2 or bcrypt) with a credential migration and `hmac.compare_digest`; needs schema/data approval and a package install | L ⛔ |
| G16 | P2 | No login throttling / lockout | `app.py` | Per-IP and per-account limits (flask-limiter or a DB counter) | M |
| G17 | P2 | `debug=True`, no production server, no HTTPS | `app.py` | waitress behind HTTPS (IIS or nginx); debug off; config through the environment | M |
| G18 | P2 | No refresh / logout / revocation | `app.py` | Refresh tokens plus a deny-list | M |
| G19 | P2 | Ollama 3B reliability and throughput | `ollama_grading_service.py` | A larger model or a better prompt and evaluation set; queue concurrency; teacher review stays mandatory | M–L |
| G20 | P2 | No deployed grading worker | `grading_worker.py` | Run as a service (after G14); monitoring | M |
| G21 | P2 | Migrations not versioned | `migrations/` | A migration-history table and an ordered runner | M |
| G22 | P2 | No pagination, notifications, audit log or metrics | various | Add when the UI needs them | M |

## 3. Readiness

**Development-demo readiness: ready**, against **FYP_E2E_Test** with the
reviewed fixtures, after G1 (or a dev proxy), G3 and G4. Proven end to end:
- teacher login → assign → student submit;
- recorded-evidence grading with Ollama → Python verification;
- teacher review with criterion overrides → privacy-safe student results.

Real student submissions cannot be graded: they are blocked by G14, and by G2
until a manual-grade path exists.

**Production readiness: not ready.** The blockers are:
- G14: sandboxed execution;
- G15: password hashing;
- G16 and G17: login throttling, debug mode, a production server and HTTPS;
- G2: grading while execution is disabled;
- G5 and G21: version control and migrations.

## 4. Checklist before frontend development starts

1. [ ] **G5** Initialize git (your authorization), with the `.gitignore` above.
2. [ ] **G6** Write `requirements.txt` and `requirements-ai.txt` from the installed versions.
3. [ ] **G1** Choose CORS handling: a frontend dev proxy (recommended, no install) or `flask-cors` (install approval).
4. [ ] **G2** Decide and implement the manual-grade path for PENDING submissions while execution is disabled.
5. [ ] **G3** Normalize the AI draft routes' error format.
6. [ ] **G7** Decide M3, or confirm the frontend must handle `criterion_approval_available = false`.
7. [ ] **G4** Bring `docs/API_CONTRACT.md` fully up to date. It is the frontend's source of truth.
8. [ ] Agree which database the frontend develops against (recommended: FYP_E2E_Test through `run_e2e_backend.py`).

P1 items (G8–G13) can proceed in parallel with early frontend work. P2 items
are production hardening.
