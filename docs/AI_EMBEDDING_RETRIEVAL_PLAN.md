# AI Phase 2 plan — problem embeddings and cosine-similarity retrieval

Status: **IMPLEMENTED 2026-11-17** (bge-small-en-v1.5, training-only index) — measured results in
`docs/AI_EMBEDDING_RETRIEVAL_RESULTS.md`. The `/api/ai/similar` endpoint in §5 was not built (not approved).
Prepared 2026-11-17, after commit `9c44197` (D1–D4).

## 1. What is already available on this PC (checked)

| Item | Result |
|---|---|
| Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub`) | Only `datasets--ByteDance-Seed--Code-Contests-Plus` (unrelated). **No embedding model** |
| sentence-transformers / fastembed / torch-hub / chroma caches | absent |
| Folders named MiniLM / bge / e5 / gte / nomic / mpnet on C:\Users and D:\ | none (only transformers' own `mpnet` source code) |
| Ollama | only `qwen2.5-coder:3b` (code LLM, not an embedding model) |
| `.venv_ai` (Python 3.12.10) | torch 2.15.0, transformers 5.22.2, tokenizers 0.23.3, numpy 2.5.4, huggingface-hub 2.4.0 |
| Not installed anywhere | sentence-transformers, scikit-learn, scipy, faiss, onnxruntime, rank-bm25 |

**Conclusion:**
- No suitable pretrained *sentence-embedding* model is on this PC, so a real semantic index needs **one model download**.
- No package install is needed: `.venv_ai` already has everything required to run such a model (torch, transformers, tokenizers, numpy, huggingface-hub).

Two things work with no download at all:
- **TF-IDF (lexical) baseline**, implemented in NumPy — a comparison baseline, not semantic.
- **Mean-pooled encoder of the existing FLAN-T5 constraint model.** It is available, but it was not trained for similarity, so it is an experimental comparison only.

## 2. Recommended model (needs your approval to download)

| | **BAAI/bge-small-en-v1.5** (recommended) | sentence-transformers/all-MiniLM-L6-v2 (alternative) |
|---|---|---|
| Parameters / layers | ~33 M / 12 | ~22.7 M / 6 |
| Vector size | 384 | 384 |
| Max input | **512** word-pieces | 256 (trained on 128) |
| Download (safetensors + config + tokenizer) | ~134 MB (estimate) | ~91 MB (estimate) |
| Licence | MIT | Apache-2.0 |
| Extra packages | none: transformers + torch already in `.venv_ai` | none |
| Pooling / normalisation | CLS token, L2-normalised | mean pooling, L2-normalised |

**Why bge-small is recommended: our texts are long.**
- The embedding text is Description + Input Format + Output Format.
- For the 5,636 training problems it measured, with the FLAN-T5 tokenizer as a proxy, at median 172 / p95 360 / max 454 tokens.
- **1,179 of 5,636 (21 %) exceed 256 tokens**, which would lose their tails under MiniLM's limit; none exceed 512.
- WordPiece counts differ slightly from this proxy, so the exact figure will be re-measured with the model's own tokenizer after download.

MiniLM remains a valid, smaller choice if download size matters more than long-text coverage.

**Download details, if approved:**
- Use `huggingface_hub.snapshot_download` (already installed), restricted to `model.safetensors`, `config.json`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `vocab.txt`, `modules.json` and the pooling config.
- Skip the ONNX / OpenVINO / PyTorch-bin duplicates.
- Pin the commit revision.
- Store under `models/embeddings/bge-small-en-v1.5/`. That folder is git-ignored, and its SHA-256 hashes will be recorded.
- No account or token is needed. No project data is sent anywhere: the download is a plain read from the model hub.

**Resources (estimate):**
- Model RAM is about 150 MB, plus about 0.5–1 GB of torch overhead while encoding.
- The audit measured only 2.1 GB free RAM, so the build should run while Ollama is idle.
- One-off CPU encoding of 5,636 problems at up to 512 tokens: a few minutes.
- Query: under 0.5 s per problem on CPU.
- Disk: about 134 MB for the model, 8.7 MB for the vectors (5,636 × 384 × float32), and under 2 MB of metadata.

## 3. Data and leakage rules

- **Source:**
  - Text comes from the original dataset, `FYP_DATASET_P001_P7411_FINAL_VERIFIED.json`. Its SHA-256 (`b777…6b09`) is checked before every build; it is read only.
- **Corpus = training split only:** the 5,636 problem IDs whose `split` is `train` in `FYP_AI_Training/FYP_Complete_Dataset.json`. That file's hash is also recorded.
- **Excluded from the corpus:**
  - Validation (716) and test (709) problems.
  - The 350 problems that were never in the processed split. Their original split is unknown, so they are excluded until cell 12 of the notebook is reproduced.
- **Embedded fields:** `Problem_Description`, `Input_Specification`, `Output_Specification`. This is exactly what a teacher provides for a new problem.
- **Metadata returned with a match:** `problem_id`, cosine score, `Input_Specification`, `Value_Constraints`.
- **Never stored in the index or returned:** `CPP_Solution`, `Test_Cases`, `Sample_Input/Output`, `Code_Constraints`.
- **Build-time guard:** the build refuses if any ID outside the training split appears. The manifest records the split, counts and hashes, so the absence of leakage can be audited.
- **Evaluation:** queries come from the **validation** split only. The test split stays untouched for the final evaluation.

## 4. Retrieval method

- **Similarity:**
  - Each vector is L2-normalised once, so cosine similarity = dot product.
  - Query: `scores = V @ q` over a (5,636 × 384) matrix, which takes about 2 M multiply-adds, i.e. milliseconds. FAISS is unnecessary at this scale.
- **Top-K:**
  - `argpartition` then sort; K defaults to 5 (capped at 20).
  - An optional minimum score is set from the validation analysis, not guessed.
- **Duplicates:**
  - Exact duplicates are found by text hash.
  - Near-duplicates (cosine ≥ 0.95 among the returned items) are collapsed, so K results are diverse. This matters because the dataset is heavily templated: one constraint string is shared by 127 training problems.
- **Self and leakage exclusion:** an `exclude_ids` parameter; during evaluation the query's own ID is always excluded.

## 5. Implementation (after approval)

| File | Purpose |
|---|---|
| `problem_embedding_index.py` (new, NumPy only) | load + validate manifest, top-K cosine, duplicate collapse, leakage guard. Unit-testable in `.venv` with small fake vectors (numpy is the only need — currently **not** in `.venv`; tests would run in `.venv_ai` or use a pure-Python fallback, decided at implementation without installing anything) |
| `embedding_model.py` (new, `.venv_ai`) | lazy-load the local model (`local_files_only=True`), pooling + normalisation, batch encode |
| `dev_tools/build_problem_index.py` (new) | read-only build: dataset hash check → train IDs → texts → vectors → `models/embeddings/index_train_v1/{vectors.npy, metadata.json, manifest.json}` |
| `dev_tools/evaluate_retrieval.py` (new) | read-only offline evaluation on the validation split (§6), including the TF-IDF and FLAN-encoder baselines |
| `ai_local_api.py` | new `POST /similar {problem, input_format, output_format, k}` on port 5001 (same input limits as `/analyze`) |
| `ai_backend_routes.py` | teacher-only proxy `POST /api/ai/similar`, uniform errors, 502/504 like `/analyze` |
| `tests/test_problem_embedding_index.py` | cosine maths, normalisation, ordering, K bounds, exclusion, duplicate collapse, field whitelist (no solutions/tests), leakage guard, manifest/hash mismatch refusal, route auth |
| `docs/API_CONTRACT.md`, `docs/SETUP_AND_SERVICES.md` | endpoint + how to rebuild the index |

**Unchanged:** constraint generation output, draft approval, test-case generation, grading pipeline, Qwen grading, databases, execution gate. The `/similar` results are shown to the teacher only. Feeding them into generation is the later RAG phase, which needs separate approval.

## 6. How retrieval will be evaluated (no results claimed yet)

- **Queries:** the 716 validation problems. **Corpus:** the 5,636 training problems.
- **Weak relevance labels**, derived from the data without human annotation:
  1. identical `Value_Constraints` text;
  2. identical `Input_Specification` text.
- **Metrics:**
  - Recall@1/5/10 and MRR for each label type.
  - Cross-split near-duplicate rate (validation→train cosine ≥ 0.95), reported because templated text inflates scores.
  - Mean query latency.
- **Compared:** bge-small (or MiniLM) vs TF-IDF vs FLAN-T5 encoder pooling.
- **Demo:** a teacher pastes a new problem and sees the top-5 training problems with their IDs, cosine scores, input formats and approved constraints.

## 7. Approvals needed before any work

1. **Download** `BAAI/bge-small-en-v1.5`, about 134 MB, pinned revision, into git-ignored `models/embeddings/`. Alternatively MiniLM at about 91 MB.
2. **Build** the training-only index: one-off CPU job that writes only to `models/embeddings/`.
3. **Implement** the code, tests and the teacher-only `/api/ai/similar` endpoint described above.

No package installation is required for any of these steps.
