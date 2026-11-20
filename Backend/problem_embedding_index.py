"""
Training-split problem index: integrity/leakage checks + cosine Top-K search.

Index directory (built by dev_tools/build_problem_index.py, git-ignored):
    vectors.npy     float32 (N, 384), every row L2-normalised
    metadata.json   [{"problem_id", "input_format", "value_constraints"}, ...]
                    in the same order as the rows of vectors.npy
    manifest.json   provenance: split, counts, model + dataset hashes, ...

Leakage rules enforced here (not just by the builder):
  * manifest split must be "train";
  * every problem id must be a training-split id (when the split map is given);
  * metadata may only contain whitelisted fields - never solutions, tests,
    sample I/O or code hints.

The manifest/metadata checks are pure Python. numpy is imported lazily for
loading vectors and searching (available in .venv_ai, not in .venv).
"""

import hashlib
import json
from pathlib import Path

INDEX_FORMAT = "fyp-problem-index-v1"
INDEX_SPLIT = "train"
EMBEDDING_DIM = 384                      # bge-small-en-v1.5
# Dense index kinds and their vector sizes: bge (384) and the mean-pooled
# encoder of the fine-tuned FLAN-T5-small constraint model (d_model 512).
ALLOWED_DIMENSIONS = frozenset({384, 512})
INDEX_KINDS = frozenset({"dense", "tfidf"})
ALLOWED_METADATA_FIELDS = frozenset({"problem_id", "input_format", "value_constraints"})
# Present in the original dataset but must never be exposed by retrieval.
FORBIDDEN_FIELDS = frozenset({
    "CPP_Solution", "Test_Cases", "Sample_Input", "Sample_Output",
    "Code_Constraints", "cpp_solution", "test_cases", "sample_input",
    "sample_output", "code_constraints", "expected_output",
})
DEFAULT_K = 5
MAX_K = 20
NEAR_DUPLICATE_COSINE = 0.95
NORM_TOLERANCE = 1e-3


