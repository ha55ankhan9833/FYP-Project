# AI RAG Phase 1 — implementation report (RAG OFF by default)

Date: 2026-11-17. Implements `docs/AI_RAG_CONSTRAINT_PLAN.md` (approval step 1).

What this phase did **not** do:
- enable RAG in the live backend;
- run the full evaluation;
- touch the test split;
- train, install or download anything;
- change the databases, grading pipeline, teacher approval or API behaviour;
- touch the frontend.

## Files

| File | Purpose |
|---|---|
| `rag_context.py` (new, pure Python) | **Example formatting:** whitelisted `input_format` + `value_constraints` only, in the encoded or raw format. **Prompt building:** with no examples, the prompt is byte-identical to the baseline. **Context selection:** see "How context selection works" below. **Reciprocal Rank Fusion** |
| `rag_retrievers.py` (new) | <ul><li>TF-IDF index (build / save / load, hash-verified).</li><li>FLAN-T5 encoder embedding: mean pool + L2; this is the generator's own encoder.</li><li>Retrievers: dense (bge / flan), TF-IDF, and hybrid (RRF of all three).</li><li>`RagContextProvider`.</li><li>`load_retriever`: every index must be split = train and hold the bge index's exact ID list.</li><li>`live_provider`: used only if `RAG_MODE != "off"`.</li></ul> |
| `rag_eval_metrics.py` (new, pure Python) | <ul><li>Validation dev / held-out split: `sha256(id) % 3 == 0` → dev, giving 239 dev / 477 held-out.</li><li>Validation-only guard.</li><li>Metrics: exact match, numeric match, number precision / recall, unsupported numbers split into copied vs invented, copy rate, latency.</li><li>Exact McNemar test; paired comparison.</li></ul> |
| `ai_constraint_service.py` | <ul><li>New `RAG_MODE = "off"` flag (with `RAG_K_MAX = 3` and `RAG_EXAMPLE_FORMAT = "encoded"`).</li><li>`generate_value_constraints(..., context_provider=None)`.</li><li>The D4 refusal runs before any retrieval.</li><li>A prompt over 512 tokens never reaches the model.</li><li>Any retrieval failure falls back to the baseline prompt with a `RETRIEVAL_UNAVAILABLE` warning.</li><li>RAG fields appear in the response **only** when `RAG_MODE != "off"`.</li></ul> |
| `problem_embedding_index.py` | Index format generalised: `kind` (dense / tfidf), dimensions 384 or 512, and a `rank()` method for raw ranking |
| `dev_tools/build_rag_indexes.py` (new) | Builds the FLAN-encoder and TF-IDF indexes from the verified training IDs; refuses to overwrite |
| `dev_tools/evaluate_rag.py` (new) | Resumable, validation-only harness: `run` (baseline / bge / flan / tfidf / hybrid, k-max, encoded / raw, dev / held-out) and `compare` (paired; all problems and the no-near-duplicate subset) |
| `tests/test_rag_context.py` (21 tests, pure Python), `tests/test_rag_retrievers.py` (6 tests, need numpy) | See "Verification" below |

### How context selection works

1. The prompt is measured with the real FLAN-T5 tokenizer, up to the 512-token limit.
2. Whole examples are added only if they fit; one that doesn't fit is skipped, never cut.
3. The problem itself is never cut.
4. Any candidate that is not a training-split problem is refused.
5. Excluded IDs and examples with duplicate content are skipped.

### Generated, git-ignored indexes

All three hold exactly the same 5,636 training IDs.

| Index | Contents | Truncated | Build |
|---|---|---|---|
| `models/embeddings/index_flan_train_v1` | 5,636 × 512; FLAN token lengths median 180 / p95 368 / max 462 | 0 | 635 s encoding, peak memory 1,115 MB |
| `models/embeddings/tfidf_train_v1` | 9,217 terms | — | 0.6 s |
| `models/embeddings/index_train_v1` (bge, existing) | unchanged | — | — |

## Verification

