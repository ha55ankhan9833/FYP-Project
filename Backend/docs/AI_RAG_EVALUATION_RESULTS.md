# RAG Phase 2 — evaluation results (validation split only)

Date: 2026-11-18. Code at commit `c1fc49f`. Procedure fixed before any run in
`dev_tools/reports/rag_eval_v1/PREREGISTRATION.md`.

Raw per-problem rows, run metadata, `*.runinfo.json` files, comparison JSON and logs are in
`dev_tools/reports/rag_eval_v1/`, a git-ignored folder.

**Decision: keep RAG disabled.** On held-out data the selected configuration failed decision-rule criterion 1. Exact match did not improve: 0.1908 → 0.1887, McNemar p = 1.0.

## Setup

- **Queries:** the validation split only, 716 problems.
  - dev = `sha256(id) % 3 == 0`: 239 problems.
  - heldout: 477 problems.
- **Test split (709 problems):** never loaded.
- **Retrieval corpus:** the 5,636 training problems.
- **Model:** the unchanged FLAN-T5-small weights. Generation settings were the same as production: beam 4, `max_new_tokens = 384`, input limit 512 tokens.
- **Hardware and services:** CPU only. Ollama had no model loaded. The databases were not used.
- **Near-duplicates:** a problem counts as one when its bge top-1 cosine similarity to a training problem is ≥ 0.95.
  - dev: 49 problems.
  - heldout: 93 problems.
  - The "no near-dup" subset leaves these problems out.

## Stage 1–2: dev selection (239 problems, all rows)

| Config | Exact | Numeric | Clean decode | Unsupported # | Invented # | Copied example | Copy wrong | Mean k | Lat. mean / max (s) | Paired wins RAG / base | McNemar p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **baseline** | **0.2176** | **0.3598** | 1.000 | 0.745 | 0.745 | — | — | 0 | 3.74 / 17.0 | — | — |
| bge k3 enc | 0.1213 | 0.3096 | 0.975 | 0.782 | 0.318 | 0.092 | 0.038 | 1.79 | 5.03 / 18.2 | 3 / 26 | 1.5e-5 |
| flan k3 enc | 0.1464 | 0.3389 | 0.967 | 0.778 | 0.352 | 0.100 | 0.029 | 1.71 | 4.90 / 17.2 | 7 / 24 | 0.0033 |
| tfidf k3 enc | 0.1381 | 0.3222 | 0.971 | 0.774 | 0.293 | 0.121 | 0.046 | 1.78 | 5.46 / 28.4 | 6 / 25 | 0.0009 |
| hybrid k3 enc | 0.1464 | 0.3305 | 0.971 | 0.774 | 0.301 | 0.113 | 0.042 | 1.75 | 6.01 / 20.0 ¹ | 7 / 24 | 0.0033 |
| flan k1 enc | 0.1590 | 0.3556 | 0.971 | 0.762 | 0.356 | 0.080 | 0.013 | 0.88 | 5.78 / 20.3 | 8 / 22 | 0.016 |
| flan k5 enc | 0.1464 | 0.3389 | 0.971 | 0.778 | 0.335 | 0.105 | 0.029 | 2.04 | 5.89 / 16.6 | 7 / 24 | 0.0033 |
| **flan k3 raw** (selected) | **0.1841** | 0.3431 | 1.000 | 0.766 | 0.213 | 0.146 | 0.042 | 2.11 | 4.62 / 18.1 | 7 / 15 | 0.134 |

**Selection.** In stage 2a, flan and hybrid tied on exact match. Flan won the tie on numeric match. In stage 2b, the raw example format beat encoded on the same retriever, so **flan, k_max = 3, raw** was selected by the pre-registered rule.

All seven RAG configurations were **below** the dev baseline. Six of the seven were significantly below it (p < 0.05). The selected configuration went to held-out testing anyway, as the pre-registration requires.

¹ Hybrid's raw mean was 127 s because of two stalls caused by the host sleeping: P446 at 18,040 s and P487 at 10,894 s, each for about 71 generated tokens. The figure shown excludes those two rows. The run was not repeated. Stalls cannot change accuracy, and hybrid lost the selection on numeric match, not on latency. Keep-awake was enabled for every run after this one.

**No-near-duplicate subset (dev, 190 problems).**
- Baseline exact match: 0.1632.
- RAG configurations: 0.1053 (bge) to 0.1421 (flan k1 and flan k3 raw).
- No configuration beat the baseline.

