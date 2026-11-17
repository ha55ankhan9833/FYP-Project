# AI Phase 1 — FLAN-T5 constraint service fixes (D1–D4)

Date: 2026-11-17. Approved scope: code-only fixes to the existing constraint service.
Model weights, datasets, databases, grading pipeline and the execution gate are unchanged.
Nothing was installed, downloaded or trained. Findings come from `docs/AI_ARCHITECTURE_AUDIT.md` §4.2.

## Changes

| File | Change |
|---|---|
| `constraint_codec.py` (new) | Authoritative placeholder codec, a transcription of notebook cell 86 (`encode_constraint` / `decode_constraint`). It also contains the limits (`INPUT_TOKEN_LIMIT = 512`, `MAX_NEW_TOKENS = 384`), malformed-marker detection, numeric-support check and structured warnings. It needs no torch, so it is unit-testable in `.venv`. |
| `ai_constraint_service.py` | <ul><li>**D1:** full decoding via the codec.</li><li>**D2:** `max_new_tokens = 384` and `early_stopping = True`, which are the notebook's validated settings. It also detects output that never ended (no end-of-sequence token).</li><li>**D3:** the unsupported-number check now runs on decoded text.</li><li>**D4:** the prompt is measured with `truncation = False`; if it is over 512 tokens the model is not run and an `INPUT_TOO_LONG` warning is returned.</li><li>`torch` / `transformers` are now imported lazily.</li><li>The response gains `warnings` and `model`; all old fields keep their meaning.</li></ul> |
| `teacher_approval_service.py` | `create_draft` copies well-formed warnings into the draft: `ai_warnings`, plus `warnings` on AI value items. Approval and finalisation logic is untouched. Drafts are stored as a JSON blob, so there is no schema change. |
| `tests/test_constraint_codec_and_service.py` + `tests/data/constraint_codec_samples.json` | 25 regression tests. The samples are 20 real `Value_Constraints` strings covering every marker in the dataset. Their expected encodings were produced by **executing the notebook's own cell-86 code**. |
| `dev_tools/constraint_codec_dataset_check.py` | Read-only check of the full dataset through the real tokenizer. |
| `docs/API_CONTRACT.md` | Documents the new response fields and warning codes. |

### The authoritative mapping (notebook cell 86, not invented)

- Bytes 33–126 except ``< > \ ^ ` { } ~ [ ]`` are kept as the literal character.
- Space → `[SP]`, newline → `[NL]`, tab → `[TAB]`.
- Every other byte, including each byte of a multi-byte UTF-8 character, becomes `[BXX]` (two hex digits).

The old backend table had only four entries. One of them was wrong: `[B3A]` was mapped to `&`, but 0x3A is `:`. The old code also collapsed all whitespace into single spaces, which destroyed multi-line constraints.

### Limits — evidence

| Limit | Value | Evidence |
|---|---|---|
| Output | 384 | <ul><li>Training `MAX_TARGET_LENGTH` was 384 (cell 88).</li><li>The longest encoded constraint among all 7,411 problems is exactly 384 tokens (P1599).</li><li>1,974 of 7,411 (27%) are longer than the old limit of 128.</li><li>`early_stopping = True` keeps beam search from running on after all beams have finished.</li></ul> |
| Input | 512 | <ul><li>Training `MAX_INPUT_LENGTH` was 512, and the model's `n_positions` is 512.</li><li>The longest training prompt was 476 tokens (cell 87).</li><li>89 dataset statements are longer than 512 and would previously have been cut silently.</li></ul> |

## Verification

| Check | Result |
|---|---|
| New regression tests | 25 / 25 pass |
| Full suite (`python -m unittest discover -s tests -t .`) | **495 tests OK** (470 previous + 25 new) |
| Full dataset round trip<br>encode → real `tokenizer.json` → decode | **7,411 / 7,411 exact**, 0 `<unk>` |
| Databases | `FYP_Database` (18 tables, 32,708 rows) and `FYP_E2E_Test` (19 tables, 46 rows) checked before and after. Every table's row count, row-content hash and the schema hash are **identical**. All reads ran in rolled-back transactions. |
| Model weights | Not touched: `models/flan_t5_constraint_final` has no diff. |

### Real-model comparison (before vs after)

**Setup**
- Old code: the frozen pre-fix `ai_constraint_service.py` from `HEAD`.
- New code: the current service.
- Both use the same loaded model, on CPU on this PC.

**Sample:** the training notebook's own evaluation sample (cell 93: `random.seed(123)`, 100 of the 716 **validation** problems). The test split was not used.

| Measure (100 problems) | Before | After |
|---|---|---|
| Presented text identical to the reference constraint | 11 | **21** |
| Text still containing undecoded markers (`[NL]`, `[B5E]`, …) | 63 | **0** |
| Output cut at the token limit | 20 (silent) | 1 (flagged `OUTPUT_TRUNCATED`) |
| Items with "unsupported numbers" | 80 | 71 |
| …of which flagged only because of marker digits (e.g. `5` from `[B5E]`) | 35 | 0 |
| Status `NEEDS_SEMANTIC_REVIEW` (clean, supported) | 20 | 29 |
| Latency per request, mean / median / p95 / max | 3.83 / 4.18 / 5.45 / 7.20 s | 3.85 / 3.70 / 7.57 / 17.01 s |

**How to read this.** The model and its predictions are unchanged. The 21 exact matches after the fix equal the 21 % the notebook measured for this same sample (cell 93); the old backend presented only 11 of them correctly. This is a **decoding / presentation fix, not an accuracy improvement of the model.** No case got worse: no problem that matched exactly before fails to match now.

Example (P3124):
- Before: `3 <= N <= 100000[NL]0 <= A[B5B]i[B5D] <= 10[B5E]9`, flagged unsupported `5`.
- After: `3 <= N <= 100000` / `0 <= A[i] <= 10^9`.

**Remaining genuine flags.** 71 of 100 suggestions still contain a number that does not appear in the problem statement. These are real hallucination warnings, which the model is known to produce (audit §3.1). They are correctly kept.

**Latency.**
- Mean latency is essentially unchanged.
- The worst case grew because the model can now run to 384 tokens. The single 17 s case (P6320) was a repetition loop (`0 <= object state <= 40; …`). It is now reported as `OUTPUT_TRUNCATED` instead of being cut silently at 128 tokens.
- The backend's 120 s AI timeout is unchanged and not approached.

## Behaviour notes for teachers / frontend

- Multi-line constraints now keep their line breaks.
- If a problem is longer than 512 tokens:
  - the draft has `ai_warnings: [INPUT_TOO_LONG]`;
  - the only AI value constraints are bounds stated explicitly in the problem, or none;
  - the teacher adds the value constraints with the existing `ADD` action;
  - approval and finalisation are unchanged.

## Not done (out of scope, unchanged)

- The model still runs even when explicit bounds are found, so their latency is not saved. This is the same behaviour as before.
- No LoRA, embeddings, RAG, test-input generation or frontend work.
