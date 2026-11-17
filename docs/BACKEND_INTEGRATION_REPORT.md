# Backend Integration Report — 2026-11-17

Backup of all pre-change sources: `_backups/phase_start_20261117_085456/` (63 files).
The project is not a Git repository; nothing was committed.

## Verified state of the system

| Item | Finding | How verified |
|---|---|---|
| Database rows | Assessment 2, TestCase 10 (all `hidden`), ReferenceSolution 1; Submission / GradingJob / Evaluation / EvaluationCriterion / **AssessmentSection = 0** | read-only SELECT |
| Rubric table | `AssessmentRubricCriterion` does not exist | INFORMATION_SCHEMA |
| Evaluation duplicates | PK on `submission_id` already blocks duplicates; code now checks first too | schema + unit tests |
| Ollama | reachable, `qwen2.5-coder:3b` (Q4_K_M) installed | `GET /api/tags` |
| g++ | WinLibs MinGW-w64 found on PATH | `Get-Command` |
| Virtualization | `VirtualizationFirmwareEnabled=False`, `HypervisorPresent=False`, no Docker / Windows Sandbox | CIM queries |

## Integration gaps found and closed

1. Scoring, rubric validation, Ollama grading, job completion and compilation-failure services existed but **nothing called them together** → `grading_pipeline.py`.
2. No way to release a job after an infrastructure failure without writing a (zero) evaluation → `grading_job_failure_service.py`.
3. `complete_grading_job` did not save per-criterion rubric results and did not pre-check duplicates → extended (backward compatible).
4. No rubric storage / API → `assessment_rubric_service.py`, `assessment_rubric_routes.py`, SQL proposal.
5. Three separate copies of the test/rubric split formula with no shared rounding → `grading_allocation.py`.
6. No teacher review/finalize API and no student result API → `evaluation_review_service.py`, `evaluation_routes.py`.
7. Assessment mark changes after submissions were allowed → now 409.

## Open items requiring your decision

1. **Approve `create_assessment_rubric_criterion.sql`** (creates 1 table + 1 optional unique index; idempotent; rollback script included). Until then rubric endpoints return 503 and the rubric gate on submissions is inactive.
2. **Secure execution is blocked.** To unblock: enable VT-x/AMD-V in BIOS → install Docker Desktop (WSL2) → `docker pull gcc:13` → set `FYP_SECURE_SANDBOX=docker` for the worker. Each step needs your approval. Then run the adversarial checks below before grading real work.
3. **No AssessmentSection rows exist and no API creates them**, so no student can submit yet. Decide whether to add an "assign assessment to section" endpoint (code only) or seed rows manually.
4. **Test-case generation is hard-locked to assessment 4** (`secure_test_case_save.py`) — every other assessment cannot get test cases.
5. **Late penalty** is not applied automatically; it is shown to the teacher (`is_late`, `late_penalty_marks`) who sets `final_score`. Confirm or request automatic deduction.
6. After finalization, students see `automated_feedback`, which includes AI explanations; the AI saw hidden test outputs, so teachers should read it before finalizing (or we hide it — your call).

## Adversarial sandbox checks to run once a sandbox exists

Infinite loop; fork/thread bomb; 4 GB allocation; 100 MB stdout; write to `/work` and `/`; open a network socket; read host paths; compile bomb (deep templates / `#include` of `/dev/zero`); exit code 125 (must become manual grading, not zero). Each must end as timeout / runtime_error / infrastructure failure with temp dirs removed.

## Known limitations

- The Ollama model's grading accuracy has **not** been benchmarked; outputs are suggestions only. One LLM call per graded submission (`grade_cpp_submission`, timeout 10 s connect / 180 s read); skipped when rubric allocation is 0 or compilation genuinely fails.
- A student program that exits with 125/126/127 under Docker is treated as an infrastructure failure → manual grading (never zero).
- Pipeline correctness is proven with mocks only; there has been no end-to-end run with real submissions.