| Check | Result |
|---|---|
| AI tests in `.venv_ai`<br>(RAG context, retrievers, embedding index, D1–D4) | **71 / 71 pass, 0 skipped** |
| Full backend suite in `.venv` | **541 tests OK** (514 previous + 27 new). 15 skipped because `.venv` has no numpy; all of them run and pass in `.venv_ai` |
| RAG-off: unit tests | <ul><li>`RAG_MODE` is `"off"` by default.</li><li>The model receives the original prompt byte-for-byte.</li><li>The response has the same keys as before, with no `retrieval` field.</li></ul> |
| RAG-off: real model vs committed `HEAD` | Full responses **identical** for 5 cases: P009, P024, P042, a new prime problem, and a 1,430-token over-limit problem |
| Short / typical / longest validation prompts with the real tokenizer and the flan and tfidf retrievers, k_max = 5 | Every prompt ≤ 512 tokens, and the measured count equals the reported count. The problem text is intact at the end of the prompt. The longest problem (> 440 tokens) still works |
| Boundaries (unit) | <ul><li>Problem prompt exactly at the limit → baseline prompt, examples skipped.</li><li>Limit − 1 → RAG refused.</li><li>> 512 → D4 refusal, provider never called.</li><li>A selection over 512 is rejected → fallback.</li></ul> |
| Leakage | <ul><li>bge, flan and tfidf indexes each contain exactly the 5,636 training IDs.</li><li>Validation, test and unknown IDs are refused when an index loads and during context selection.</li><li>Retrieved IDs for validation queries are all training IDs.</li><li>The harness refuses test-split IDs.</li><li>Dev and held-out are disjoint, together cover exactly the 716 validation problems, and include no test IDs.</li></ul> |
| Field whitelist | Solution, test cases, sample I/O, description and expected output never appear in the prompt (unit test with marked values) |
| Fallback | <ul><li>Provider exception → baseline prompt plus `RETRIEVAL_UNAVAILABLE`.</li><li>Retriever module unavailable → same.</li><li>Draft creation, approval and finalisation are unaffected.</li></ul> |
| Harness smoke run | 3 dev problems, baseline vs hybrid: rows written, `compare` works. **Too few problems to mean anything** (0/3 exact in both). Hybrid used 2–3 examples per problem; prompts were ≤ 495 tokens. Latency was 3.5 s baseline vs 5.4 s RAG |
| Databases | `FYP_Database` and `FYP_E2E_Test` identical to the pre-AI-work snapshot |
| Execution gate | `get_secure_sandbox()` → `None` |

## Remaining risks

1. **The model never saw context in training.** Inference-time RAG may be ignored, may be copied wrongly, or may lower accuracy. Only the evaluation can tell; the decision rule is fixed in the plan.
2. **Budget pressure.** Encoded examples average about 150 tokens; for long problems, few or no examples fit. This is reported per problem (`k_used`, `skipped_for_budget`).
3. **Latency.** Context prompts are longer. The smoke run showed about 5.4 s vs 3.5 s, but 3 problems is not a measurement.
4. **Templated near-duplicates.** 142 of 716 validation problems have a near-duplicate in training, which can inflate exact match. The harness always reports the no-near-duplicate subset.
5. **Hallucination accounting.** The D3 check counts numbers copied from examples as unsupported. That is correct — the teacher must still verify them — and the harness separates copied numbers from invented ones.
6. **Memory if enabled.** The hybrid mode loads bge (about 150 MB) next to FLAN-T5. bge and the FLAN encoder are not loaded at all while RAG is off.
7. **Live enablement** would need a code change to `RAG_MODE` plus approval. There is no request parameter that turns it on.

## Ready for the separate evaluation phase

The commands below follow the plan's stages; there are about 3.5 h of CPU in total. The `--out` paths are suggestions; they should be outside git, or in a git-ignored folder.

```
set O=<original dataset .json>
set S=<FYP_Complete_Dataset.json>
python dev_tools\evaluate_rag.py near-duplicates --original %O% --splits %S% --out results\near_duplicates.json
REM every run below also takes: --near-duplicates results\near_duplicates.json
python dev_tools\evaluate_rag.py run --original %O% --splits %S% --config baseline --subset dev --out results\dev_baseline.jsonl
python dev_tools\evaluate_rag.py run ... --config bge|flan|tfidf|hybrid --k-max 3 --format encoded --subset dev --out results\dev_<cfg>.jsonl
python dev_tools\evaluate_rag.py run ... --config <best> --k-max 1|3|5 --format encoded|raw --subset dev ...
python dev_tools\evaluate_rag.py run ... --subset heldout (baseline and the chosen config)
python dev_tools\evaluate_rag.py compare results\heldout_baseline.jsonl results\heldout_<best>.jsonl
```
(`python` = `.venv_ai\Scripts\python.exe`)
