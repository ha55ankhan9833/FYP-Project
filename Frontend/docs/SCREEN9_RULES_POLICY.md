# Screen 9: Rules and Policy

## Files

Production changes: `src/App.jsx`, `src/services/api.js`, `src/services/assessmentRules.js`, `src/hooks/useAssessmentRules.js`, `src/pages/AssessmentRules.jsx`, `src/pages/AssessmentRules.css`, and `src/components/AssessmentRulesForm.jsx`.

Verification-only files live under `.verification`: screen9 unit/browser scripts, isolated preview/build script, backend hash baseline and assessment-rules screenshots. Screenshots show the real components in an explicitly labeled isolated preview with blank constraints, not a simulated live instructor session.

## API and data ownership

Authenticated POST `/api/ai/analyze` through the existing port-5002 proxy sends `{problem,input_format,output_format}` from Basic Details. It is the non-saving analysis endpoint. The frontend allows 130 seconds for the Flask service's 120-second upstream timeout. Other requests keep their existing timeout.

The outer response is `{status:"success",data:{...}}`. `data.value_constraints[].text` populates Value Constraints; `data.code_constraints[].type` populates Code Constraints. Warnings, suggestion statuses, model status and incomplete-output notices are shown. Missing or empty code suggestions stay blank and editable.

Backend inspection: FLAN-T5's prompt requests **value** constraints. Code constraints are derived separately by `extract_code_constraints` from recognized phrases across problem/input/output. This supports a limited vocabulary (recursion, loops, functions, dynamic programming and selected STL requirements), not general model generation of code requirements. Numeric bounds alone do not imply such requirements. Supporting additional patterns or model-generated code constraints requires separately authorized backend work.

User live verification confirmed Step 1 → Step 2 and automatic Value Constraints (`1 <= N <= 100000; -10^9 <= A[i] <= 10^9`). Code Constraints remained empty. Frontend mapping of nonempty type objects passes isolated tests. The specific live `data.code_constraints` response has been requested from the user; an empty UI alone does not prove the exact response array.

## Draft behavior and limits

Rules live in instructor-session-scoped React memory. Back/Forward preserves policy, edits, mode and selected section IDs; successful or failed analysis is not automatically repeated for the same source. Changing problem/input/output causes a new analysis. Regeneration stages suggestions if fields already contain content; replacing edits requires an explicit action. Logout/account changes/discard clear the state and abort outstanding analysis. No constraint data is stored persistently.

No draft ID, schema or approval is invented. Current non-saving analysis has no draft ID; approval flags remain false. Draft creation/actions/approval/finalization/schema APIs were inspected but are not called because they persist data and require separate approval.

Date/time are required, checked for valid values and a future local deadline. Future checks use the device clock; the planned backend format is `YYYY-MM-DDTHH:MM:SS` interpreted in server-local time without a timezone suffix. Late penalty supports finite, nonnegative amounts up to total marks. Turning late submissions off retains the field but makes its effective penalty zero. The backend accepts allow_late/late_penalty_marks but does not automatically implement a per-24-hour deduction; the UI discloses this. Nothing is saved. Textareas preserve whitespace and have no arbitrary 200-character cap.

Generate validates and explains unmet prerequisites. End-to-end generation still needs a saved teacher-owned draft, review of both categories, approval/finalization, supported approved integer ranges or structured schema, approved reference solution and verified expected-output execution/oracle, actual assessment ID and validation/rubric allocation. Plain text is not a schema. No assessment, section assignment, test generation or DB mutation runs in this screen. Step 3 is not implemented.

## Verification

- Production build and ESLint pass.
- Service tests cover exact payload, Bearer requirement, response mapping, warnings, missing code, edit protection, date/time validation and penalty boundaries.
- Isolated browser tests cover loading/success/error fixtures, initial population, regeneration apply/keep, Back caching, validation, toggles and prerequisite-only Generate. Fixtures are explicitly test-only and never claimed as live model output.
- Responsive checks: 1440, 1280, 768, 390 and 375px; no horizontal overflow, desktop sidebar and mobile bottom navigation with clearance.
- Direct real Flask unauthenticated analysis returns 401 AUTH_REQUIRED. User confirmed authenticated live value generation; live code response is pending.
- 258 Backend file hashes match the pre-task baseline. No Backend edits, package installation, database writes or commits were performed.
