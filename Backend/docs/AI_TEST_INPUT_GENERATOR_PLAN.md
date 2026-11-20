# AI test-input generator — implementation plan (for approval)

Date: 2026-11-18. Status: **plan only**.

What has *not* happened yet:
- no training, downloads or installs;
- no database or grading changes;
- no C++ execution.

This plan replaces nothing that exists. `RAG_MODE` stays `"off"`. The embedding, retriever and RAG code stays in the repo for research use only.

## 0. Measured facts this plan is built on

All of these come from read-only scripts over the original dataset, `FYP_DATASET_P001_P7411_FINAL_VERIFIED.json`, with SHA-256 `b7774576…`.

| Fact | Value |
|---|---|
| Problems / test cases | 7,411 / 74,110. Every problem has exactly 10 cases, with no duplicate inputs inside a problem |
| Case 1 | Always identical to `Sample_Input` |
| Split (existing, notebook cell 12) | train 5,636 / validation 716 / test 709 / excluded 350 |
| Input shape | 32,439 single-line, 29,901 with 2–5 lines, 11,770 with more than 5 lines |
| Problem types (keywords; one problem can match several) | array 2,256 · string 1,459 · graph 1,057 · grid/matrix 923 · multi-testcase 614 · floating 493 |
| Canonical form ("lines kept, exactly one trailing newline") | 99.7% of inputs are already canonical, apart from a missing trailing newline. The only exceptions are 115 inputs ending in a blank line, plus a few others |
| **Light line-aware codec** (`\n` → `[NL]`, rare bytes → `[BXX]`) | **100%** exact round trip through the FLAN-T5 tokenizer. Tokens: median 13, p90 84, p95 191, p99 1,233 |
| Inputs longer than 256 tokens (light codec) | 4.1%. 6,191 problems have all 10 inputs at or under 256 tokens |
| Existing constraint codec on the same inputs | median 25 tokens, p95 345. It is too heavy for this task |
| Problems whose 10 inputs are 1–3 integers on one line | train 710 / val 93 / test 80 (about 12%) |
| `Value_Constraints` accepted by the backend grammar as-is | train 93 / val 18 / test 8 |
| Prompt (description + input format + constraints) | median 204 tokens, p95 451. 76 problems exceed 512 tokens |
| Local AI environment (`.venv_ai`) | torch 2.15, transformers 5.22, sentencepiece. **No `peft`** |

**The integration constraint.** The backend's input validator, `generic_test_inputs.validate_inputs`, accepts only 1–3 integer variables on one line. `merge_additional_inputs` collapses whitespace and limits each input to 200 characters. So today, only scalar-integer assessments can take AI candidates through `additional_inputs`. Array, string, graph and multi-line inputs need a later, separately approved extension of *test generation* (§8, item 3). Grading itself would not change.

## 1. Is FLAN-T5-small + LoRA practical? Yes, within clear limits

**For it:**
- **Size and memory.** 77 M parameters, the same architecture as the deployed constraint model, so local CPU inference is already proven. Peak memory is about 820 MB and latency about 4 s for one beam-4 output.
- **Fits the data.** 96% of target inputs fit in 256 output tokens with the light codec.
- **The task suits it.** "Produce another input in the same format as the sample, with different values" is a copy-and-vary pattern, which seq2seq models of this size handle well.

**Limits, designed around rather than ignored:**
- **No large stress tests.** Inputs with N around 10⁵ cannot be emitted. Large or maximum-size cases stay with the deterministic generator (§9 B1).
- **Weak arithmetic consistency.** A declared count N may not match the number of elements generated. Every candidate is validated (§5), and invalid ones are discarded, never repaired silently.
- **Prompts over 512 tokens.** These problems (76 of them, plus any pushed over by the sample input) are refused with `INPUT_TOO_LONG`, using the same policy as D4.

**Why LoRA rather than another full fine-tune:**
- it gives a separate adapter of about 10 MB;
- the constraint model stays untouched;
- there is less over-fitting on 5.6 k problems.

A full fine-tune would also fit on Colab. It is not planned, because it adds no FYP value.

## 2. Task formulation and exact dataset preparation

**Task:** one prompt per problem, one canonical test input per target.