## Stage 3: held-out (477 problems) — baseline vs flan k3 raw

| Metric | Baseline (all) | flan k3 raw (all) | Baseline (no near-dup, 384) | flan k3 raw (no near-dup) |
|---|---|---|---|---|
| Exact-match constraint accuracy | **0.1908** | 0.1887 | 0.1589 | 0.1589 |
| Paired exact wins (RAG / base) | — | 20 / 21, **p = 1.0** | — | 13 / 13, p = 1.0 |
| Numeric match (all numbers equal) | 0.3103 | 0.3396 | 0.2708 | 0.3281 |
| Number precision / recall | 0.689 / 0.678 | 0.707 / 0.729 | 0.667 / 0.653 | 0.691 / 0.711 |
| Clean decode | 0.9937 | 0.9937 | 1.000 | 1.000 |
| Complete output | 0.9916 | 0.9916 | 0.9974 | 0.9974 |
| Output truncated | 0.0084 | 0.0084 | 0.0026 | 0.0026 |
| Input too long (D4 refusal) | 0 | 0 | 0 | 0 |
| Outputs with unsupported-number warning | 0.7107 | 0.7191 (+0.84 pp) | 0.6667 | 0.6771 |
| … of which a number was copied from an example | 0 | 0.6436 | 0 | 0.5938 |
| … with an invented number (in neither the problem nor an example) | 0.7107 | 0.2201 | 0.6667 | 0.2161 |
| Output identical to a retrieved example's constraints | — | 0.1279 | — | 0.0807 |
| … and wrong (incorrect example copying) | — | 0.0252 | — | 0.0286 |
| Mean examples used / skipped for budget | — | 2.12 / 1,173 | — | 2.26 / 1,083 |
| No example fitted the budget | — | 0.0461 | — | 0.0495 |
| Retrieval failure → fallback | — | 0 | — | 0 |
| Latency mean / median / p95 / max (s) | 3.97 / 3.34 / 8.04 / 51.6 | 4.91 / 4.39 / 8.77 / 25.3 | 3.76 / 3.13 / 8.02 / 51.6 | 4.77 / 4.34 / 8.58 / 25.3 |
| Retrieval time, mean / max | — | 80 ms / 273 ms | — | — |
| Peak process memory | 817 MB | 834 MB | — | — |
| Wall time (477 problems) | 1,895 s | 2,311 s | — | — |

## Decision rule (pre-registered)

| Criterion | Required | Measured | Result |
|---|---|---|---|
| 1. Exact match improves, McNemar p < 0.05 | improvement and p < 0.05 | −0.21 pp, p = 1.0 | **FAIL** |
| 2. Unsupported-number rate rise | ≤ +2 pp | +0.84 pp | pass |
| 3. Validity (clean decode and complete) does not fall | no fall | unchanged (0.9937 / 0.9916) | pass |

**Outcome:** the rule fails, so RAG stays disabled. `RAG_MODE = "off"` in `ai_constraint_service.py` was not changed.

## Interpretation

These are observations, not claims beyond the data.

- **Exact match.** Retrieved examples did not improve exact-match accuracy on dev or held-out. The encoded format made it significantly worse on dev. The likely cause is the known risk that the model never saw context during training: examples act as noise more often than as guidance.
- **Numbers.** With raw examples, held-out numeric match rose by 2.9 pp, and invented numbers fell from 71% to 22% of outputs. However, most of those numbers now come from retrieved examples rather than from the problem. The overall unsupported-number rate did not fall, so a teacher still has to check the same share of outputs.
  - These were secondary metrics, not the pre-registered criterion.
  - They are a possible direction for a future, separately approved experiment, such as training with context.
- **Format.** The encoded example format made decoding less clean: 2.5–3.3% of dev outputs were invalid, against 0% for the baseline. The raw format did not have this problem.
- **Near-duplicates.** Leaving them out does not change the conclusion.

## Integrity checks after evaluation

- `FYP_Database` and `FYP_E2E_Test`: identical to the pre-AI-work snapshot, using the read-only per-table count, content hash and schema hash.
- `RAG_MODE = "off"`. No live-service, grading or teacher-approval code was changed. `get_secure_sandbox()` returns `None`.
- Nothing was installed, downloaded, retrained or committed during Phase 2 Parts B–C. The only new tracked-candidate file is this document; the other new files are git-ignored reports.
- The test split was not loaded by any run. The harness refuses non-validation IDs.
