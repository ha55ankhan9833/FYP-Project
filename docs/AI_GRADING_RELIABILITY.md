# AI grading reliability safeguards

AI rubric marks are **suggestions**. Nothing below changes a mark; every
safeguard can only set `NEEDS_REVIEW`. Teacher finalization is mandatory
before any result reaches a student.

## What the model receives (`rubric_ai_grader.build_llm_evidence`)
Problem, input/output format, approved value constraints, approved code
constraints, the teacher rubric, the student source, and test evidence:
failing tests **first**, each with its real input, expected and actual
output; `failed_test_numbers`; static `code_constraint_checks`. Synthetic
development evidence is labelled `synthetic_development_fixture`.

## Response contract `criterion_only_v1.1` (no totals)
```json
{"criteria": [{"criterion_name": "...", "mark": 2.5, "explanation": "...",
               "cited_failed_tests": [6, 9]}],
 "observed_failures": [{"test_number": 9, "description": "..."}],
 "suspected_bugs": ["..."],
 "overall_feedback": "...", "issues": ["..."], "improvement_suggestions": ["..."]}
```
Observed failures must cite supplied failing tests; suspected bugs are
inferences. Python validates every field and sums the marks.

## Static code-constraint checks (`code_constraint_checker.py`)
Comments, strings, char literals, raw strings and preprocessor lines are
removed first. Statuses: SATISFIED / VIOLATED / INCONCLUSIVE.

| Constraint | Static rule | INCONCLUSIVE when |
|---|---|---|
| `loop_required` / `loops_prohibited` | `for`/`while`/`do` tokens | macros, `goto`, STL algorithms |
| `recursion_required` / `recursion_prohibited` | self or mutual call cycle between defined functions | lambdas, `std::function`, function pointers, macros |
| `function_required` | user-defined function called from `main` | defined but not called from `main` |
| `stl_container_required`, `hash_map_required` | container type tokens | macros |
| `function_prohibited:NAME` / `function_required:NAME` | `NAME(` call in code | macros |
| `header_prohibited:NAME` / `header_required:NAME` | `#include <NAME>` | — |
| anything else / free text | — | always |

No automatic deduction: the current schema has no teacher penalty policy.

## Consistency flags (`grading_consistency_checks.py`)
`DEFECT_ADMITTED_WITH_FULL_MARKS`, `PRAISE_WITH_ZERO_MARKS`,
`UNSUPPORTED_FAILURE_CLAIM`, `FAILURES_IGNORED`, `FAILURES_NOT_EXPLAINED`,
`EXECUTION_CLAIM`, `CONSTRAINT_NOT_VERIFIED`, `CONSTRAINT_CLAIM_CONTRADICTED`,
plus the existing score guards (e.g. full rubric marks with failed tests).
Text rules are lexical heuristics and are labelled as such.

## Python verification layer (`grading_verification.py`, verification_v1)
Runs after scoring, before the single atomic save. It re-derives:
exactly 10 unique results, pass count vs case flags, every pass flag
re-compared with `output_comparison.DEFAULT_POLICY` (TRIMMED_EXACT - the
historical rule), the equal-weight test score, exact rubric criterion set,
marks within limits and 2 decimals, rubric total = Python sum, combined
total within the maximum. Any mismatch -> `VERIFICATION_FAILED`, job
released, nothing saved, never a zero. Questionable but valid AI output ->
marks preserved, `review_status = NEEDS_REVIEW`. The report contains only
counts and test numbers (no inputs or outputs) and is appended to the
teacher feedback under a teacher-only marker; students never see it.

Evidence provenance: every evidence set is labelled `sandbox_execution`
or `synthetic_development`. Synthetic evidence is refused
(`SYNTHETIC_EVIDENCE_REJECTED`) unless `GradingDependencies.
allow_synthetic_evidence` is True, which only the dev harness sets; the
gate refuses to register a synthetic source.

## Student-facing redaction
The model sees hidden inputs, so its wording can quote them. When a result
is published, the teacher-only verification section is removed, and hidden
test inputs (plus hidden expected outputs of 6+ characters; short ones such
as YES/NO are part of the public output format) are replaced by
`[hidden test input]` in the AI section and criterion explanations.

## Known limits (live evaluation, qwen2.5-coder:3b, 2026-11-17)
The model reliably reports *which* supplied tests failed, but did not
identify the cause of any of the three synthetic bugs without hints,
praised buggy code, invented one passing test as failing, and made false
claims about correct code. Flags catch these patterns; they do not make
the model accurate.