```
Generate a new test input.
Problem: <Problem_Description>
Input format: <Input_Specification>
Constraints: <Value_Constraints>
Sample input: <light-encoded Sample_Input>
```

- **Target:** the light-encoded input of case k, for k = 2 … 10. Case 1 is the sample, which is already in the prompt.
- **Pairs:** this gives up to 9 pairs per problem. The model learns to vary inputs while keeping the sample's structure.
- **Diversity:** comes from sampling at inference time (§3).

**Script:** `dev_tools/build_test_input_dataset.py` (new). It reads the original JSON only and refuses to overwrite. It works in these steps:

1. Verify the original file's SHA-256 and the split-file digest. Use the **existing** split IDs (`tests/data/split_ids.json`) so the new model has the same train/validation/test separation as the constraint model.
2. Canonicalise each input: add one trailing newline if it is missing. Inputs that are not canonical even after this (about 0.3%) are dropped and counted.
3. Light-encode each input. Assert an exact round trip through the FLAN tokenizer. Drop targets over 256 tokens, and record how many per split.
4. Build the prompt, then measure it with the real tokenizer (`truncation=False`). Drop it if it is over 512 tokens, and record how many.
5. Write the outputs to a git-ignored folder, `data_prepared/test_input_v1/`:
   - `train.jsonl`, all from the training split;
   - `val_dev.jsonl` (239 problems) and `val_heldout.jsonl` (477 problems), using the same `sha256(id) % 3` split as the RAG evaluation;
   - `manifest.json`, containing the source hash, split digest, per-split counts, every drop reason and a SHA-256 for each file.
6. Run the leakage checks:
   - no problem ID appears in two files;
   - the **test split is never written**;
   - no training target string equals any validation target string (reported);
   - the near-duplicate flags from `rag_eval_v1/near_duplicates.json` are copied into the validation rows.
7. Run unit tests for the codec round trip, the canonicaliser, the split guard and the refusal to overwrite.

The expected training size is about 5,636 × 9 × 0.96 ≈ 48 k pairs. The exact figure will come from the manifest, not from this estimate.

## 3. LoRA training configuration (Google Colab)

| Item | Setting |
|---|---|
| Base model | `google/flan-t5-small`, downloaded inside Colab only, at a pinned revision |
| Library | `peft` `LoraConfig(task_type=SEQ_2_SEQ_LM)`, installed in Colab only |
| Target modules | `q, k, v, o` (self- and cross-attention) and `wi_0, wi_1, wo` (gated FFN) |
| r / alpha / dropout | 16 / 32 / 0.05 |
| Trainable parameters | about 2.56 M (3.3%), calculated from the config (d_model 512, inner 384, d_ff 1024, 8 + 8 layers). This will be confirmed with `print_trainable_parameters()` |
| Lengths | source 512, target 256; dynamic padding; labels padded to −100 |
| Optimiser | AdamW, lr 1e-3, linear decay, 3% warm-up, weight decay 0 |
| Batch | 16; gradient accumulation 2 if memory needs it |
| Precision | fp32. T5 is unstable in fp16, and Colab's T4 has no bf16 |
| Epochs | 3, evaluating loss on `val_dev` at the end of each epoch; the checkpoint with the best `val_dev` loss is kept |
| Seed | 42, recorded together with every library version |

**Outputs, written to Drive:**
- the adapter;
- a **merged** model, from `merge_and_unload()`, of about 308 MB;
- `TRAINING_MANIFEST.json`: data hashes, config, final losses, wall time and the GPU type.

**Why merge:** the local backend then loads a plain T5 checkpoint with the libraries it already has. No `peft` install is needed locally.

**Notebook:** `training/test_input_lora.ipynb`, committed without outputs. It reads only `train.jsonl` and `val_dev.jsonl` from Drive.

## 4. Inference (local CPU)

1. Build the same prompt. Over 512 tokens → refuse (`INPUT_TOO_LONG`).
2. Generate 16 samples (`do_sample`, top_p 0.95, temperature 0.9, `max_new_tokens` 256) plus 1 greedy output. All generation settings are fixed in code.
3. Decode with the light codec. Mark invalid markers or a missing EOS as `DECODE_INCOMPLETE` and drop them.
4. Canonicalise. Remove duplicates and anything equal to the sample input.
5. Validate (§5) and drop failures, keeping a reason code for each.
6. Pick up to N survivors by max-min diversity over shape features: line count, number of tokens, leading size value, and the minimum and maximum numeric value.

