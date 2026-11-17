"""
Registry of REVIEWED Python oracles that may produce expected outputs
instead of executing a C++ reference solution.

This is deliberately NOT a way to upload expected outputs:
- Oracles are code in this repository, reviewed like any other code.
  Requests can only NAME an oracle; they cannot supply outputs or code.
- Each oracle is bound to the exact SHA-256 of the approved C++
  reference solution it was verified to be equivalent to. It can only be
  used for an assessment whose APPROVED reference has that hash.
- Each oracle computes every output with two independent algorithms;
  any disagreement aborts the import.
- Each oracle declares the input domain it was verified for; the
  assessment's approved constraints must lie inside it.

Equivalence evidence for prime_check_v1:
    fixtures/verify_prime_oracle.py checks the oracle against a
    line-by-line transcription of the C++ reference for EVERY N in
    1..1,000,000 (see fixtures/new_assessment_csc101/oracle_verification.json).
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable


class OracleError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedOracle:
    oracle_id: str
    description: str
    reference_sha256: str
    variable_count: int
    domain_minimum: int
    domain_maximum: int
    primary: Callable          # tuple[int] -> str
    secondary: Callable        # tuple[int] -> str (independent algorithm)

    def check_domain(self, variables):
        if len(variables) != self.variable_count:
            raise OracleError(
                f"Oracle {self.oracle_id} expects {self.variable_count} "
                f"input variable(s)")
        for v in variables:
            if v["minimum"] < self.domain_minimum or v["maximum"] > self.domain_maximum:
                raise OracleError(
                    f"Approved range for {v['variable']} is outside the domain "
                    f"oracle {self.oracle_id} was verified for "
                    f"[{self.domain_minimum}, {self.domain_maximum}]")

    def expected_output(self, input_data):
        tokens = input_data.split()
        if len(tokens) != self.variable_count:
            raise OracleError("Input does not match oracle arity")
        values = tuple(int(t) for t in tokens)
        for value in values:
            if not self.domain_minimum <= value <= self.domain_maximum:
                raise OracleError("Input outside verified oracle domain")
        first = self.primary(values)
        second = self.secondary(values)
        if first != second:
            raise OracleError(
                f"Independent oracle implementations disagree on {input_data!r}")
        return first


# ------------------------------------------------------------------ prime

PRIME_DOMAIN_MAX = 1_000_000


@lru_cache(maxsize=1)
def _prime_sieve():
    sieve = bytearray([1]) * (PRIME_DOMAIN_MAX + 1)
    sieve[0] = sieve[1] = 0
    i = 2
    while i * i <= PRIME_DOMAIN_MAX:
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(range(i * i, PRIME_DOMAIN_MAX + 1, i)))
        i += 1
    return bytes(sieve)


def _prime_by_sieve(values):
    (n,) = values
    return "YES\n" if _prime_sieve()[n] else "NO\n"


def _prime_by_miller_rabin(values):
    """Deterministic Miller-Rabin; bases 2,3,5,7 are exact below 3.2e9."""
    (n,) = values
    if n < 2:
        return "NO\n"
    for p in (2, 3, 5, 7):
        if n % p == 0:
            return "YES\n" if n == p else "NO\n"
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return "NO\n"
    return "YES\n"


PRIME_CHECK_V1 = VerifiedOracle(
    oracle_id="prime_check_v1",
    description=("Prints YES if N is prime else NO, newline-terminated; "
                 "equivalent to fixtures/reference_solutions/"
                 "prime_check_reference.cpp on 1..1,000,000"),
    reference_sha256=(
        "529e00e0a8e4000826d288f293e33e0e4753ecc49426156b9c936bb730aac9ab"),
    variable_count=1,
    domain_minimum=1,
    domain_maximum=PRIME_DOMAIN_MAX,
    primary=_prime_by_sieve,
    secondary=_prime_by_miller_rabin,
)


VERIFIED_ORACLES = {PRIME_CHECK_V1.oracle_id: PRIME_CHECK_V1}


def get_verified_oracle(oracle_id):
    if not isinstance(oracle_id, str) or oracle_id not in VERIFIED_ORACLES:
        raise OracleError("Unknown or unverified oracle")
    return VERIFIED_ORACLES[oracle_id]
