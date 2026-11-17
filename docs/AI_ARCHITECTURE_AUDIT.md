# FYP AI Architecture Audit — Dataset, Fine-Tuning, Embeddings, RAG, Test-Case Generation

Audit date: 2026-11-17. **Read-only.** No code, model, dataset or database was changed; nothing was
installed, downloaded or trained. Every number below was measured during this audit or is quoted from a
saved notebook output (cell numbers given). Anything that is an estimate is labelled **(estimate)**.
No model accuracy figures are invented; where a metric does not exist yet, the report says so.

Status labels used throughout:

| Label | Meaning |
|---|---|
| **VERIFIED** | Implemented and verified by evidence (files, logs, tests, notebook outputs) |
| **UNVALIDATED** | Exists, but has no credible validation evidence |
| **MISSING** | Must be implemented |
| **OPTIONAL** | Enhancement; not required to meet the supervisor requirement |

---

## 1. Verified original dataset

| Property | Verified value |
|---|---|
| Primary path | `D:\Combine dataset to p2237 to p4411\FYP_DATASET_P001_P7411_FINAL_VERIFIED\FYP_DATASET_P001_P7411_FINAL_VERIFIED.json` |
| Identical copy | `D:\Auto-Test-Case-Generator\C++ Dataset Creation\Claude updated generation\FYP_DATASET_P001_P7411_FINAL_VERIFIED\` |
| SHA-256 (json) | `b77745760e7ff750ff204c8093532035dca40326b291bc565a8c3e0a0a4f6b09` (both copies) |
| SHA-256 (jsonl) | `ecebda3322328e860d88055080eb71ba86f89407472846613c6371f973bead8e` (both copies) |
| SHA-256 (csv) | `e4255a00eb15b5a02ec837b625e1aa3d330247e9c99342a22686cd5b0a8f7c69` (both copies) |
| Size / lines (json) | 48,586,201 bytes (46.34 MB) / 88,934 lines — identical to notebook cell 1 output |
| Problems | 7,411, IDs P001..P7411 sequential, no gaps, no duplicates |
| Fields | exactly 10, same order in every record |
| Test cases | 74,110 = 10 per problem; 0 malformed; `Test_Cases` stored as a JSON **string** |
| JSONL / CSV | record-for-record identical to the JSON |

### 1.1 Field profile (characters)

| Field | Empty | Median | p95 | Max | Exact-duplicate values |
|---|---|---|---|---|---|
| Problem_Description | 0 | 414 | 1,079 | 1,979 | 0 |
| Input_Specification | 0 | 131 | 291 | 546 | 3,563 |
| Output_Specification | 0 | 84 | 254 | 390 | 2,161 |
| Sample_Input | 0 | 12 | 60 | 533 | 2,899 |
| Sample_Output | 0 | 3 | 34 | 384 | 4,228 |
| Value_Constraints | 0 | 60 | 166 | 261 | 3,405 |
| Code_Constraints | 0 | 110 | 389 | 1,016 | 4,264 |
| CPP_Solution | 0 | 777 | 2,178 | 5,954 | 2 |
| Test_Cases (JSON text) | 0 | 728 | 7,174 | 2,210,844 | 8 |

Observations (all measured):

* **Templated text.** Thousands of problems share identical Input/Output specifications and constraint
  strings (e.g. one constraint string is shared by 127 training problems — notebook cell 82). This matters for
  retrieval (near-duplicates) and for evaluation (exact-match is inflated by memorised templates).
* **Descriptions are near-unique:** only one pair of descriptions is identical after lower-casing and
  punctuation normalisation.
* **Sample I/O leaks into tests:** in all 7,411 problems the `Sample_Input` equals one of the 10 test inputs.
  Sample I/O must therefore never be put in a prompt whose target is a test case of the same problem.
* **Test cases:** input length median 15 chars, p95 260, max 2,077,733; 299 inputs exceed 10,000 chars;
  27 blank inputs, 45 blank outputs; no problem has duplicate inputs among its 10. Input line counts are
  varied (0 newlines: 11,682; 5+ lines: 14,945) — multi-line structured inputs are the norm, not the exception.
* **CPP_Solution:** all 7,411 contain `main(` and read stdin; 7,409 distinct. These are usable as offline
  oracles for evaluation (executed only in an isolated Linux/Colab environment, never on the Windows host).
* **Code_Constraints** are mostly *algorithmic guidance / complexity statements*
  ("Use a one-pass prefix-sum…", "Time Complexity: O(N·|P|)") — **not** the enforceable student-code
  restrictions the backend models (`recursion_required`, `loops_prohibited`, …). They must not be used as
  training targets for mandatory code constraints.
* **Bracket placeholders:** the raw dataset contains no `[SP]`/`[B3C]`/`[B3E]`/`[B3A]` markers. Those markers
  are created by the notebook encoders (§2.5). A handful of literal bracket strings exist in data
  (`[NL]`×4, `[TAB]`×2 in tests, `[26]`-style array indices in C++ code) — any encoder must escape `[` and `]`
  (the constraint encoder does).
* A Hugging Face cache entry `datasets--ByteDance-Seed--Code-Contests-Plus` exists on this PC. The notebook
  never references it; it is **not** part of this dataset's lineage.

---

## 2. Existing preprocessing (verified from the notebook)

Notebook: `G:\My Drive\Colab Notebooks\FYP_Auto_Test_Case_Generator.ipynb` (168 cells, Colab, Tesla T4).

| # | Operation | Evidence | What actually happened |
|---|---|---|---|
| 1 | Load | cells 1–4 | `/content/FYP_DATASET_P001_P7411_FINAL_VERIFIED.json` (uploaded); 7,411 problems, 74,110 cases, 0 missing fields |
| 2 | Field selection | cell 11 | Prompt uses **only** Problem_Description, Input_Specification, Output_Specification, Value_Constraints. Sample I/O, Code_Constraints, CPP_Solution never used for training |
| 3 | Record unit | cell 11 | one record per **individual test case** (`problem_id`, `test_number`, `input_text`, `target_text` = JSON `{"input","expected_output"}`) |
| 4 | Target-length filter | cell 11 | drop a case if its JSON target > **256** tokens → 74,110 → 70,526 (−3,584) |
| 5 | Split | cell 12 | `sklearn.train_test_split` on **sorted unique problem IDs** (string sort), 80/20 then 50/50, `random_state=42` → 5,914 / 739 / 740 problems. Problem-level; asserted disjoint |
| 6 | Input-length filter | cell 14 | drop a case if its prompt > **512** tokens → 67,475 (−3,051) |
| 7 | Saved | cell 16 | `train/validation/test.json` (53,829 / 6,885 / 6,761) |
| 8 | Quality audit | cells 18–19 | 0 empty fields, 0 exact duplicate pairs, 0 invalid JSON; 27 blank inputs, 45 blank outputs |
| 9 | Blank quarantine | cell 25 | drop cases whose input **or** output is blank/whitespace → 67,410 (−65); saved as `*_clean.json` (originals kept) |
| 10 | Constraint dataset | cell 84 | one record per problem from the clean splits; target = Value_Constraints text extracted by regex → 5,636 / 716 / 709 |

**Not performed:** no Unicode/whitespace normalisation (inputs are kept byte-exact — correct for tests);
no near-duplicate detection; no de-duplication of templated statements; no cross-split near-duplicate check.

### 2.1 Why exactly 350 problems are absent (reproduced exactly)

Re-running cells 11/14/25 with the deployed model's own `tokenizer.json` (local file) reproduces the
processed dataset **exactly** (same 7,061 problem IDs, same 67,410 cases):

| Cause | Problems | Rule |
|---|---|---|
| Every one of the 10 targets > 256 tokens | **18** | cell 11 — P1415, P1488, P1494, P1554, P1555, P1565, P1566, P1567, P1569, P1573, P1574, P1576, P1588, P1614, P1615, P1621, P1623, P1635 |
| Prompt > 512 tokens (all 10 cases share the prompt) | **332** | cell 14 |
| All cases blank | 0 | cell 25 removed 65 cases but no whole problem |

The 18 were removed **before** the split, so they were never assigned to train/val/test. The 332 were
assigned a split in cell 12 and removed afterwards; their split membership was not saved.

### 2.2 Selection bias (measured)

Dropped problems by ID range: P1–1000: 19 · P1001–2000: 69 · P2001–3000: 4 · P3001–4000: 97 ·
P4001–5000: 5 · P5001–6000: 0 · **P6001–7000: 156** · P7001–7411: 0.

The dataset was built in batches (different generation runs), so length filtering removed whole *styles* of
problems unevenly — mainly long, multi-clause statements. In addition, the 3,584 dropped large-output cases
are disproportionately **stress / maximum-size tests**. Consequences: the model never saw the longest
statements, and it learned a distribution skewed toward small inputs (bad for boundary/maximum tests). Any
final report must state this.

### 2.3 Prompt construction (verified)

```
Generate one valid C++ test case.
Problem: …
Input Format: …
Output Format: …
Value Constraints: …
Test Case Number: N
```
Constraint task (cell 84): `Generate value constraints for the following C++ programming problem.` +
Problem / Input Format / Output Format (constraints removed, become the target).

### 2.4 Token limits

| Item | Value |
|---|---|
| Test-case input limit | 512 (cells 13–14) |
| Test-case target limit | 256 (cell 11) |
| Constraint model input / target limit | 512 / 384 (cell 88) |
| Constraint prompt length, all 7,411 (measured now) | median 203, p95 430, max 846; **89 > 512** |
| Encoded constraint target, all 7,411 | median 97, p95 226, max 384 |

### 2.5 Special-character encoding (`[SP]`, `[B3C]`, …)

FLAN-T5's SentencePiece vocabulary cannot represent `{ } \ < ~ ^` and does not preserve newlines
(cells 28–31: 0/100 JSON targets round-tripped). The notebook therefore tried:

* **HEX** targets (cell 32) — lossless, but the trained model looped (`3120310A…`) and never emitted EOS (cells 47–49).
* **Natural V1–V3** (cells 50–58): `[SP]`, `[NL]`, `[TAB]`, `[Bxx]` byte markers; V3 decoder round-trips
  **67,410/67,410** exactly (cell 58).
* **Constraint encoder** (cell 86): escapes bytes outside 33–126 and the characters ``<>\^`{}~[]`` as
  `[B<hex>]`, plus `[SP]/[NL]/[TAB]`; 5,636/5,636 exact round-trips, 0 `<unk>`.

### 2.6 Better representation of all 7,411 problems (PROPOSED, not done)

1. **Put the fields that matter for the task first** (Input Format, Value Constraints), then the description,
   and truncate the *description tail* instead of dropping the problem → all 7,411 problems usable.
2. **Generate inputs only.** Input-only targets are tiny (median 9 tokens, p95 131; 71,959/74,110 ≤ 256),
   so far fewer cases are lost, and expected outputs come from execution (§8) — never from the model.
3. Keep the existing split for the 7,061 problems; reproduce cell 12 in Colab (same sorted ID list +
   `random_state=42`) to recover the 332 problems' original split; put the 18 (never split) in train.
4. Keep using a lossless escaping scheme (constraint encoder or Natural V3), and train the decoder side on it.

---

## 3. Existing fine-tuning evidence

### 3.1 Constraint model — `models\flan_t5_constraint_final` — **VERIFIED (trained), weakly validated**

| Item | Evidence |
|---|---|
| Base | `google/flan-t5-small` (cell 89), T5 encoder–decoder, 8+8 layers, d_model 512, 6 heads, vocab 32,128, 76,961,152 parameters |
| Method | **Full fine-tuning** — "Trainable Parameters: 76,961,152" (cell 89). No adapter files (`adapter_config.json`, `adapter_model.*`) exist anywhere → **not LoRA** |
| Data / task | `constraints_train.json` (5,636) → generate **encoded Value_Constraints** from Problem + Input/Output Format. Validation: 716 |
| Hyper-parameters | 3 epochs, batch 4 × grad-accum 2 (effective 8), LR 1e-4 linear decay, FP32, eval/save each epoch (cell 90; trainer_state) |
| Training log | `trainer_state.json`: loss 3.05 (step 50) → ~0.52 (step 2,100); 2,115 steps; eval_loss **0.2884 → 0.2281 → 0.2143**; 12.75 min on T4 (cell 91) |
| Deployed weights | SHA-256 of `model.safetensors` is identical in backend, Drive `flan_t5_constraint_final`, and `checkpoint-2115` (final epoch) |
| Generation metric | 100 validation problems, beam 4: decode 100/100, **exact match 21/100**, numeric-token match 32/100 (cell 93). Error analysis: operator differences 43, numeric 68 (cell 94). Exact match 33% when all numbers appear in the prompt vs 16% when they do not (cell 95) |
| Test split | never evaluated → still clean for a final evaluation |

Interpretation: the model has genuinely learned the *format* of constraints but frequently hallucinates
numbers that are not in the statement — which is why the backend already prefers explicitly stated bounds
and marks AI numbers as unverified.

### 3.2 Experimental test-case checkpoints — **UNVALIDATED, not suitable**

| Checkpoint | Training | Target format | Evidence |
|---|---|---|---|
| `flan_t5_small_fp32_v1` | 989 examples, 1 epoch, LR 1e-4, FP32, loss 4.68 | HEX | First FP16 run produced loss 0.0 / grad NaN (cell 42). FP32 model loops `3120310A…`, no EOS (cells 47–49) |
| `flan_t5_small_natural_v2` | 970 examples, 1 epoch, loss 2.84 | Natural V3 | Degenerate repetition `1[SP]1[SP]…`, no `OUTPUT:`, no EOS (cells 67–70); teacher-forced token accuracy 72% on 20 examples (cell 71) |
| `flan_t5_small_natural_v3` | v2 + 2 epochs, loss 2.05 | Natural V3 | Still repetitive (cells 77–78). P009 multi-sample: complete outputs, but generated inputs **invalid** (wrong column count, bad first line, N out of range — cell 80) |

All three `experiment_info.json` files say `"status": "trained_not_yet_validated"`. They were trained on
~1.4% of the available cases for 1–3 epochs, with no validation loss and no validity metric. They are useful
only as a documented negative result ("too little data + output-in-target caused degenerate generation").

---

## 4. Current vs intended AI pipeline

### 4.1 Current (verified in code)

```
Teacher → POST /api/ai/analyze (main app, JWT teacher)          ai_backend_routes.py
        → POST 127.0.0.1:5001/analyze (separate .venv_ai)       ai_local_api.py
        → analyze_cpp_problem()                                  ai_constraint_service.py
             explicit bounds regex (numeric_constraint_validator) – preferred if found
             else FLAN-T5 constraint model (beam 4)               – status UNVERIFIED/NEEDS_SEMANTIC_REVIEW
             code constraints: regex patterns                     – NEEDS_TEACHER_REVIEW
        → draft → /api/ai/drafts … approve/finalize              teacher_approval_routes.py / _service.py
        → approved constraints on dbo.Assessment
Test cases: generate-test-cases → preflight (approved constraints, APPROVED reference, hash, none existing)
        → deterministic inputs (generic_test_inputs: ≤3 integer variables, one line)
          + optional teacher `additional_inputs` (≤10)
        → expected outputs ONLY from the approved reference (sandbox, or SHA-allow-listed trusted host)
          or a verified Python oracle bound to that reference hash (prime_check_v1 only)
        → exactly 10 tests (test_case_policy.REQUIRED_TEST_CASE_COUNT = 10), run twice for determinism
```

There is **no** AI in test-case generation today: inputs are deterministic and limited to ≤3 integer ranges.

### 4.2 Defects found in the current AI path (not fixed — read-only)

| ID | Finding | Measured impact |
|---|---|---|
| D1 | `clean_constraint_text` decodes only `[SP] [B3C] [B3E] [B3A]`. The training encoder also emits `[B5E]` (^), `[B5B]/[B5D]` ([ ]), `[NL]`, `[B7B]/[B7D]` ({ }), `[B7E]`, `[B5C]`, UTF-8 bytes | **4,257 / 7,411 (57%)** of dataset constraints need a marker the backend does not decode (e.g. `10^9` would display as `10[B5E]9`). `[B3A]`→`&` is wrong (0x3A is `:`) but inert — the encoder never emits it |
| D2 | `max_new_tokens=128` at inference vs 384 in training | **1,974 / 7,411 (27%)** encoded constraint targets exceed 128 tokens → silently truncated suggestions |
| D3 | `unsupported_numbers` uses `\d+` on the *partly decoded* text | Undecoded markers such as `[B5E]` inject a spurious "5" → false UNVERIFIED flags |
| D4 | Prompt truncation at 512 with no warning | 89 / 7,411 dataset statements would be truncated; teachers are not told |

These are small, isolated fixes inside `ai_constraint_service.py` (P0 in §14). D1+D2 directly affect what
teachers see.

**Update 2026-11-17:** D1–D4 FIXED (code only, model unchanged). See `docs/AI_CONSTRAINT_FIXES_D1_D4.md`.

### 4.3 Intended

```
New problem → validation → embedding → cosine top-K (train-only index) → trustworthy examples
  → fine-tuned generator (+ RAG context) → value-constraint draft (+ regex code constraints)
  → deterministic constraint validation → teacher approval (existing service)
  → approved constraints + approved reference solution
  → generator proposes N candidate INPUTS (inputs only) → format/constraint validator → dedupe/diversity
  → teacher review → existing pipeline executes the APPROVED reference (or verified oracle) for outputs
  → exactly 10 approved hidden tests
```

---

## 5. Recommended model and LoRA approach

### 5.1 Is LoRA justified?

FLAN-T5-small (77 M) already fits full fine-tuning on a free T4 in minutes, so LoRA's usual benefit
(memory) is small here. LoRA *is* justified for these concrete, measurable reasons:

1. **Keep the working baseline frozen.** The deployed constraint model stays byte-identical; new behaviour
   lives in separate adapter files → no regression risk to the working backend.
2. **One base, several tasks.** A *constraint* adapter and a *test-input* adapter share one 296 MB base;
   each adapter is ~3–6 MB (estimate) — versionable in git and easy to demonstrate.
3. **Clean experimental evidence:** trainable-parameter count, adapter weight files, loss curves, and a
   before/after comparison against the frozen base.

Risks: LoRA can underperform full fine-tuning on a model this small; adapter rank too low → underfitting.
That is why Experiment A (existing full fine-tune) remains the baseline and LoRA must beat it on held-out
metrics to be adopted. Do **not** stack a LoRA adapter on the constraint model just "to have a layer".

### 5.2 Configuration (proposed)

| Item | Recommendation |
|---|---|
| Library | Hugging Face PEFT (`LoraConfig(task_type=SEQ_2_SEQ_LM)`) — training in Colab only |
| Target modules | T5 attention `q`, `v` (start); `q,k,v,o` if underfitting |
| Rank / alpha / dropout | r = 16, α = 32, dropout 0.05 (start); ablate r ∈ {8, 16, 32} |
| Trainable params | q,v: 48 matrices × r(512+384) = **688,128 (0.89%)**; q,k,v,o: **1,376,256 (1.79%)**; everything else frozen |
| Base for test-input adapter | `google/flan-t5-small` (clean) — the constraint fine-tune is a different task |
| Base for constraint adapter | compare (i) existing full fine-tune [baseline A] vs (ii) base + LoRA [C], (iii) + RAG [D] |
| Precision | FP32 (FP16 produced NaN loss in cell 42) or BF16 if the GPU supports it |
| Inputs / targets | constraint task: as cell 84 (+ RAG context for D); test-input task: §8.2 |
| One multitask model vs separate adapters | **Separate adapters** — different targets, independent evaluation, independent rollback |
| Saving / versioning | `save_pretrained()` → `adapter_config.json` + `adapter_model.safetensors` under `models/adapters/<task>_<version>/` with SHA-256, data hash, config and metrics in a `training_record.json` |
| Loading | either PEFT at runtime (needs `peft` in `.venv_ai`) **or** `merge_and_unload()` in Colab and ship a merged checkpoint (no new local dependency); keep adapter files as evidence |
| Proof of training | trainer_state.json loss/eval curves, adapter weight norms ≠ 0, trainable-param printout, base-model hash unchanged, metric deltas vs frozen base |

---

## 6. Embeddings and cosine-similarity retrieval

| Decision | Recommendation and reason |
|---|---|
| Model | `sentence-transformers/all-MiniLM-L6-v2` (22 M params, 384-dim, trained for semantic similarity, fast on CPU). Can be run with the already-installed `transformers` + mean pooling; `sentence-transformers` is optional. FLAN-T5 encoder pooling is *not* trained for similarity and is a weaker fallback. A TF-IDF/BM25 index is the non-neural baseline for comparison |
| Text embedded | Constraint task: **Description + Input Format + Output Format** (what a teacher provides). Test-input task: the same **plus approved Value_Constraints**. Never Sample I/O, tests or solutions |
| Field order | Description first, then formats — the model truncates at 256 word-pieces (estimate: most descriptions fit; the tail of the longest is cut) |
| Normalisation | L2-normalise every vector; cosine = dot product |
| Storage | `float32` matrix 7,411 × 384 = **11.4 MB** (`.npy`) + `metadata.json` (problem_id, split, field hashes, text hash, model name + revision) |
| Search | NumPy brute force (one 7,411 × 384 mat-vec per query, milliseconds). **FAISS is not needed** at this scale |
| Top-K | K = 3–5, then budget-fill (§7) |
| Duplicates | Exact: text hash. Near: cosine ≥ 0.95 flagged; collapse templated near-duplicates so the K examples are diverse |
| Leakage | Separate indexes per experiment: **train-only** index for all evaluation; validation/test problems never in the corpus; the query's own ID always excluded |
| Demo display | matched problem IDs, cosine scores, the field shown to the model (constraints only — never solutions or expected outputs) |

---

## 7. RAG-assisted constraint generation

**Definitions (for the report):** *Fine-tuning* changes model weights using our dataset. *Embeddings* map a
problem's text to a vector. *Cosine similarity* scores how close two vectors point. *RAG* inserts the
retrieved neighbours' approved constraints into the prompt at inference. *Deterministic validation* is
rule-based checking (parsers, regex, range checks) that does not depend on the model.

Flow: validate input (types, lengths — existing route checks) → embed → top-K from train-only index →
keep neighbours with cosine ≥ threshold → **context = compact examples** (Input Format + Value Constraints)
→ generate → decode with the *full* marker table → deterministic checks (numbers present in source, operator
conflicts as in cells 102–103, parser for the integer-range grammar) → draft with evidence and neighbour IDs
→ teacher approval via the existing draft service (unchanged).

**Context budget (measured with the real tokenizer):** new-problem prompt median 203 tokens, p95 430;
one compact example median 85, p95 167. With a 512-token encoder budget: a median problem fits ~3 examples;
a p95 problem fits 0–1; 89 problems do not fit even alone. Therefore: never truncate the new problem —
truncate/omit examples; K is dynamic. FLAN-T5 uses relative position buckets, so >512 tokens is technically
accepted, but quality beyond the trained length is untested — measure before relying on it.

Important: a model fine-tuned **without** context may ignore or copy retrieved examples. Experiment B
measures that honestly; D trains *with* retrieved context (neighbours retrieved from train-only index,
excluding self).

**Insufficient information:** if the statement contains no explicit bounds and the generated numbers are not
supported by the statement or neighbours, return the draft as `UNVERIFIED_SUGGESTION` (existing status) —
never as a fact.

---

## 8. Automatic test-case generation (main requirement)

### 8.1 Principles

* The model proposes **inputs only**. Expected outputs come only from the teacher's APPROVED reference
  solution (existing sandbox / SHA-allow-listed trusted host path) or a verified oracle bound to that
  reference's hash. `prime_check_v1` is registry-bound to one reference hash and can never be used for
  another problem.
* More candidates than needed (e.g. 30) are generated, validated, de-duplicated and ranked; exactly 10 are
  approved (existing `REQUIRED_TEST_CASE_COUNT = 10`).

### 8.2 Training data for the test-input generator

| Choice | Recommendation (measured basis) |
|---|---|
| Unit | **One record per test case** (10-case sets: median 403 tokens, only 4,679/7,411 ≤ 512 even with outputs) |
| Target | input only, losslessly escaped (median 9 tokens, p95 131; 71,959/74,110 ≤ 256; 192 raw inputs contain `<unk>` → escaping required) |
| Prompt | Input Format + Value Constraints first, description truncated last, plus a *role tag* (e.g. `case: minimal / boundary / typical / large`) derived from the case's position/size — gives the model a diversity control (to be validated) |
| Exclude | Sample_Input as a target (it is always one of the 10 tests) when it also appears in the prompt — simplest: never put Sample I/O in the prompt |
| Large/stress cases | keep a deterministic large-input generator; do not train the LM to emit 2 MB inputs |

### 8.3 Validation and fallback

* **Input validator — MISSING beyond integers.** `generic_test_inputs` supports ≤3 integer variables on one
  line. Arrays, strings, matrices, graphs and multi-line formats need a small teacher-approved **input
  schema** (e.g. `N int [1,1e5]; A list N int [-1e9,1e9]`; `S string len [1,1e5] charset a-z`), from which
  both a deterministic generator and a validator are derived. The AI may *suggest* the schema; the teacher
  approves it (same pattern as constraints).
* **Diversity:** exact-duplicate removal, structural signature dedupe (lengths/extremes), coverage of
  min/max/boundary per variable.
* **Fallback:** if the model's candidates fail validation, the deterministic schema generator fills the
  remaining slots — generation never silently returns fewer than 10.
* **Integration point already exists:** AI-validated inputs can be passed as `additional_inputs` to the
  existing `generate_and_save_test_cases` / oracle routes (≤10, merged after structural cases) — no change to
  output computation, approval, scoring or the database.

Execution constraint: the production untrusted-C++ gate stays **disabled**; new problems therefore need a
human-reviewed reference hash on the allow-list (existing mechanism) or a verified oracle. Dataset
`CPP_Solution`s are run only in Colab/Linux for offline evaluation.

---

## 9. Split and leakage-prevention strategy

* Keep the existing problem-level split (5,636 / 716 / 709) for the 7,061 problems; recover the 332 by
  reproducing cell 12; the 18 never-split problems → train.
* Test split stays untouched until the final run (the constraint model was only checked on validation).
* Retrieval index for evaluation = train split only; self-ID excluded.
* Report the cross-split near-duplicate rate (cosine ≥ 0.95, identical constraint strings) and report metrics
  both on all test problems and on test problems with no near-duplicate in train — templated text otherwise
  inflates scores.

---

## 10. Experiments and metrics

| Exp | Configuration | Feasible? |
|---|---|---|
| A | Existing full fine-tuned constraint model | yes — baseline, already deployed |
| B | A + retrieved context at inference | yes — inference-only |
| C | flan-t5-small + constraint LoRA, no retrieval | yes — Colab |
| D | flan-t5-small + constraint LoRA trained with retrieval | yes — Colab; needs index first |
| T1 | test-input LoRA (inputs only) vs deterministic schema generator | yes — needs schemas for the evaluated problems |

Metrics (calculation; ground truth needed):

| Metric | How | Ground truth |
|---|---|---|
| Constraint exact match | normalised string equality | dataset Value_Constraints (have) |
| Numeric/bound accuracy | parse `lo op var op hi` triples; precision/recall of triples | dataset (have; parser needed) |
| Constraint validity | parses + no operator conflict with explicit statement evidence | rules (have) |
| Unsupported-number rate | numbers absent from statement & neighbours | none |
| Input validity rate | candidate passes schema validator **and** reference runs without error/timeout | schemas (**needs annotation** for evaluated problems) + CPP_Solution (have) |
| Expected-output success | reference produces deterministic output twice | CPP_Solution (have) |
| Duplicate rate | duplicates / candidates | none |
| Boundary coverage | share of variables whose min and max are hit | schemas (**annotation**) |
| Mutation detection | fraction of mutants of CPP_Solution killed by the 10 selected tests vs dataset's own 10 tests | mutants generated automatically; run in Colab |
| Retrieval quality | recall@K of same-template neighbours; MRR | weak labels from identical constraint/format strings |
| Latency / resources | wall-clock per request on this PC; training time, peak GPU memory | measured |

No results exist yet for B, C, D, T1 — none are claimed here.

---

## 11. Hardware feasibility (measured now)

| Item | Value |
|---|---|
| CPU | Intel Core i5-1235U, 10 cores / 12 threads |
| RAM | 15.7 GB total, **2.1 GB free** at audit time (SQL Server, Ollama, other apps running) |
| GPU | Intel UHD only; no CUDA / no `nvidia-smi` |
| Disk free | C: 8.9 GB · D: 258.3 GB · G: (Drive cache) 8.5 GB |
| `.venv_ai` | Python 3.12.10, torch 2.15.0, transformers 5.22.2, tokenizers 0.23.3, sentencepiece 0.2.3, numpy 2.5.4, safetensors 0.8.0, Flask 3.1.3 |
| Not installed | peft, sentence-transformers, faiss-cpu, scikit-learn, accelerate, datasets |
| Ollama | `qwen2.5-coder` present (grading model — unchanged) |

| Question | Answer |
|---|---|
| Local FLAN-T5-small inference | Feasible (already deployed, CPU). Local latency not measured in this audit; Colab CPU took 3.6 s for one beam-4 generation (cell 152) |
| Local embedding generation | Feasible: MiniLM on 7,411 problems is a one-off CPU job (minutes, estimate); needs a one-time model download (~90 MB) — requires your approval |
| CPU LoRA training | Not practical: no CUDA, ~2 GB free RAM, 50k+ examples |
| Colab GPU | Recommended. Measured: full fine-tune 5,636 examples × 3 epochs = 12.75 min on T4. Test-input data is ~10–13× larger → roughly 1.5–3 h per run (estimate) |
| Extra packages | Colab: `peft` (+ `sentence-transformers` optional). Local: none if adapters are merged before export and embeddings use `transformers`; otherwise `peft`. Each install needs your approval |
| Memory risk | Running the AI service + embeddings beside SQL Server + Ollama on 16 GB is tight; load models lazily and once |

---

## 12. Files and services requiring integration

| File | Change (future, not done) |
|---|---|
| `ai_constraint_service.py` | full marker decoder (D1), `max_new_tokens` ≥ 384 (D2), number check after full decode (D3), truncation warning (D4), optional RAG context, optional adapter loading |
| `ai_local_api.py` | new endpoints e.g. `/similar`, `/suggest-test-inputs` (port 5001, same isolation) |
| `ai_backend_routes.py` | teacher-only proxies for the new endpoints; uniform errors |
| new `problem_embedding_index.py` | build/load `.npy` + metadata, cosine top-K, leakage filters |
| new `input_schema.py` | schema parser, deterministic generator, validator (extends `generic_test_inputs`) |
| `test_case_routes.py` / `test_case_builder.py` | accept validated AI candidates via the existing `additional_inputs` path; schema-based variables |
| `teacher_approval_service.py` | carry evidence/neighbour IDs into the draft (display only) |
| `models/` | `adapters/<task>_<version>/`, `embeddings/<model>_<version>/` (not in git; hashes recorded) |
| Unchanged | SQL Server schema, grading pipeline, Ollama Qwen rubric grading, Python verification, student privacy, criterion overrides, equal-weight 10-test scoring, execution gate (disabled) |

---

## 13. Estimated complexity

| Component | Status | Complexity |
|---|---|---|
| Original dataset located & verified | VERIFIED | — |
| Preprocessing / splits | VERIFIED | — |
| Constraint model (full fine-tune) | VERIFIED (trained; 21% exact on 100 val) | — |
| Constraint service + teacher draft approval | VERIFIED (code) — D1–D4 defects | Low |
| Code-constraint regex extractor (v5, 100% recall on 59 labelled mandatory cases, cell 140) | VERIFIED on small labelled set | — |
| Test-case checkpoints v1/v2/v3 | UNVALIDATED (degenerate) | — |
| Deterministic integer test generation + reference/oracle outputs + exactly-10 rule | VERIFIED | — |
| Embedding index + cosine retrieval | MISSING | Low–Medium |
| RAG prompt construction + Exp B | MISSING | Medium |
| Constraint LoRA (C, D) | MISSING | Medium (Colab) |
| Input-schema validator/generator for structured inputs | MISSING | Medium–High |
| Test-input generator (LoRA, inputs only) + candidate filtering | MISSING | High |
| Offline evaluation harness (CPP_Solution execution, mutation testing) in Colab | MISSING | Medium–High |
| FAISS, multitask single adapter, larger base (flan-t5-base) | OPTIONAL | — |

---

## 14. Prioritised implementation sequence

| Priority | Step | Why first |
|---|---|---|
| **P0** | Fix D1–D4 in `ai_constraint_service.py` + unit tests (marker round-trip on all 7,411 constraints) | Teachers currently see undecoded/truncated suggestions; tiny, isolated change |
| **P0** | Evaluate Exp A on the untouched **test** split with the same metric code (exact, numeric triples) | Gives the honest baseline every later claim is compared against |
| **P1** | Build train-only MiniLM index (+ TF-IDF baseline), retrieval metrics, `/similar` endpoint for the demo | Satisfies "vectors + similarity" with real, measurable evidence; cheap |
| **P1** | Exp B (RAG at inference) | Inference-only; shows whether retrieval helps the existing model |
| **P2** | Colab: Exp C and D (constraint LoRA, r=16, q/v), with saved adapters + training records | The meaningful dataset-trained component with proof of training |
| **P2** | Input-schema DSL + validator + deterministic generator (teacher-approved) | Required before any AI input can be trusted for non-integer problems |
| **P3** | Colab: test-input LoRA (inputs only, all 7,411 problems represented), offline validity / mutation evaluation with CPP_Solution | Main FYP requirement; depends on schemas and the evaluation harness |
| **P3** | Integrate AI candidates via `additional_inputs` with teacher review | Re-uses the verified output/approval/scoring path unchanged |
| OPTIONAL | flan-t5-base + LoRA, FAISS, multitask adapter | Only if time remains and metrics justify it |

Each step that installs packages, downloads models, trains, or changes code needs your explicit approval.
