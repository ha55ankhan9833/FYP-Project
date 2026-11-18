"""
Assemble (locally) the exact files the Colab notebook needs, for upload ONLY
after approval G-A. Nothing is uploaded by this script.

Bundle = train.jsonl, validation_dev.jsonl, manifest.json (hash-verified),
training/test_input_lora_config.json, test_input_codec.py, BUNDLE_MANIFEST.json.
validation_heldout / validation_problems / any test data are never included.

    python dev_tools/package_colab_bundle.py [--data data_prepared/test_input_v1] [--out DIR]
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_prepared" / "test_input_v1"
OUT = ROOT / "data_prepared" / "colab_bundle_test_input_v1"
DATA_FILES = ("train.jsonl", "validation_dev.jsonl")
EXTRA = (ROOT / "training" / "test_input_lora_config.json", ROOT / "test_input_codec.py")
FORBIDDEN = ("validation_heldout.jsonl", "validation_problems.jsonl")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package(data=DATA, out=OUT):
    data, out = Path(data), Path(out)
    if out.exists():
        raise SystemExit(f"REFUSED: {out} exists")
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
              "files": files, "excluded": list(FORBIDDEN) + ["test split (never prepared)"]}
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
