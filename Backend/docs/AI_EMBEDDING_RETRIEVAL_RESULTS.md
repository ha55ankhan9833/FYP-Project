# AI Phase 2 — embeddings and cosine-similarity retrieval: results

Date: 2026-11-17. Approved: download `BAAI/bge-small-en-v1.5`, build a training-only index,
NumPy Top-K, evaluate against TF-IDF and the FLAN-T5 encoder, add tests, prime-number demo.
**Not done (not approved):** LoRA, RAG integration, HTTP endpoint, frontend. Original datasets, both
databases, grading APIs and the execution gate are unchanged.

## 1. Model download (verified)

| Item | Value |
|---|---|
| Repo / revision | `BAAI/bge-small-en-v1.5` @ `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (pinned), MIT licence |
| Files | 11 files (weights, config, tokenizer, pooling config, README). The ONNX and `pytorch_model.bin` duplicates were skipped |
| Size / time | 134,505,940 bytes, downloaded in 27.7 s |
| Integrity | `model.safetensors` SHA-256 matches the hub's published LFS hash; every file size matches. All file hashes are recorded in `models/embeddings/bge-small-en-v1.5/DOWNLOAD_MANIFEST.json` |
| Location | `models/embeddings/` (git-ignored, like the FLAN-T5 model) |
| Packages installed | **none** — runs on the existing `.venv_ai` (torch 2.15.0, transformers 5.22.2, numpy 2.5.4) |
| Loading | `local_files_only=True`; nothing is downloaded at runtime |

**Procedure, taken from the model's own files:**
- `1_Pooling/config.json`: CLS-token pooling.
- `modules.json`: Transformer → Pooling → Normalize, i.e. L2 normalisation.
- `sentence_bert_config.json`: `max_seq_length` 512.
- README: use the query instruction only for short-query → passage search; otherwise use no instruction. Both options were measured (§4); no-instruction is better, so it is used.

## 2. Index (training split only)

| Item | Value |
|---|---|
| Text source | original dataset `FYP_DATASET_P001_P7411_FINAL_VERIFIED.json` (SHA-256 checked before reading) |
| Embedded text | `Problem_Description` + `Input format:` + `Output format:` |
| Corpus | **5,636 training-split problems** |
| Excluded | 716 validation, 709 test, 350 without a recorded split |
| Vectors | 5,636 × 384 float32, every row L2-normalised; `vectors.npy` is 8.66 MB |
| Stored with the vectors | `problem_id`, `input_format`, `value_constraints` only. **No** solutions, test cases, sample I/O or code hints |
| Truncation (bge's own tokenizer) | median 161, p95 330, max 443 tokens → **0 of 5,636 truncated** at 512 (the FLAN-T5 proxy had suggested 1,179 over 256) |
| Build | 518.6 s of CPU encoding (8.6 min) |
| Provenance | `manifest.json` records model revision, weights hash, dataset hash, split-source hash, id-list hash and vectors hash |
| Load-time checks | id-list hash and vectors hash, shape and dtype, unit norms, split = train, field whitelist |

## 3. Retrieval

`problem_embedding_index.ProblemIndex.search` works as follows:
- The query is L2-normalised, so cosine similarity = `vectors @ query`.
- Results are ranked with a stable sort.
- Optional `exclude_ids` and `min_score`; `k` is between 1 and 20.
- Results with cosine ≥ 0.95 to an already-returned result are folded into it as `near_duplicates`. The dataset is heavily templated, so without this the top-K would be near-copies.

## 4. Evaluation on validation problems (measured)

**Setup:**
- Queries: the 716 validation problems. Corpus: the 5,636 training problems. The test split was not used.
- Every method embeds the same text.
- There are no human relevance judgments, so two weak labels are derived from the data:
  - **same_constraints**: a training problem with identical `Value_Constraints` (362 queries have at least one);
  - **same_input_format**: identical `Input_Specification` (379 queries).
- Rankings are scored as raw rankings, with no near-duplicate folding.

| Method | same_constraints R@1 / R@5 / R@10 / MRR | same_input_format R@1 / R@5 / R@10 / MRR |
|---|---|---|
| bge-small (no instruction) | 0.638 / 0.771 / 0.801 / 0.695 | 0.644 / 0.776 / 0.829 / 0.700 |
| bge-small + query instruction | 0.624 / 0.760 / 0.798 / 0.688 | 0.639 / 0.768 / 0.826 / 0.698 |
| TF-IDF (NumPy) | 0.696 / 0.812 / 0.867 / 0.753 | 0.676 / 0.831 / 0.873 / 0.744 |
| FLAN-T5 constraint-model encoder (mean pool) | **0.696 / 0.837 / 0.879 / 0.763** | **0.736 / 0.863 / 0.908 / 0.793** |

**Honest reading:**
- **On these labels, bge-small is the weakest of the three methods.** These labels reward exact *template* identity: the same constraint string or the same input-format wording.
  - TF-IDF matches identical wording directly.
  - The FLAN-T5 encoder was fine-tuned on this dataset to predict exactly these constraints from exactly these fields.
- The labels do **not** measure topical or semantic relatedness, which is what bge is trained for.
- The prime demo (§5) shows bge finding semantically related problems. Its strength there is not measured by these numbers and should not be claimed as a metric.
- Adding the query instruction slightly lowers every bge metric, which confirms the no-instruction choice.
- **Templating:** 142 of 716 validation problems (19.8 %) have a training problem with bge cosine ≥ 0.95. Retrieval scores on this dataset are inflated by near-duplicates; any RAG evaluation must report results with and without these.
- bge top-1 cosine across validation queries: mean 0.889, median 0.883, min 0.697, max 0.998.

**Consequence for the next phase (a recommendation, not done):**
- For **constraint RAG**, the useful neighbour is one with the same constraints. On that label the FLAN encoder or TF-IDF retrieve better than bge, so a hybrid score (e.g. bge + TF-IDF) or the FLAN encoder should be compared inside the RAG experiment rather than assuming bge.
- A small human-judged topical relevance set (e.g. 50 queries) would be needed to measure bge's semantic advantage fairly.

## 5. Demo — new prime-number problem (not in the dataset)

> Write a C++ program that reads a positive integer N and determines whether N is a prime number…
> Input: a single line containing one integer N. Output: YES if N is prime, otherwise NO.

The query is 68 tokens.

| Rank | Training problem | Cosine | What it is |
|---|---|---|---|
| 1 | P2593 | 0.8662 | Decide prime vs composite by trial division up to √N; `1 <= N <= 10^12` |
| 2 | P5132 | 0.8310 | Recursive prime checking by trial division; `2 <= N <= 10^6` |
| 3 | P2613 | 0.8268 | Is N the sum of two consecutive primes; `1 <= N <= 10^7` |
| 4 | P2579 | 0.8245 | Sum of the prime digits of N; `1 <= N <= 10^18` |
| 5 | P2621 | 0.8227 | Does N have a prime number of divisors; `4 <= N <= 10^12` |

The two most similar problems are prime-testing problems; ranks 3–5 are prime-related number theory.

## 6. Resource usage (measured on this PC: i5-1235U, CPU only)

| Item | Value |
|---|---|
| Model on disk | 128.3 MB |
| Index on disk | 8.66 MB vectors + 1.55 MB metadata |
| Index load + full verification | 0.037 s |
| Query encode (batched, steady state) | 0.113 s per query |
| Search (5,636 × 384, Top-5 with folding) | 1.44 ms per query |
| End-to-end single query (encode + search, model already loaded) | 0.049 s |
| First query of a fresh process | ~22 s, measured while the evaluation was also running. Most of it is model loading, so load the model once at service start |
| Peak memory, evaluation process | 1,157 MB. This includes bge, the FLAN-T5 model, TF-IDF and all score matrices; bge retrieval alone needs less |
| FLAN-T5 encoder baseline | 710 s to encode corpus + queries (slower than bge's 519 s for the corpus alone) |

## 7. Tests and checks

| Check | Result |
|---|---|
| `tests/test_problem_embedding_index.py` (19 tests), run with `.venv_ai` | **19/19 pass** |
| Full suite, run with `.venv` | **514 tests OK**: 495 previous + 19 new. 9 skipped because `.venv` has no numpy; those 9 pass in `.venv_ai` |
| Leakage tests | <ul><li>the real index contains exactly the 5,636 training IDs from `tests/data/split_ids.json`;</li><li>validation, test and unknown-split IDs are rejected;</li><li>split ≠ train is rejected;</li><li>forbidden fields (solutions, tests, sample I/O, code hints) are rejected.</li></ul> |
| Consistency tests | <ul><li>vector hash, shape, dtype and unit norms;</li><li>id-list hash; duplicate IDs;</li><li>tampering detected;</li><li>provenance (model revision, dataset hash, 0 truncated).</li></ul> |
| Ranking tests | <ul><li>cosine values and order; ties are stable;</li><li>a non-normalised query still works;</li><li>self-exclusion; near-duplicate folding;</li><li>`min_score`; bounds on `k`; zero or wrong-dimension queries rejected;</li><li>self-retrieval on the real index scores 1.0.</li></ul> |
| Databases | `FYP_Database` and `FYP_E2E_Test` are identical to the pre-phase snapshot (row counts, content hashes, schema) |
| Execution gate | `get_secure_sandbox()` → `None` (still disabled) |

## 8. Files

New, uncommitted:
- `embedding_model.py`
- `problem_embedding_index.py`
- `dev_tools/build_problem_index.py`, `dev_tools/evaluate_retrieval.py`, `dev_tools/demo_similar_problems.py`
- `tests/test_problem_embedding_index.py`
- `tests/data/split_ids.json` (IDs and split names only)
- this document

Generated and git-ignored: `models/embeddings/bge-small-en-v1.5/` and `models/embeddings/index_train_v1/` (`vectors.npy`, `metadata.json`, `manifest.json`, `evaluation.json`).

Reproduce:
```
.venv_ai\Scripts\python.exe dev_tools\build_problem_index.py <original.json> <FYP_Complete_Dataset.json>
.venv_ai\Scripts\python.exe dev_tools\evaluate_retrieval.py <original.json> <FYP_Complete_Dataset.json>
.venv_ai\Scripts\python.exe dev_tools\demo_similar_problems.py <original.json>
```
