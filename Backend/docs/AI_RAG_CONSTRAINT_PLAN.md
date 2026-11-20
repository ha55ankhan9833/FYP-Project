# AI Phase 3 plan — RAG-assisted value-constraint generation

Status: **PLAN ONLY — awaiting approval.** No RAG code, training, download, migration or frontend
work has started. Prepared 2026-11-17 after commit `dd9a448`.

## 0. Starting facts (measured)

| Fact | Value | Source |
|---|---|---|
| Baseline generator | fine-tuned FLAN-T5-small, 512-token input, 384-token output, beam 4 | `ai_constraint_service.py` (D1–D4) |
| Baseline accuracy | 21/100 exact on the notebook's validation sample | `docs/AI_CONSTRAINT_FIXES_D1_D4.md` |
| Retrieval corpus | 5,636 training problems only | `models/embeddings/index_train_v1` |
| Proxy retrieval (exact-template labels) | FLAN encoder > TF-IDF > bge | `docs/AI_EMBEDDING_RETRIEVAL_RESULTS.md` |
| What the proxy shows | It measures template identity, not semantic relatedness. It cannot rank the retrievers for RAG; only measured generation quality can | — |

Measured budget for the 716 validation problems (FLAN-T5 tokenizer, read-only):

| Item | Median | p95 | Max |
|---|---|---|---|
| Base prompt, no context | 197 | 392 | 468 (0 over 512) |
| One example (input format + constraints), **raw** constraints | 83 | 163 | — |
| One example, constraints **encoded** in the model's placeholder format | 150 | 298 | — |

How many of bge's top-5 examples (after folding near-duplicates) fit in 512 tokens:

| Examples that fit | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Raw constraints | 23 | 111 | 145 | 103 | 135 | 199 |
| Encoded constraints | 72 | 234 | 192 | 104 | 61 | 53 |

**Near-duplicates:** 142 of 716 validation problems have a training problem with bge cosine ≥ 0.95.

**Raw vs encoded:**
- Raw constraints lose `<`, `^`, `{`, `}`: the model's tokenizer maps them to `<unk>`.
- Encoded constraints (`[B3C]=`…) match exactly what the model learned to *output*, but cost about 1.8× the tokens.
- Both representations are compared (§3, stage 2b).

## 1. What changes and what does not

- **Baseline preserved:** model weights, the baseline prompt and the D1–D4 behaviour are unchanged. RAG is a separate, opt-in prompt path.
- With RAG **off** (the default), the prompt must be byte-identical to today's; a regression test enforces this.
- This is **inference-time RAG only (Experiment B).** The model was not trained with context. A model trained with context is a later LoRA experiment (D) and is not part of this phase.
- Unchanged: test-case generation, grading, Qwen, databases, the execution gate, the frontend.

## 2. Retrieval methods to compare

All four use the same corpus: 5,636 training problems, text = description + input format + output format.

| Method | How | New artifact (git-ignored) |
|---|---|---|
| `bge` | existing index (384-d, CLS, L2) | none |
| `flan` | mean-pooled encoder of the **same** FLAN-T5 constraint model, L2-normalised. The generator's encoder is already in memory, so this adds no extra model | `models/embeddings/index_flan_train_v1` (5,636 × 512). The index format is generalised to record its dimension and model |
| `tfidf` | lexical TF-IDF (NumPy; sublinear tf, smoothed idf, L2) | `models/embeddings/tfidf_train_v1` (vocabulary, idf, sparse doc weights) |
| `hybrid` | **Reciprocal Rank Fusion** of bge + flan + tfidf: score = Σ 1/(60 + rank) | none |

RRF has no weights to tune, so no tuning on evaluation data is needed. Weighted score mixtures are not used for this reason.

## 3. Leakage-safe context selection

### Query flow (identical in evaluation and in the service)

1. Build the baseline prompt and measure it with the FLAN-T5 tokenizer, `truncation=False`. If it is over 512 tokens, keep the D4 behaviour: `INPUT_TOO_LONG`, model not run. RAG cannot help when the problem alone does not fit.
2. Retrieve candidates from the **training-only** index:
   - the index is verified at load against the split map, so validation, test and unknown-split IDs are refused;
   - near-duplicates among candidates are folded (cosine ≥ 0.95);
   - take up to 10 candidates.
3. **Context fields:**
   - included: the neighbour's `Input_Specification` and `Value_Constraints` only;
   - never included: description, solution, test cases, sample input/output, code hints.
4. **Greedy budget fill:**
   - Walk the candidates in rank order, adding each **whole** example only if `base + header + example ≤ 512`; otherwise skip it and try the next.
   - Stop at `K_max`.
   - Examples are never cut, and the new problem is never cut.
   - The final prompt is asserted to be ≤ 512 before `generate()`.
   - Record `k_used`, `context_tokens` and `skipped_for_budget`.
5. **Prompt layout** (fixed):
   ```
   Generate value constraints for the following C++ programming problem.
   Similar solved problems:
   Input Format: …  Value Constraints: …          (k_used times)
   Problem: …
   Input Format: …
   Output Format: …
   ```
   If `k_used` = 0, the prompt is exactly the baseline prompt.

### Evaluation leakage rules

- Queries are validation problems only. The 709 test problems are never loaded by any RAG tool; a test enforces that the test-split IDs are refused.
- **Dev / held-out split of validation:** a problem is in `dev` if `sha256(problem_id) mod 3 == 0` (about 239), otherwise `held-out` (about 477). The rule is deterministic and documented.
  - All choices (retriever, K, raw vs encoded) are made on `dev` only.
  - The final baseline-vs-RAG comparison uses `held-out` only.
