# Grading preview harness (development only, SYNTHETIC evidence)

```
.venv\Scripts\python.exe dev_tools\grading_preview_harness.py [--scenario NAME] [--live-negative]
```

**Not an end-to-end student execution test.** Student C++ is never compiled
or run; per-test output comes from a Python model of each fixture.

| Real | Synthetic |
|---|---|
| Assessment 5 config, problem text, rubric, 15 hidden tests (read-only SQL) | Student submissions in `dev_tools/synthetic_submissions/` |
| `grading_pipeline.process_claimed_job` (evidence validation, equal-weight scoring, rubric validation, score combination, feedback) | Per-test stdout (Python model of the fixture's logic) |
| Ollama `qwen2.5-coder:3b` through `rubric_ai_grader` | Job claim / lease (no GradingJob row) |

Safety:
- Every SQL connection is wrapped by `ReadOnlyConnection`: any statement not
  starting with SELECT/WITH, any `commit()`, and any `autocommit` change raise.
- The pipeline's write steps (`complete`, `complete_compilation_failure`,
  `release`) are replaced by in-memory capture.
- `SyntheticEvidenceSandbox` is never registered with `secure_execution_gate`;
  the production worker still refuses to claim jobs.
- No module in the application imports `dev_tools` and no route exposes it
  (enforced by `tests/test_grading_harness_and_reliability.py`).
- The LLM is told the evidence type is `synthetic_development_fixture`.

Scenarios (10-test rule): `all_pass` 10/10, `most_pass` 8/10
(`d * d < n` bug), `half_range` 6/10 (sieve inner bound 1000),
`none_pass` 0/10 (`Yes`/`No` output case).

`--test-source database` (default) uses the production loader, which
refuses any assessment whose stored set is not exactly 10 (Assessment 5
holds 15 until its migration is approved). `--test-source
proposed-migration` uses the 10 reviewed rows of
`fixtures/new_assessment_csc101/migration_to_10_tests.json`, read from the
database and renumbered in memory only. `--live-negative` runs real failure paths: Ollama unreachable,
real 0.5 s read timeout, and a 100 % validation override (no LLM call).

Fairness: the `//` developer header of each fixture (which labels it
SYNTHETIC and describes the bug) is stripped before the code is sent to
the model, so the model is not handed the diagnosis.

AI contract: rubric grading uses the **criterion-only contract**
(`ollama_grading_service.grade_rubric_criteria`): the model returns
`{criteria: [{criterion_name, mark, explanation}], overall_feedback,
issues, improvement_suggestions}` and is never asked for a total. Python
validates each criterion and computes the total. The legacy
`grade_cpp_submission` contract is unchanged and still rejects a reported
total that differs from its ratings.

Reports: `dev_tools/reports/grading_preview_*.json`.
