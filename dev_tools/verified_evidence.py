"""
Durability of the RECORDED execution evidence behind verified-fixture grading.

The recorded reports (dev_tools/reports/trusted_execution_*.json) are
git-ignored on purpose (they contain hidden test inputs/outputs and are raw
run output). The code only pins their SHA-256 in
recorded_evidence_grading.REVIEWED_RECORDS. A clean checkout therefore has
the pins but NOT the evidence; this tool makes that state explicit and
restorable instead of silent.

    .venv\\Scripts\\python.exe dev_tools\\verified_evidence.py status
        -> per record: PRESENT_VERIFIED / MISSING / HASH_MISMATCH (exit 1 unless all verified)
    .venv\\Scripts\\python.exe dev_tools\\verified_evidence.py backup <dir>
        -> copies every verified report + a manifest to <dir> (never overwrites a
           different file)
    .venv\\Scripts\\python.exe dev_tools\\verified_evidence.py restore <dir>
        -> copies back ONLY files whose SHA-256 equals the reviewed pin

Nothing here compiles, executes or touches a database. Restoring cannot
create evidence: a file is accepted only if it is byte-identical to the
reviewed record.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dev_tools import recorded_evidence_grading as reg  # noqa: E402

MANIFEST = "VERIFIED_EVIDENCE_MANIFEST.json"
PRESENT, MISSING, MISMATCH = "PRESENT_VERIFIED", "MISSING", "HASH_MISMATCH"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def status(reports_dir=None, records=None):
    reports_dir = Path(reports_dir or reg.REPORTS)
    records = reg.REVIEWED_RECORDS if records is None else records
    result = {}
    for key, record in records.items():
        path = reports_dir / record["report"]
        if not path.exists():
            result[key] = MISSING
        elif _sha(path) != record["report_sha256"]:
            result[key] = MISMATCH
        else:
            result[key] = PRESENT
    return result


def backup(target, reports_dir=None, records=None):
    reports_dir = Path(reports_dir or reg.REPORTS)
    records = reg.REVIEWED_RECORDS if records is None else records
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    states = status(reports_dir, records)
    if any(s != PRESENT for s in states.values()):
        raise RuntimeError(f"refusing to back up incomplete evidence: {states}")
    copied = []
    for record in records.values():
        dest = target / record["report"]
        if dest.exists():
            if _sha(dest) != record["report_sha256"]:
                raise RuntimeError(f"{dest} exists with different content")
            continue
        shutil.copy2(reports_dir / record["report"], dest)
        copied.append(record["report"])
    manifest = {key: {"report": r["report"], "report_sha256": r["report_sha256"],
                      "fixture_sha256": r["fixture_sha256"]} for key, r in records.items()}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True),
                                   encoding="utf-8")
    return copied


def restore(source, reports_dir=None, records=None):
    reports_dir = Path(reports_dir or reg.REPORTS)
    records = reg.REVIEWED_RECORDS if records is None else records
    reports_dir.mkdir(parents=True, exist_ok=True)
    restored, rejected = [], []
    for key, record in records.items():
        src = Path(source) / record["report"]
        dest = reports_dir / record["report"]
        if dest.exists() and _sha(dest) == record["report_sha256"]:
            continue
        if src.exists() and _sha(src) == record["report_sha256"]:
            shutil.copy2(src, dest)
            restored.append(key)
        else:
            rejected.append(key)
    return restored, rejected


def main():
    parser = argparse.ArgumentParser(description="Recorded-evidence durability")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("directory")
    args = parser.parse_args()
    if args.cmd == "status":
        states = status()
        print(json.dumps(states, indent=1))
        if any(s != PRESENT for s in states.values()):
            sys.exit("VERIFIED GRADING UNAVAILABLE for the records above that are not "
                     f"{PRESENT}: restore them with 'restore <backup dir>'.")
    elif args.cmd == "backup":
        print("copied:", backup(args.directory), "| manifest:", MANIFEST)
    else:
        restored, rejected = restore(args.directory)
        print("restored:", restored, "| not restorable (missing or wrong hash):", rejected)
        if rejected:
            sys.exit(1)


if __name__ == "__main__":
    main()
