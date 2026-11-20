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

Equivalence evidence for max_min_v1 (reference sha256 89aca8e6...):
    the secondary algorithm IS a line-by-line transcription of the reference
    loop; fixtures/verify_max_min_oracle.py checks primary == transcription
    for every N=1 and N=2 array over the full value domain, every N=3 array
    over 21 boundary values, and 200,000 seeded random arrays with
    N in 1..100 (fixtures/max_min_oracle/oracle_verification.json).
    The reference is never compiled or executed.
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
    structured = False         # legacy 1-3 integer input format only

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


# ------------------------------------------------- count-prefixed arrays

@dataclass(frozen=True)
class CountPrefixedArrayOracle:
    """
    Oracle for a teacher-approved STRUCTURED schema of exactly this shape:
        variables: {<count>: {min, max}}
        items:     [{"type": "ints", "vars": [<count>]},
                    {"type": "array", "name": <any>, "length": <count>,
                     "min": .., "max": .., distinct: false, order: null}]
        relations: []
    Every input is validated against the approved schema BEFORE either
    algorithm runs; the approved ranges must lie inside the verified domain.
    """
    oracle_id: str
    description: str
    reference_sha256: str
    count_minimum: int
    count_maximum: int
    value_minimum: int
    value_maximum: int
    primary: Callable          # list[int] -> str
    secondary: Callable        # list[int] -> str (independent algorithm)
    structured = True

    def check_schema(self, schema):
        if not isinstance(schema, dict):
            raise OracleError(f"Oracle {self.oracle_id} requires an approved input schema")
        variables = schema.get("variables") or {}
        items = schema.get("items") or []
        if schema.get("relations"):
            raise OracleError(f"Oracle {self.oracle_id} does not support schema relations")
        if len(variables) != 1 or len(items) != 2:
            raise OracleError(f"Oracle {self.oracle_id} supports only one count "
                              "line followed by one array")
        (count_name, count_spec), = variables.items()
        head, array = items
        if head.get("type") != "ints" or head.get("vars") != [count_name]:
            raise OracleError("The first input line must be the element count only")
        if (array.get("type") != "array" or array.get("length") != count_name
                or array.get("distinct") or array.get("order") is not None):
            raise OracleError("The second input line must be an unrestricted "
                              "array of exactly N integers")
        if (count_spec.get("min") < self.count_minimum
                or count_spec.get("max") > self.count_maximum):
            raise OracleError(
                f"Approved range for {count_name} is outside the domain oracle "
                f"{self.oracle_id} was verified for "
                f"[{self.count_minimum}, {self.count_maximum}]")
        if array.get("min") < self.value_minimum or array.get("max") > self.value_maximum:
            raise OracleError(
                f"Approved element range is outside the domain oracle "
                f"{self.oracle_id} was verified for "
                f"[{self.value_minimum}, {self.value_maximum}]")

    def expected_output(self, schema, input_data):
        from input_schema import validate_input

        self.check_schema(schema)
        check = validate_input(schema, input_data)
        if not check["valid"]:
            first = check["errors"][0]
            raise OracleError(f"Input rejected by the approved schema before the "
                              f"oracle ran: {first['code']}")
        tokens = input_data.split()
        count = int(tokens[0])
        values = [int(t) for t in tokens[1:]]
        if len(values) != count or not self.count_minimum <= count <= self.count_maximum:
            raise OracleError("Input does not match the oracle's count-prefixed format")
        if any(not self.value_minimum <= v <= self.value_maximum for v in values):
            raise OracleError("Input outside verified oracle domain")
        first = self.primary(values)
        second = self.secondary(values)
        if first != second:
            raise OracleError(
                f"Independent oracle implementations disagree on {input_data!r}")
        return first


def _max_min_builtin(values):
    return f"{max(values)}\n{min(values)}\n"


def _max_min_reference_transcription(values):
    """Line-by-line transcription of max_min_reference.cpp's loop."""
    maximum = minimum = 0
    for i, a in enumerate(values):
        if i == 0:
            maximum = a
            minimum = a
        else:
            if a > maximum:
                maximum = a
            if a < minimum:
                minimum = a
    return f"{maximum}\n{minimum}\n"


MAX_MIN_V1 = CountPrefixedArrayOracle(
    oracle_id="max_min_v1",
    description=("Prints the maximum then the minimum of a count-prefixed integer "
                 "array, each newline-terminated; equivalent to fixtures/"
                 "reference_solutions/max_min_reference.cpp for 1 <= N <= 100, "
                 "-1000 <= A[i] <= 1000"),
    reference_sha256=(
        "89aca8e6279d32de33fd2b65366ec813c55164743c3d4704604aef3c5ae17240"),
    count_minimum=1,
    count_maximum=100,
    value_minimum=-1000,
    value_maximum=1000,
    primary=_max_min_builtin,
    secondary=_max_min_reference_transcription,
)


VERIFIED_ORACLES = {PRIME_CHECK_V1.oracle_id: PRIME_CHECK_V1,
                    MAX_MIN_V1.oracle_id: MAX_MIN_V1}


def get_verified_oracle(oracle_id):
    if not isinstance(oracle_id, str) or oracle_id not in VERIFIED_ORACLES:
        raise OracleError("Unknown or unverified oracle")
    return VERIFIED_ORACLES[oracle_id]
