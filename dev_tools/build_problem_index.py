"""
Build the TRAINING-SPLIT problem embedding index (read-only on all inputs).

    .venv_ai\\Scripts\\python.exe dev_tools\\build_problem_index.py ^
        <original dataset .json> <FYP_Complete_Dataset.json> [out_dir]

* Text comes from the ORIGINAL 10-column dataset (SHA-256 checked); only
  Problem_Description, Input_Specification and Output_Specification are embedded.
* Membership comes from the processed split (FYP_Complete_Dataset.json):
  only split == "train" (5,636). Validation, test and the 350 problems with no
  recorded split are excluded.
* Stored metadata: problem_id, input_format, value_constraints only.
* Refuses to overwrite an existing index directory.
"""

import ctypes
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import embedding_model as em  # noqa: E402
import problem_embedding_index as pei  # noqa: E402

ORIGINAL_SHA256 = "b77745760e7ff750ff204c8093532035dca40326b291bc565a8c3e0a0a4f6b09"
EXPECTED_TRAIN = 5636
DEFAULT_OUT = ROOT / "models" / "embeddings" / "index_train_v1"


def peak_memory_mb():
    """Peak working set of this process (Windows), or None elsewhere."""
    try:
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters),
                                               ctypes.c_ulong]
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
            return None
        return round(counters.PeakWorkingSetSize / 1048576, 1)
    except (AttributeError, OSError):
        return None


def load_inputs(original_path, splits_path):
    raw = Path(original_path).read_bytes()
    if pei.sha256_bytes(raw) != ORIGINAL_SHA256:
        raise SystemExit("REFUSED: original dataset hash mismatch")
    original = {r["Problem_ID"]: r for r in json.loads(raw.decode("utf-8"))}
    splits_raw = Path(splits_path).read_bytes()
    processed = json.loads(splits_raw.decode("utf-8"))["problems"]
    split_of = {p["problem_id"]: p["split"] for p in processed}
    return original, split_of, pei.sha256_bytes(splits_raw)


def main(original_path, splits_path, out_dir=DEFAULT_OUT):
    import numpy as np

    out_dir = Path(out_dir)
    if out_dir.exists():
        raise SystemExit(f"REFUSED: {out_dir} already exists (indexes are never overwritten)")

    started = time.perf_counter()
    original, split_of, splits_sha = load_inputs(original_path, splits_path)
    train_ids = sorted((pid for pid, s in split_of.items() if s == pei.INDEX_SPLIT),
                       key=lambda pid: int(pid[1:]))
    if len(train_ids) != EXPECTED_TRAIN:
        raise SystemExit(f"REFUSED: expected {EXPECTED_TRAIN} training problems, got {len(train_ids)}")
    excluded = {
        "validation": sum(s == "validation" for s in split_of.values()),
        "test": sum(s == "test" for s in split_of.values()),
        "no_recorded_split": len(set(original) - set(split_of)),
    }

    texts = [em.problem_text(original[pid]["Problem_Description"],
                             original[pid]["Input_Specification"],
                             original[pid]["Output_Specification"]) for pid in train_ids]
    lengths = em.token_counts(texts)
    truncated = sum(n > em.MAX_LENGTH for n in lengths)

    t0 = time.perf_counter()
    vectors = em.encode(texts)
    encode_seconds = time.perf_counter() - t0

    metadata = [{"problem_id": pid,
                 "input_format": original[pid]["Input_Specification"],
                 "value_constraints": original[pid]["Value_Constraints"]} for pid in train_ids]

    out_dir.mkdir(parents=True)
    np.save(out_dir / "vectors.npy", vectors.astype(np.float32), allow_pickle=False)
    (out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    download = json.loads((em.MODEL_DIR / "DOWNLOAD_MANIFEST.json").read_text(encoding="utf-8"))
    sorted_lengths = sorted(lengths)
    manifest = {
        "format": pei.INDEX_FORMAT,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": pei.INDEX_SPLIT,
        "count": len(train_ids),
        "excluded": excluded,
        "dimensions": int(vectors.shape[1]),
        "normalized": "L2",
        "model": {"repo": em.MODEL_REPO, "revision": em.MODEL_REVISION,
                  "weights_sha256": download["files"]["model.safetensors"]["sha256"],
                  "pooling": "cls", "max_length": em.MAX_LENGTH,
                  "query_instruction": None},
        "text_template": "{Problem_Description}\\nInput format: {Input_Specification}"
                         "\\nOutput format: {Output_Specification}",
        "original_dataset_sha256": ORIGINAL_SHA256,
        "split_source_sha256": splits_sha,
        "problem_ids_sha256": pei.ids_digest(train_ids),
        "vectors_sha256": pei.sha256_bytes((out_dir / "vectors.npy").read_bytes()),
        "token_lengths": {"median": sorted_lengths[len(lengths) // 2],
                          "p95": sorted_lengths[int(0.95 * len(lengths))],
                          "max": sorted_lengths[-1],
                          "over_max_length_truncated": truncated},
        "build": {"encode_seconds": round(encode_seconds, 1),
                  "total_seconds": round(time.perf_counter() - started, 1),
                  "peak_memory_mb": peak_memory_mb()},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    index = pei.load_index(out_dir, split_of)          # full re-verification
    print(json.dumps({k: manifest[k] for k in ("count", "excluded", "dimensions",
                                               "token_lengths", "build")}, indent=1))
    print("verified index rows:", len(index))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