Latency and peak memory will be **measured** on the validation runs; nothing is assumed here. The target is at most 30 s per problem on this laptop. If it is slower, the number of samples drops to 8.

## 5. Validating generated inputs

| Level | Used where | Check |
|---|---|---|
| V1 decode | always | Light-codec decode is clean and complete |
| V2 shape | offline evaluation | Invariants inferred from the problem's **10 gold inputs**: line count (fixed, or equal to a leading count), tokens per line, token type (int, float or word) per position, and count consistency (if the gold inputs always have "first token = number of items on the next line", the candidate must too) |
| V2′ sample shape | backend, for new problems | The same invariants, inferred from the single sample input. This is weaker, so it is reported separately |
| V3 range | offline evaluation | Numbers checked against bounds pulled from `Value_Constraints` by an **evaluation-only** parser. The parser handles `10^5`, `1e5`, trailing punctuation and "length of S"; it is not the backend grammar. Problems where no bound can be parsed are reported as "range unknown", never as "valid" |
| V4 backend | integration | The existing `validate_inputs` against the teacher-approved constraints. This is unchanged and fails closed |
| V5 reference-accepted | optional, gated (§6) | The verified reference solution exits with 0 within the time limit, deterministically, with non-empty output |

The headline validity figure is **V1 ∧ V2 ∧ V3**. No candidate is ever counted as valid only because the model produced it.

## 6. Expected outputs from verified references or oracles

- **Backend: no change.** Expected outputs keep coming from the existing paths:
  - the sandbox, when a sandbox is available;
  - the human-approved trusted-host allowlist;
  - a verified Python oracle (`/generate-test-cases/oracle`, `dry_run` by default).

  The AI only proposes *inputs*. It never produces expected outputs, and clients still cannot supply them.
- **Offline evaluation: an execution gate, needing your approval.**
  - **What it is:** running the dataset's reference solutions, and their mutants, on candidate inputs.
  - **Why it can't run here:** the local gate is `None` and host execution is forbidden.
  - **Proposed option:** do it in the **disposable Colab VM**, never on the Windows host, using `g++` with time and memory limits, for validation problems only.
  - **What it unlocks:** V5 and bug detection (§7).
  - **If not approved:** the report uses V1–V3 and diversity only.

## 7. Metrics

All metrics are computed per problem, then averaged. They are reported for all problems and for the no-near-duplicate subset. Tuning uses `val_dev`; the reported result uses `val_heldout`.

| Area | Metrics |
|---|---|
| Validity | V1, V2, V3 and V1∧V2∧V3 rate per candidate; problems with at least N valid candidates; `INPUT_TOO_LONG` refusals |
| Diversity | distinct-valid ratio; mean pairwise normalised edit distance; distinct line counts and distinct leading sizes per problem; rate of copying the sample input |
| Boundary coverage | for each variable with a parseable bound: minimum hit, maximum hit (where the 256-token budget allows), and special values present (0, 1, −1, all-equal, sorted / reversed, empty-string-equivalent) |
| Bug detection (gated, §6) | **mutation score**: about 100 `val_heldout` problems × up to 10 mutants (relational flip, off-by-one constant, `+`↔`−`, `<`↔`<=`, dropped `break` / `return`). A mutant is killed when its output differs from the reference. Mutants that none of the gold tests kill are treated as possibly equivalent and reported separately. Compared, 10 tests each: AI vs deterministic baseline vs the dataset's own gold 10 (a reference ceiling) |
| Cost | mean and maximum latency, peak memory, candidates generated per accepted input |

**Pre-registered decision rule** (to be written down before any evaluation run):
- The AI generator becomes the *suggestion source* only if, on `val_heldout`:
  1. its V1∧V2∧V3 validity is at most 5 percentage points below baseline B1;
  2. and *either* the mutation score beats B1 (paired Wilcoxon p < 0.05, if §6 is approved), *or* boundary coverage and diversity both beat B1 (paired p < 0.05).
- Otherwise B1 stays the default, and the model is reported as a research result.

The **709-problem test split** is evaluated once, at the end, only with separate approval.

## 8. Backend integration (after the evaluation; separate approval)