- Results are always reported for **all** queries and for the **no-near-duplicate** subset (bge top-1 < 0.95). Copying the constraints of a near-identical template inflates exact match.

## 4. Evaluation protocol

Every run produces one JSONL row per problem. Runs are resumable, run in the background, and use the same problems for every configuration, so comparisons are paired.

| Stage | Data | Configurations | CPU estimate |
|---|---|---|---|
| 1 | dev (~239) | baseline (no RAG) | ~15 min |
| 2a | dev | bge / flan / tfidf / hybrid, encoded examples, K_max = 3 | ~72 min |
| 2b | dev | best retriever from 2a, raw vs encoded, K_max ∈ {1, 3, 5} | ~55 min |
| 3 | held-out (~477) | baseline vs the single best dev configuration | ~66 min |

The estimates use the measured ~3.85 s per baseline generation and an assumed ~4.5 s with context. Total ≈ 3.5 h of CPU.

**Metrics for every configuration:**

| Area | Metric | Definition |
|---|---|---|
| Accuracy | exact match | decoded output `.strip()` == reference (same as notebook cell 93) |
| Accuracy | numeric match | same list of numbers as the reference (notebook's diagnostic) |
| Accuracy | number precision / recall | set overlap of numbers vs the reference |
| Validity | clean decode | no `DECODE_*` warning |
| Validity | complete | no `OUTPUT_TRUNCATED`; non-empty |
| Hallucination | unsupported numbers | D3 check: numbers not in the problem statement |
| Hallucination | split of unsupported numbers | copied from a retrieved example vs present in neither |
| Copying | copy rate | output == one retrieved example's constraints, split into correct copies and wrong copies |
| Truncation | input | `INPUT_TOO_LONG` (expected 0 on validation); examples skipped for budget; `k_used` distribution |
| Truncation | output | `OUTPUT_TRUNCATED` rate |
| Latency | timing | retrieval ms; generation s; end-to-end mean / median / p95 |

**Significance:**
- Exact match uses a paired **exact McNemar test** (binomial on discordant pairs, computed with `math.comb`; no scipy).
- Wins and losses are listed per problem ID.

**Decision rule** (stated before running):
- RAG is recommended for the service **only if**, on held-out:
  1. exact match improves with McNemar p < 0.05;
  2. the unsupported-number rate does not rise by more than 2 percentage points;
  3. validity does not fall.
- Otherwise the baseline stays, and the result is reported as a negative or inconclusive finding.

**No accuracy claim is made in advance.**

## 5. Integration with the Flask constraint-draft workflow (only after a positive decision and approval)

| Component | Change |
|---|---|
| `ai_constraint_service.py` | `generate_value_constraints(..., retrieval=None)`. `None` = today's behaviour; byte-identical prompt is tested. The RAG mode is a server-side constant, default **off**; teachers cannot switch it |
| new `rag_context.py` | retriever wrappers (bge / flan / tfidf / RRF), budget-fill, prompt builder. Pure logic is unit-testable |
| `ai_local_api.py` (port 5001, `.venv_ai`) | loads the chosen retriever once at start-up. If retrieval fails → generate **without** context and add a `RETRIEVAL_UNAVAILABLE` warning. The request never fails because of RAG |
| response `model.retrieval` | `{method, k_used, context_tokens, skipped_for_budget, examples: [{problem_id, score}]}`. Value-item `status` rules are unchanged (D3 still flags numbers not in the statement, including copied ones) |
| `teacher_approval_service.create_draft` | adds `ai_context` (neighbour IDs and scores) for display. Review, approval and finalisation are unchanged |
| `docs/API_CONTRACT.md` | new optional fields and warning code |

**Memory:**
- `flan` adds nothing beyond the loaded generator.
- `bge` adds about 150 MB plus the 8.7 MB index.
- `tfidf` adds a few MB.

## 6. Tests to add

- **Prompt:**
  - RAG off → prompt byte-identical to baseline;
  - `k_used = 0` → baseline prompt;
  - layout and field whitelist (no solutions, tests or sample I/O; no description of neighbours).
- **Budget:**
  - never over 512;
  - examples skipped, never cut;
  - the problem itself is never cut;
  - boundary at 511 / 512 / 513;
  - D4 refusal still happens before retrieval.
- **Leakage:**
  - every index (bge, flan, tfidf) contains exactly the 5,636 training IDs;
  - validation, test and unknown IDs are refused;
  - the evaluation tool refuses test-split IDs;
  - dev / held-out assignment is deterministic and disjoint.
- **RRF:** ordering, tie-breaking, and duplicates across retrievers.
- **Fallback:** retriever exception → baseline generation plus `RETRIEVAL_UNAVAILABLE`.
- **Draft:** `ai_context` is carried through; approval is unaffected.
- **McNemar helper:** known values.

## 7. Approvals requested (separately)

1. **Implement** the retrievers (FLAN-encoder and TF-IDF artifacts built offline from training problems only), the RAG prompt builder, the evaluation harness and the tests. This is code only, with no downloads and no installs; the service stays at RAG **off**.
2. **Run** stages 1–3: about 3.5 h of background CPU, read-only on datasets and databases, validation split only.
3. Later and only if the decision rule is met: **enable** RAG in the constraint service, wired into the draft workflow.
