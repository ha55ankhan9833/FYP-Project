"""
Assemble (locally) the exact files the Colab notebook needs, for upload ONLY
after approval G-A. Nothing is uploaded by this script.

Bundle = train.jsonl, validation_dev.jsonl, manifest.json (hash-verified),
training/test_input_lora_config.json (must be fully version-pinned),
training/test_input_lora.ipynb (no outputs; round-trip, Drive mount, GPU preflight
and checkpoint-resume safeguards required), training/colab_training_utils.py,
test_input_codec.py, BUNDLE_MANIFEST.json.
validation_heldout / validation_problems / any test data are never included.
An existing bundle folder is never overwritten (use a new versioned --out).

    python dev_tools/package_colab_bundle.py [--data data_prepared/test_input_v1] [--out DIR]
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_prepared" / "test_input_v1"
OUT = ROOT / "data_prepared" / "colab_bundle_test_input_v3"
DATA_FILES = ("train.jsonl", "validation_dev.jsonl")
CONFIG = ROOT / "training" / "test_input_lora_config.json"
NOTEBOOK = ROOT / "training" / "test_input_lora.ipynb"
UTILS = ROOT / "training" / "colab_training_utils.py"
EXTRA = (CONFIG, NOTEBOOK, UTILS, ROOT / "test_input_codec.py")
FORBIDDEN = ("validation_heldout.jsonl", "validation_problems.jsonl")
ROUND_TRIP_CELL = "# 5a. Target round-trip"
DRIVE_BUNDLE_DIR = "MyDrive/FYP_AI_Training/test_input_v3_bundle"
# Reliability safeguards the bundled notebook must contain (bundle v3).
REQUIRED_NOTEBOOK_SNIPPETS = ("drive.mount('/content/drive')", DRIVE_BUNDLE_DIR,
                              "verify_bundle(BUNDLE)", "gpu_preflight(torch", "check_drive_space(",
                              "restore_for_resume(", "make_drive_sync_callback(",
                              "resume_from_checkpoint=")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_training_files(config_path=CONFIG, notebook_path=NOTEBOOK):
    """The bundled config must be fully pinned and the notebook must be clean and consistent."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    pins = config.get("pinned_packages") or {}
    if not config["base_model"].get("revision") or \
            not all(pins.get(p) for p in ("peft", "transformers", "accelerate")) or \
            pins["peft"] != config.get("peft_version"):
        raise SystemExit("REFUSED: training config is not fully version-pinned")
    notebook = json.loads(Path(notebook_path).read_text(encoding="utf-8"))
    sources = ["".join(c["source"]) for c in notebook["cells"]]
    if any(c.get("outputs") for c in notebook["cells"]):
        raise SystemExit("REFUSED: notebook contains outputs")
    if not any(s.startswith(ROUND_TRIP_CELL) for s in sources):
        raise SystemExit("REFUSED: notebook lacks the target round-trip cell")
    joined = "".join(sources)
    if "warmup_ratio=" in joined:
        raise SystemExit("REFUSED: notebook passes warmup_ratio (unsupported by the pinned transformers)")
    missing = [s for s in REQUIRED_NOTEBOOK_SNIPPETS if s not in joined]
    if missing:
        raise SystemExit(f"REFUSED: notebook lacks required safeguards: {missing}")
    if not config.get("checkpointing") or config["training"].get("save_strategy") != "steps":
        raise SystemExit("REFUSED: periodic checkpointing is not configured")
    return {"base_model": config["base_model"]["repo"], "revision": config["base_model"]["revision"],
            "packages": {k: pins[k] for k in ("peft", "transformers", "accelerate")},
            "runtime": config.get("runtime")}


def package(data=DATA, out=OUT):
    data, out = Path(data), Path(out)
    if out.exists():
        raise SystemExit(f"REFUSED: {out} exists")
    pins = check_training_files()
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name in DATA_FILES:
        if sha256(data / name) != manifest["files"][name]["sha256"]:
            raise SystemExit(f"REFUSED: {name} does not match manifest.json")
        rows = [json.loads(line) for line in (data / name).read_text(encoding="utf-8").splitlines()]
        expected = "train" if name == "train.jsonl" else "validation"
        if {r["split"] for r in rows} != {expected}:
            raise SystemExit(f"REFUSED: {name} contains rows outside the {expected} split")
        if name == "validation_dev.jsonl" and {r["subset"] for r in rows} != {"dev"}:
            raise SystemExit("REFUSED: validation_dev.jsonl contains non-dev rows")
    out.mkdir(parents=True)
    files = {}
    for source in [data / n for n in DATA_FILES] + [data / "manifest.json"] + list(EXTRA):
        target = out / source.name
        shutil.copyfile(source, target)
        files[source.name] = {"sha256": sha256(target), "bytes": target.stat().st_size}
    assert not any((out / n).exists() for n in FORBIDDEN)
    bundle = {"purpose": "Colab LoRA training bundle (upload only after approval G-A)",
              "pins": pins, "files": files,
              "excluded": list(FORBIDDEN) + ["test split (never prepared)"]}
    (out / "BUNDLE_MANIFEST.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    print(json.dumps(package(args.data, args.out), indent=1))


if __name__ == "__main__":
    main()
