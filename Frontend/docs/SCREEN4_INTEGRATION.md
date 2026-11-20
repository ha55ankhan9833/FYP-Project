# Screen 4 — Select Section

## Files
Created:
- src/pages/SelectSection.jsx
- src/pages/SelectSection.css
- src/components/SectionSelectionForm.jsx
- src/pages/AssessmentNextStep.jsx (explicit temporary placeholder only)
Modified existing source:
- src/App.jsx: routes and local assessment-flow state only.
Verification-only files are under .verification/: screen4-preview.html/jsx, build-screen4-preview.mjs, screen4-browser.cjs, screen4-routes.cjs, preview build, screenshots and pre-change hash manifests.

## Navigation and local flow
The existing dashboard Create New Assignment / Quiz link remains unchanged: #/instructor/assessments/new now renders Select Section under InstructorGuard. Back goes to #/instructor/dashboard. Existing five-item navigation is reused; no badges/counts are fabricated.

Each selection card is a label around a native checkbox with a stable backend section ID. Whole-card clicking and native keyboard Space interaction toggle selection. Multiple selection is allowed. Continue is disabled unless at least one selected ID remains in the current ready list. Unsupported/unavailable, empty and loading data cannot produce a valid selection. Missing student counts use Student count unavailable.

Continue saves {ownerId, sectionIds} to App's React state, then opens #/instructor/assessments/new/details, an explicitly labeled temporary next-step placeholder. It does not create or assign anything. Returning to Select Section initializes the checkboxes from that local selection only for the same verified instructor. After reload the in-memory flow is lost by design; no database/localStorage/sessionStorage persistence is introduced. A different teacher cannot see the old flow through the UI. The actual next assignment screen is not implemented.

## Missing backend contract
The backend still offers only assessment-scoped GET /api/assessments/{id}/sections and /assignable-sections. The latter requires an existing owned assessment and is filtered by its course. Neither supplies a complete authenticated teacher-assigned-section list for pre-assessment selection. No speculative endpoint is called and /api/students is not used.

The shared loadAssignedSections adapter currently returns unavailable without a network request. Retry reruns this boundary and does not pretend that a missing API exists. Once a complete teacher-list contract is available, connect the adapter to a read-only authenticated request through the existing API client, confirm record granularity, stable section identifiers, course restrictions, section_name, optional semester_no and enrollment-count semantics. The adapter must explicitly confirm completeness before an empty list is accepted. A successful empty assessment-scoped response is not proof of no teacher assignments.

Current production state: Sections Unavailable / We couldn't load your assigned sections right now. Continue is disabled. Confirmed empty state: No Sections Assigned Yet / Your assigned sections will appear here once they are configured. Loading and errors remain separate from confirmed empty.

## Visual layout
White/light screen, navy headings, rounded selection cards and blue checkbox/button styling. Desktop reuses the 192px sidebar and a single content column capped at 780px. Tablet/mobile use bottom navigation. Mobile Continue sits above the navigation with a measured gap and natural page scrolling; desktop Continue follows the content without a large forced gap. Device chrome is excluded.

## Verification
- npm run build: pass.
- npm run lint: pass.
- Isolated production-screen previews: 1440, 1280, 768, 390 and 375px; aligned content, no horizontal overflow and no Continue/navigation overlap.
- Component-only test fixtures (never production arrays): whole-card and Space-key selection, multi-select, stable IDs in Continue callback, saved selection restoration on remount, invalid/removed selection filtering, missing counts, empty/loading/error/unavailable states and retry all pass.
- Built-app route checks: Welcome and Instructor Login navigation, empty validation and password visibility pass. Dashboard, Select Section and temporary next-step routes reject missing authentication. Student Login remains a placeholder.
- Existing frontend source other than App.jsx is byte-for-byte unchanged, including auth, prior screens and port-5002 Vite proxy.
- All 254 tracked backend files match the pre-Screen-4 hash baseline. No SQL operations, write requests, backend edits, package installs or commits were performed.
- Live populated-section rendering and authenticated selection-to-placeholder navigation remain unverified because the complete teacher-section API and an approved live instructor session are unavailable to this test harness. Component fixtures tested selection behavior; no successful login was fabricated.

The desktop/mobile screenshots show isolated production-component previews of the current unavailable state. They do not contain fixture sections or represent a successful authenticated session.
