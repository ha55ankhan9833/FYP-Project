"""
Build the FLAN-T5-encoder and TF-IDF retrieval indexes for RAG (read-only inputs).

    .venv_ai\\Scripts\\python.exe dev_tools\\build_rag_indexes.py ^
        <original dataset .json> <FYP_Complete_Dataset.json>

Both indexes use EXACTLY the problem ids (and order) of the verified bge
training index (models/embeddings/index_train_v1), re-checked against the
processed split: training split only. Text = embedding_model.problem_text from
the original dataset. Metadata = the bge index metadata (whitelisted fields).
Refuses to overwrite existing index directories.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev_tools"))
os.environ.setdefault("FYP_MODEL_BASE_PATH", str(ROOT / "models"))

import embedding_model as em  # noqa: E402
import problem_embedding_index as pei  # noqa: E402
import rag_retrievers as rr  # noqa: E402
from build_problem_index import ORIGINAL_SHA256, load_inputs, peak_memory_mb  # noqa: E402

FLAN_WEIGHTS = ROOT / "models" / "flan_t5_constraint_final" / "model.safetensors"


def main(original_path, splits_path):
    import numpy as np
    import ai_constraint_service as svc

    for target in (rr.FLAN_INDEX_DIR, rr.TFIDF_INDEX_DIR):
        if target.exists():
            raise SystemExit(f"REFUSED: {target} already exists (indexes are never overwritten)")

    original, split_of, splits_sha = load_inputs(original_path, splits_path)
    reference = pei.load_index(rr.BGE_INDEX_DIR, split_of)      # train-only, verified
    ids = reference.problem_ids
    metadata = reference.metadata
    texts = [em.problem_text(original[p]["Problem_Description"], original[p]["Input_Specification"],
                             original[p]["Output_Specification"]) for p in ids]
    common = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "original_dataset_sha256": ORIGINAL_SHA256,
        "split_source_sha256": splits_sha,
        "text_template": reference.manifest["text_template"],
        "excluded": reference.manifest["excluded"],
    }

    # ---- TF-IDF
    t0 = time.perf_counter()
    tfidf = rr.TfidfIndex.build(ids, texts)
    tfidf_manifest = tfidf.save(rr.TFIDF_INDEX_DIR, metadata, {
        **common, "weighting": "sublinear tf, idf=log((1+n)/(1+df))+1, L2",
        "tokenizer": "[a-z0-9]+ on lower-cased text",
        "build_seconds": round(time.perf_counter() - t0, 1)})
    rr.TfidfIndex.load(rr.TFIDF_INDEX_DIR, split_of)
    print("tfidf:", {k: tfidf_manifest[k] for k in ("count", "vocabulary", "build_seconds")})

    # ---- FLAN-T5 encoder (mean pooling of the fine-tuned constraint model)
    tokenizer, model, _device = svc.load_model()
    lengths = [len(x) for x in tokenizer(texts, truncation=False)["input_ids"]]
    t0 = time.perf_counter()
    vectors = rr.flan_embed(texts, tokenizer, model)
    seconds = time.perf_counter() - t0
    out = rr.FLAN_INDEX_DIR
    out.mkdir(parents=True)
    np.save(out / "vectors.npy", vectors.astype(np.float32), allow_pickle=False)
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    ordered = sorted(lengths)
    manifest = {
        "format": pei.INDEX_FORMAT, "kind": "dense", "split": pei.INDEX_SPLIT,
        "count": len(ids), "dimensions": int(vectors.shape[1]), "normalized": "L2",
        "model": {"name": "flan_t5_constraint_final encoder", "pooling": "mean",
                  "max_length": rr.FLAN_MAX_LENGTH,
                  "weights_sha256": hashlib.sha256(FLAN_WEIGHTS.read_bytes()).hexdigest()},
        "problem_ids_sha256": pei.ids_digest(ids),
        "vectors_sha256": pei.sha256_bytes((out / "vectors.npy").read_bytes()),
        "token_lengths": {"median": ordered[len(ordered) // 2], "p95": ordered[int(.95 * len(ordered))],
                          "max": ordered[-1],
                          "over_max_length_truncated": sum(n > rr.FLAN_MAX_LENGTH for n in lengths)},
        "build": {"encode_seconds": round(seconds, 1), "peak_memory_mb": peak_memory_mb()},
        **common,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    index = pei.load_index(out, split_of)
    assert index.manifest["problem_ids_sha256"] == reference.manifest["problem_ids_sha256"]
    print("flan:", {k: manifest[k] for k in ("count", "dimensions", "token_lengths", "build")})


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
