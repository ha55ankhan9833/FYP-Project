# LoRA Test-Input Generator — Training and Evaluation Report

Date: 2026-11-18. Status: **trained, evaluated, NOT enabled.**

The deterministic generator (`baseline_input_generator.py`) remains the default.
`TEST_INPUT_AI_MODE = "off"` and `RAG_MODE = "off"` are unchanged. The trained model is kept
as research evidence only. The 709-problem test split was never used.

## 1. Decision

On every measured metric the trained model is worse than the deterministic baseline, on both
validation subsets (dev and held-out). Following the pre-agreed rule ("if the model performs
worse than the baseline, leave the baseline as the default"), the model stays disabled.

## 2. Model and reproducibility metadata

| Item | Value |
|---|---|
| Base model | `google/flan-t5-small`, revision `0fc9ddf78a1e988dac52e2dac162b0ede4fd74ab` |
| Method | LoRA (peft 0.21.2), merged into the base weights for export |
| LoRA config | r 16, alpha 32, dropout 0.05, bias none, targets q, k, v, o, wi_0, wi_1, wo |
| Trainable parameters | 2,555,904 |
| Training data | `train.jsonl` 47,238 rows (sha256 `4d42963a…7b86`) |
| Eval data (during training) | `validation_dev.jsonl` 2,034 rows (sha256 `b11b25da…45f5`) |
| Dataset manifest | sha256 `e2d7807a…14b3` |
| Never uploaded | `validation_heldout.jsonl`, `validation_problems.jsonl`, all test-split data |
| Optimiser | lr 1e-3, linear schedule, warmup 266 steps (3 %), weight decay 0, fp32 |
| Batch / epochs / steps | 16 / 3 / 8,859 optimizer steps |
| Seed | 42 |
| Checkpointing | every 500 steps, SHA-verified copies to Drive, resumable; run fingerprint `f3fc7530…52c8` |
| Hardware | Google Colab, Tesla T4 (14.56 GiB), CUDA 13.0 |
| Library versions | Python 3.13.16, torch 2.14.1+cu130, transformers 5.22.2, accelerate 1.15.0, peft 0.21.2 |
| Wall time | 6,384 s (single session, no resume needed) |
| Loss | eval_loss 1.8392 (step 500) → 1.3281 (step 8,859, best = final); mean train loss 1.4336 |
| Exported weights | `model.safetensors`, 307.9 MB, sha256 `a303b5402b81e4fe9b3dd560c0cbea7db5923d6337d06ed0010bf0751f4d8b30` |
| Local path | `models/test_input_generator_v1/` (git-ignored); hash verified against `TRAINING_MANIFEST.json` on every load |

Reproduction inputs are tracked in git: `training/test_input_lora_config.json`,
`training/test_input_lora.ipynb`, `training/colab_training_utils.py`,
`dev_tools/package_colab_bundle.py` (commits `7608a82`, `da733ce`, `495320a`).

Known metadata quirk: the exported `config.json` says `tie_word_embeddings: true` while the
saved embedding and output weights are separate. transformers detects this and does not tie
them (correct behaviour, outputs verified identical to the in-memory model). Not changed.

## 3. Evaluation method

- Tool: `dev_tools/evaluate_test_inputs.py` (baseline and model), plus
  `dev_tools/reports/test_input_eval_v2/compare_paired.py` for paired statistics.
- 10 outputs per problem per method. Model: frozen service defaults (sampling, top_p 0.95,
  temperature 0.9, max 256 new tokens), seed 42. Configuration frozen after dev; no tuning on held-out.
- Validity is measured against schemas **inferred from each problem's sample input**
  (`input_schema_inference.py`). They accept 98.3 % (dev) / 99.8 % (held-out) of the problems'
  real test inputs, but they are self-consistency checks, not independently verified constraints.
- Only problems with an inferred schema can be scored: 67 / 234 dev, 135 / 464 held-out.
- CPU inference on the development laptop (no GPU).

## 4. Results on schema-supported problems

| Metric | Dev: baseline | Dev: model | Held-out: baseline | Held-out: model |
|---|---|---|---|---|
| Problems | 67 | 67 | 135 | 135 |
| Syntax valid | 100 % | 90.0 % | 100 % | 91.5 % |
| Valid (syntax + constraints) | 100 % | 83.0 % | 100 % | 85.0 % |
| Malformed (won't decode) | 0 % | 0.45 % | 0 % | 0.15 % |
| Unique valid inputs per problem | 10.0 | 6.93 | 9.97 | 7.24 |
| Problems with ≥10 unique valid | 67 | 12 | 133 | 25 |
| Problems with 0 valid | 0 | 1 | 0 | 1 |
| Scalar minimum hit | 0.87 | 0.42 | 0.91 | 0.50 |
| Scalar maximum hit | 0.72 | 0.06 | 0.72 | 0.03 |
| Valid outputs identical to a real test input | 42 | 199 | 86 | 383 |
| Latency mean / max | 0.4 ms / 1.7 ms | 2.17 s / 18.1 s | 0.4 ms / 1.7 ms | 1.42 s / 17.1 s |

Paired by problem on unique valid inputs:

| Subset | Model better | Tie | Baseline better | Two-sided sign test |
|---|---|---|---|---|
| Dev | 0 | 12 | 55 | p < 10⁻⁶ |
| Held-out | 0 | 25 | 110 | p < 10⁻⁶ |

Held-out by input shape (model valid / model outputs; unique valid model vs baseline):

| Shape | Problems | Model valid | Unique valid (model / baseline) |
|---|---|---|---|
| scalars | 54 | 488 / 540 | 362 / 536 |
| array | 49 | 409 / 490 | 395 / 490 |
| string | 22 | 219 / 220 | 190 / 220 |
| multi_test | 7 | 19 / 70 | 19 / 70 |
| graph | 3 | 12 / 30 | 12 / 30 |

## 5. Supplementary: problems the baseline cannot handle

No schema exists, so validity cannot be measured; only a structural proxy is reported
(does an output's line-and-token shape match at least one real input of that problem).

| Subset | Problems | Malformed | Unique per problem | Exact / loose shape match (proxy) | Truncated problems | Latency mean / max |
|---|---|---|---|---|---|---|
| Dev | 167 | 0.66 % | 9.16 | 71.9 % / 90.9 % | 9 | 3.43 s / 17.6 s |
| Held-out | 329 | 1.09 % | 9.24 | 68.2 % / 91.9 % | 22 | 3.48 s / 18.4 s |

A shape match is not evidence of validity.

## 6. Strengths

- Loads offline with plain transformers (no peft), about 5 s, about 0.83 GiB RAM.
- Scalar and string inputs are almost always well formed (string 219 / 220 valid on held-out).
- Produces realistic inputs: far more of its valid outputs coincide with real test inputs
  than the baseline's (383 vs 86 on held-out). Nothing shows this helps find bugs.
- Produces structurally plausible output for formats the deterministic generator cannot parse.

## 7. Failure modes

- **Count errors:** stated array length, number of edges or number of test cases does not match
  the data that follows (e.g. `8\n0 -1 2 -3 0 -1 2\n` — 8 declared, 7 given).
- **Weak boundary coverage:** maximum values almost never generated (3–6 %).
- **Low diversity:** repeats itself; fewer than 10 unique valid inputs on most problems.
- **Graphs and multi-test inputs:** low validity (12 / 30 and 19 / 70 on held-out), slow, and
  sometimes truncated at the 256-token limit.
- **Latency:** seconds per request on CPU, worst case about 18 s; the baseline is sub-millisecond.

## 8. Why it is not enabled

1. Worse than the baseline on validity, uniqueness, boundary coverage and diversity (above).
2. The suggestion endpoint resolves a schema before generating and returns 422 without one, so
   the model would only run where the baseline already works better.
3. Live-integration gaps: the Flask `.venv` has no torch; no generation timeout; the live model
   path ignores `seed`; an over-long prompt surfaces as a generic 400.

Enabling it would need a design change (use it only where no schema exists), a way to show its
inputs catch faulty programs, and the integration gaps closed — each a separate approval.

## 9. Artefacts

| Artefact | Location | In git |
|---|---|---|
| Training config, notebook, utilities, packager | `training/`, `dev_tools/package_colab_bundle.py` | yes |
| Inference wrapper (mode off) | `ai_test_input_service.py` | yes |
| Evaluator | `dev_tools/evaluate_test_inputs.py` | yes |
| Exported model + `TRAINING_MANIFEST.json` | `models/test_input_generator_v1/` and Drive `FYP_AI_Training/test_input_v1_output/final/` | no (git-ignored) |
| Raw evaluation outputs and analysis scripts | `dev_tools/reports/test_input_eval_v2/` | no (git-ignored) |
| Training bundles | `data_prepared/colab_bundle_test_input_v*/` | no (git-ignored) |