1. **New module `ai_test_input_service.py`.**
   - Lazy torch import; `TEST_INPUT_AI_MODE = "off"` by default.
   - The merged model sits in `models/test_input_generator_v1/` (git-ignored) with a SHA-256 manifest.
   - Its only job is to return candidate strings with reason codes. It never executes code and never writes to the database.
2. **New read-only endpoint: `POST /api/assessments/<id>/test-inputs/suggestions`.**
   - Teacher only, and only for their own assessment.
   - It runs §4 and then checks candidates with the existing `validate_inputs` (V4) against the **approved** constraints.
   - The response has `usable_as_additional_inputs` (candidates that pass V4, at most `10 − structural count`, each 200 characters or less after whitespace collapsing), `rejected_count` with reasons, and `model{…}` metadata.
   - The teacher picks candidates and calls the **existing** generation endpoint with them as `additional_inputs`.
   - The 10-case policy, expected-output paths, teacher approval and grading do not change.
3. **Out of scope here: structured inputs.** Arrays, strings, graphs and multi-line inputs are shown only in the offline evaluation and the demo script. Saving them would need an extension of the generation validator and of `merge_additional_inputs`, which currently collapses newlines. That would be its own plan.
4. **Tests.**
   - Endpoint auth and ownership.
   - Mode off → 404/disabled.
   - The model is stubbed in unit tests.
   - V4 filtering.
   - No database writes, checked with the read-only snapshot.
   - The response never contains expected outputs.
   - The full suite plus a database-unchanged check.

## 9. Baselines (deterministic, no ML)

- **B0:** the existing `generic_test_inputs`, for scalar-integer problems only (93 val / 80 test problems are of that form).
- **B1: sample-mutation fuzzer**, implemented in step P2 below.
  - Keeps the structure of the sample input.
  - Re-draws numbers inside the bounds parsed for V3, falling back to the same magnitude when no bound is parsed.
  - Resizes the following list when a leading count is detected.
  - Mutates strings within the same character class.
  - Adds min/max size variants when they fit the budget.
  - Seeded and fully deterministic.
- B1 is also the planned fallback for production suggestions if the model loses.

## 10. Steps, effort and approval gates

| Step | Work | Needs approval | Estimate |
|---|---|---|---|
| P1 | Dataset preparation script, manifest, leakage tests (§2), run locally | this plan | 1 day |
| P2 | Validators V1–V3, B1 baseline, metrics, tests; B0/B1 measured on `val_dev` | this plan | 1–2 days |
| **G-A** | Upload `train.jsonl` and `val_dev.jsonl` (derived from your dataset; no test split) to your Google Drive | **yes** | — |
| **G-B** | In Colab: install `peft` and download `google/flan-t5-small` at a pinned revision | **yes** | — |
| P3 | Colab LoRA training (§3) | with G-A/G-B | 1 day including the run |
| **G-C** | Download the merged model (about 308 MB) from Drive to the git-ignored `models/` | **yes** | — |
| P4 | Local inference and `val_dev` tuning (sample count only), then a single `val_heldout` run against B1 | with G-C | 1 day |
| **G-D** (optional) | Reference and mutant execution in the Colab VM (§6) | **yes** | 1 day |
| P5 | Results report (measured values only) and the decision rule | — | ½ day |
| **G-E** | Backend suggestion endpoint (§8), RAG still off, grading untouched | **yes** | 1 day |
| **G-F** | One-time evaluation on the 709-problem test split | **yes** | ½ day |

The total is about 6–8 working days. P1–P2 are useful even if the model is never trained, because B1 is a working improvement on its own.

## 11. Risks

1. **Shape errors** (counts, line numbers) from a small model → strict V2 validation and dropping invalid candidates; validity is reported honestly.
2. **Collapse to sample copies** → copies are removed and the copy rate is reported. Temperature and top-p are tuned on `val_dev` only.
3. **Templated near-duplicates** inflating results → the no-near-duplicate subset is always reported.
4. **Large inputs not representable** → left to the deterministic generator, and stated in the report.
5. **Limited integration** (scalar-integer only today) → stated plainly. Structured-input saving is a separate extension.
6. **The weak V2′ check for new teacher problems** → candidates are always teacher-reviewed; nothing is auto-saved.