class IndexIntegrityError(ValueError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def ids_digest(problem_ids):
    return sha256_bytes("\n".join(problem_ids).encode("utf-8"))


def validate_manifest(manifest, metadata, split_of=None):
    """
    Pure-Python consistency and leakage checks.
    split_of: optional {problem_id: split} map from the processed dataset.
    Returns the list of problem ids in row order.
    """
    if not isinstance(manifest, dict) or manifest.get("format") != INDEX_FORMAT:
        raise IndexIntegrityError("Unknown index format")
    if manifest.get("split") != INDEX_SPLIT:
        raise IndexIntegrityError("Index must be built from the training split only")
    kind = manifest.get("kind", "dense")
    if kind not in INDEX_KINDS:
        raise IndexIntegrityError("Unknown index kind")
    if kind == "dense" and manifest.get("dimensions") not in ALLOWED_DIMENSIONS:
        raise IndexIntegrityError("Unexpected embedding dimension")
    if not isinstance(metadata, list) or len(metadata) != manifest.get("count"):
        raise IndexIntegrityError("Metadata row count does not match the manifest")

    problem_ids = []
    for row in metadata:
        if not isinstance(row, dict):
            raise IndexIntegrityError("Malformed metadata row")
        keys = set(row)
        if keys & FORBIDDEN_FIELDS:
            raise IndexIntegrityError(
                f"Metadata exposes forbidden fields: {sorted(keys & FORBIDDEN_FIELDS)}")
        if not keys <= ALLOWED_METADATA_FIELDS or "problem_id" not in keys:
            raise IndexIntegrityError(f"Unexpected metadata fields: {sorted(keys)}")
        problem_ids.append(row["problem_id"])

    if len(set(problem_ids)) != len(problem_ids):
        raise IndexIntegrityError("Duplicate problem ids in the index")
    if manifest.get("problem_ids_sha256") != ids_digest(problem_ids):
        raise IndexIntegrityError("Problem-id list does not match the manifest")

    if split_of is not None:
        leaked = [pid for pid in problem_ids if split_of.get(pid) != INDEX_SPLIT]
        if leaked:
            raise IndexIntegrityError(
                f"{len(leaked)} non-training problem(s) in the index, e.g. {leaked[:3]}")
    return problem_ids


class ProblemIndex:
    def __init__(self, vectors, metadata, manifest):
        self.vectors = vectors
        self.metadata = metadata
        self.manifest = manifest
        self.problem_ids = [row["problem_id"] for row in metadata]
        self._row_of = {pid: i for i, pid in enumerate(self.problem_ids)}

    def __len__(self):
        return len(self.problem_ids)

    def rank(self, query_vector, n):
        """Raw cosine ranking (no folding): [(problem_id, score)] for the top n."""
        import numpy as np

        if type(n) is not int or n < 1:
            raise ValueError("n must be a positive integer")
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self.vectors.shape[1]:
            raise ValueError("Query vector has the wrong dimension")
        norm = float(np.linalg.norm(query))
        if not norm > 0:
            raise ValueError("Query vector is zero")
        scores = self.vectors @ (query / norm)
        order = np.argsort(-scores, kind="stable")[:n]
        return [(self.problem_ids[int(i)], float(scores[int(i)])) for i in order]

    def search(self, query_vector, k=DEFAULT_K, exclude_ids=(), min_score=None,
               collapse_threshold=NEAR_DUPLICATE_COSINE):
        """
        Cosine Top-K. Vectors are L2-normalised, so cosine = dot product.
        Results whose vector is >= collapse_threshold similar to an already
        returned result are folded into it (templated near-duplicates), so the
        K results are distinct. Returns [{rank, problem_id, score, ...}].
        """
        import numpy as np

        if type(k) is not int or not 1 <= k <= MAX_K:
            raise ValueError(f"k must be an integer between 1 and {MAX_K}")

        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self.vectors.shape[1]:
            raise ValueError("Query vector has the wrong dimension")
        norm = float(np.linalg.norm(query))
        if not norm > 0:
            raise ValueError("Query vector is zero")
        query = query / norm

        scores = self.vectors @ query
        order = np.argsort(-scores, kind="stable")
        excluded = set(exclude_ids)

        chosen = []
        for row in order:
            row = int(row)
            score = float(scores[row])
            if min_score is not None and score < min_score:
                break
            pid = self.problem_ids[row]
            if pid in excluded:
                continue
            if collapse_threshold is not None:
                twin = next((c for c in chosen
                             if float(self.vectors[c["_row"]] @ self.vectors[row])
                             >= collapse_threshold), None)
                if twin is not None:
                    twin["near_duplicates"].append(pid)
                    continue
            chosen.append({"_row": row, "problem_id": pid, "score": score,
                           "near_duplicates": []})
            if len(chosen) == k:
                break

        results = []
        for rank, item in enumerate(chosen, start=1):
            meta = self.metadata[item["_row"]]
            results.append({
                "rank": rank,
                "problem_id": item["problem_id"],
                "score": round(item["score"], 6),
                "input_format": meta.get("input_format", ""),
                "value_constraints": meta.get("value_constraints", ""),
                "near_duplicates": item["near_duplicates"],
            })
        return results


def load_index(directory, split_of=None):
    """Load and fully verify an index directory. Raises IndexIntegrityError."""
    import numpy as np

    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    validate_manifest(manifest, metadata, split_of)

    raw = (directory / "vectors.npy").read_bytes()
    if sha256_bytes(raw) != manifest.get("vectors_sha256"):
        raise IndexIntegrityError("vectors.npy does not match the manifest hash")

    if manifest.get("kind", "dense") != "dense":
        raise IndexIntegrityError("Not a dense vector index")
    vectors = np.load(directory / "vectors.npy", allow_pickle=False)
    if vectors.dtype != np.float32 or vectors.shape != (len(metadata), manifest["dimensions"]):
        raise IndexIntegrityError("vectors.npy has an unexpected shape or dtype")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.all(np.abs(norms - 1.0) <= NORM_TOLERANCE):
        raise IndexIntegrityError("Index vectors are not L2-normalised")
    return ProblemIndex(vectors, metadata, manifest)
