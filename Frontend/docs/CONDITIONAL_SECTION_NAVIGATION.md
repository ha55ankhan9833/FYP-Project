# Conditional Section Dashboard Navigation

## Files
Created:
- src/pages/CombinedSections.jsx
- src/components/CombinedSectionsView.jsx
- src/pages/CombinedSections.css
Modified:
- src/App.jsx
- src/services/assessmentFlow.js
- src/services/sectionDetails.js
- src/pages/SectionDetails.jsx
- src/components/SectionDetailsView.jsx
- src/pages/SectionDetails.css
- src/pages/AssessmentNextStep.jsx
Verification-only previews, scripts, screenshots and baseline are under .verification/.

## Continue and selection context
Select Section still uses the real authenticated teacher-section loader. Continue validates/copies the complete ID array into the existing owner-bound React flow state. Exactly one ID navigates to the existing #/instructor/sections/{encoded ID}. Multiple IDs navigate to #/instructor/sections/overview. No selection goes directly to assessment creation anymore.

The overview route is handled explicitly ahead of rendering the dynamic section route. It is protected by the same InstructorGuard. It reloads teacher sections and resolves every saved ID before displaying any selected data. Missing flow after refresh, a different owner, fewer than two IDs or any deleted/unassigned ID produces recovery to Select Section. It never silently drops one of the original selected IDs.

Cards link to existing single-section details without changing the flow. Section Details detects whether the current ID belongs to the saved context, validates the entire context, and carries all those IDs into Create. Back returns to combined overview for a multi-context, to Select Section for a single context, or to Dashboard for a direct section visit outside the flow. Invalid context disables Create and offers re-selection.

Combined Create carries all validated IDs into the existing next-step placeholder. Section Create does the same for a valid original context; direct section visits initialize one ID. The placeholder revalidates membership and displays actual program/name and ID for every selected section. It remains a temporary placeholder and does not implement assessment creation.

## Data and metrics
No production sections or counts are hardcoded. Section data remains GET /api/teacher/sections with the shared CSC-101 / semester 1 / 2025SM scope and existing bearer session, through Vite's unchanged port-5002 proxy.

The combined list includes all selected sections, real program/name, course code/name, semester, session code and enrolled count. The student metric is Total section enrollments, computed only when every selected section has a real count. Missing any count produces a dash. It is not called distinct/unique students: the API does not guarantee disjoint memberships or expose identities for deduplication.

Assessment counts reuse the authenticated assessment-to-section joins already implemented for Section Details. Each selected section is queried; assessment IDs are deduplicated across all sections. Any missing/failed lookup or unknown/inconsistent classification leaves all aggregate assessment counts unavailable, not partial or zero. A fully successful empty result is zero. This uses GET only and repeats per-section read fan-out; a server-side aggregate API would be more efficient for large selections.

## Isolation and persistence
The flow stays in the existing session-generation-keyed InstructorRoutes tree. Back/forward among protected selection/detail/overview/placeholder routes preserves the full same-owner ID array. Refresh loses in-memory selection and shows recovery rather than guessing. Existing client logout/session replacement unmounts the tree and clears the flow; another teacher's ID cannot read it. There is no new persistent cache or browser storage key.

No assessment-creation or section-assignment POST is called. No database operations, backend edits, installs or commits were performed.

## Validation
Production Vite build and ESLint pass. Unit checks cover zero/one/multi routing, ID encoding, order/copy preservation, owner mismatch, missing/deleted membership, deduplicated shared assessments, inconsistent classification and partial-failure handling. Existing section/flow service tests still pass.

Browser component tests pass at 1440,1280,768,390,375px: single main column, sidebar/mobile navigation, no horizontal overflow, every selected card, exact all-ID Create callback, Back target, enrollment sum labeling, unavailable counts and loading/error/missing recovery. Long content scrolls with bottom padding so Create clears mobile navigation. Built-app regression checks reject unauthenticated overview/details/profile/creation routes and Back restoration, and preserve Welcome/Login interactions.

Tests and screenshots use isolated explicitly named component fixtures only, not real instructor sessions, hardcoded production records or fabricated successful logins. Real instructor populated navigation, back/forward through the authenticated flow, and actual logout/account-switch verification remain for interactive user testing. All 257 tracked Backend files match the pre-task baseline. No backend or SQL configuration was changed.
