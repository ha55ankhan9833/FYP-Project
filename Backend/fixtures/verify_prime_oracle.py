"""
Evidence that verified oracle prime_check_v1 is equivalent to the approved
C++ reference (fixtures/reference_solutions/prime_check_reference.cpp) on
the whole approved domain, and preview of Assessment 5's 10 test cases.

No database access, no C++ compilation or execution.

    python fixtures/verify_prime_oracle.py
"""

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle_test_case_service import generate_from_verified_oracle  # noqa: E402
from verified_oracles import (PRIME_CHECK_V1, _prime_by_miller_rabin,  # noqa: E402
                              _prime_by_sieve)

OUT = ROOT / "fixtures" / "new_assessment_csc101" / "oracle_verification.json"
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "prime_check_reference.cpp"
GENERATION = json.loads((ROOT / "fixtures/new_assessment_csc101/"
                         "07_generate_test_cases.json").read_text())["body"]


def reference_transcription(n):
    """Line-by-line Python transcription of the approved C++ reference."""
    prime = n >= 2                                   # bool prime = n >= 2;
    d = 2                                            # for (long long d = 2;
    while prime and d * d <= n:                      #  prime && d * d <= n;
        if n % d == 0:                               #  if (n % d == 0)
            prime = False                            #      prime = false;
        d += 1                                       #  ++d)
    return "YES\n" if prime else "NO\n"              # cout << ... << '\n';


def main():
    source = REFERENCE.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    assert digest == PRIME_CHECK_V1.reference_sha256, "reference hash changed"

    started = time.time()
    mismatches = []
    primes = 0
    for n in range(1, PRIME_CHECK_V1.domain_maximum + 1):
        a = _prime_by_sieve((n,))
        b = _prime_by_miller_rabin((n,))
        c = reference_transcription(n)
        primes += a == "YES\n"
        if not a == b == c:
            mismatches.append(n)
            if len(mismatches) > 10:
                break
    exhaustive = {
        "domain": [1, PRIME_CHECK_V1.domain_maximum],
        "values_checked": PRIME_CHECK_V1.domain_maximum,
        "implementations": ["sieve_of_eratosthenes", "miller_rabin_bases_2_3_5_7",
                            "cpp_reference_transcription"],
        "mismatches": mismatches,
        "primes_found": primes,
        "known_prime_count_pi_10e6": 78498,
        "seconds": round(time.time() - started, 1),
    }

    preview = generate_from_verified_oracle(
        assessment_id=5, teacher_id="BIIT235", oracle_id="prime_check_v1",
        additional_inputs=GENERATION["additional_inputs"], dry_run=True,
        preflight_fn=lambda a, t: {"source_hash": digest,
                                   "reference_solution_id": 2,
                                   "constraints": "1 <= N <= 1000000"})
    cases = preview["test_cases"]

    previous = json.loads(OUT.read_text()) if OUT.exists() else {}
    superseded = previous.get("superseded_15_case_preview")
    if superseded is None and len(previous.get("assessment_5_preview", [])) == 15:
        superseded = previous["assessment_5_preview"]

    report = {"reference_sha256": digest,
              "oracle_id": PRIME_CHECK_V1.oracle_id,
              "exhaustive_equivalence": exhaustive,
              "assessment_5_preview": cases,
              "superseded_15_case_preview": superseded,
              "superseded_note": ("15 cases saved to the database on "
                                  "2026-11-17 before the 10-test rule; see "
                                  "migration_to_10_tests.json")}
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(exhaustive, indent=1))
    for c in cases:
        n = int(c["input_data"])
        print(f"{c['test_number']:>2} {c['category']:<17} N={n:<8} "
              f"expected={c['expected_output'].strip():<3} "
              f"transcription={reference_transcription(n).strip()}")
    print("written:", OUT)


if __name__ == "__main__":
    main()
