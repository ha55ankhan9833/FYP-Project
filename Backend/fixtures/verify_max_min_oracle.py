"""
Equivalence evidence for verified_oracles.MAX_MIN_V1 (no C++ is compiled or run).

The oracle's secondary algorithm is a line-by-line transcription of
fixtures/reference_solutions/max_min_reference.cpp. This script checks
primary (built-in max/min) == transcription, through the real
expected_output() entry point (schema validation included), on:
  - every N=1 array over the full value domain [-1000, 1000]
  - every N=2 array over the full value domain (2001^2 = 4,004,001 arrays)
  - every N=3 array over 21 boundary/near-boundary values (9,261 arrays)
  - 200,000 seeded random arrays with N uniform in 1..100
It also confirms the reference file's SHA-256 is the one the oracle is bound to.

    .venv\\Scripts\\python.exe fixtures\\verify_max_min_oracle.py
Writes fixtures/max_min_oracle/oracle_verification.json.
"""

import hashlib
import itertools
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from input_schema import check_schema  # noqa: E402
from verified_oracles import MAX_MIN_V1  # noqa: E402

REFERENCE = ROOT / "fixtures" / "reference_solutions" / "max_min_reference.cpp"
OUT = ROOT / "fixtures" / "max_min_oracle" / "oracle_verification.json"
SCHEMA = check_schema({
    "version": 1, "variables": {"N": {"min": 1, "max": 100}},
    "items": [{"type": "ints", "vars": ["N"]},
              {"type": "array", "name": "A", "length": "N", "min": -1000, "max": 1000}],
    "relations": []})
BOUNDARY = [-1000, -999, -998, -501, -500, -499, -2, -1, 0, 1, 2,
            499, 500, 501, 998, 999, 1000, -100, 100, -10, 10]
SEED = 20261120


def text(values):
    return f"{len(values)}\n{' '.join(map(str, values))}\n"


def check_fast(values):
    """Both algorithms directly (the N=2 sweep is too large for schema parsing)."""
    a = MAX_MIN_V1.primary(list(values))
    b = MAX_MIN_V1.secondary(list(values))
    if a != b:
        raise AssertionError(f"disagree on {values}: {a!r} vs {b!r}")


def check_full(values):
    """Through expected_output(): schema validation + both algorithms."""
    out = MAX_MIN_V1.expected_output(SCHEMA, text(values))
    expected_max, expected_min = max(values), min(values)
    if out != f"{expected_max}\n{expected_min}\n":
        raise AssertionError(f"unexpected output on {values}: {out!r}")


def main():
    started = time.time()
    digest = hashlib.sha256(REFERENCE.read_bytes()).hexdigest()
    if digest != MAX_MIN_V1.reference_sha256:
        raise SystemExit(f"Reference hash {digest} does not match the oracle binding")

    counts = {}
    domain = range(-1000, 1001)
    for v in domain:
        check_full([v])
    counts["n1_full_domain"] = len(domain)

    n2 = 0
    for pair in itertools.product(domain, repeat=2):
        check_fast(pair)
        n2 += 1
    for pair in itertools.product(BOUNDARY, repeat=2):
        check_full(list(pair))
    counts["n2_full_domain"] = n2

    n3 = 0
    for triple in itertools.product(BOUNDARY, repeat=3):
        check_full(list(triple))
        n3 += 1
    counts["n3_boundary_values"] = n3

    rng = random.Random(SEED)
    for _ in range(200_000):
        n = rng.randint(1, 100)
        check_full([rng.randint(-1000, 1000) for _ in range(n)])
    counts["random_arrays_n1_100"] = 200_000

    result = {
        "oracle_id": MAX_MIN_V1.oracle_id,
        "reference": "fixtures/reference_solutions/max_min_reference.cpp",
        "reference_sha256": digest,
        "method": ("primary (built-in max/min) vs line-by-line transcription of the "
                   "reference loop; checks marked full also go through schema "
                   "validation in expected_output()"),
        "random_seed": SEED,
        "checked": counts,
        "disagreements": 0,
        "cpp_compiled_or_executed": False,
        "seconds": round(time.time() - started, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
