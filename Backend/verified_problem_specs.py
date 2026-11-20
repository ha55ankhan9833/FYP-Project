"""
Server-controlled eligibility of verified oracles WITHOUT a teacher reference
solution.

A verified oracle (verified_oracles.py) may serve an assessment only through
one of two reviewed bindings:
  1. reference binding (existing): the assessment's APPROVED reference
     solution has the oracle's reviewed SHA-256;
  2. problem-spec binding (this module): the assessment's complete
     specification matches an owner-reviewed entry of
     verified_problem_specs.json.

A spec matches only when EVERY field matches exactly, after normalisation of
Unicode (NFKC, <= / >= / minus signs), letter case and whitespace runs:
  problem_statement, input_format, output_format   one of the reviewed texts
  approved value constraints                        the reviewed set, exactly
  approved input schema                             the reviewed schema, exactly
                                                    (canonical check_schema form)
and the oracle itself accepts the schema (verified domain). There is no
keyword, title or similarity matching: a reworded statement, changed output
order, wider bounds or different schema is simply not verified, and the
caller reports VERIFICATION_REQUIRED.

The registry is loaded from the repository file only; no API writes it.
A malformed entry makes the whole registry unavailable (fail closed).
"""

import hashlib
import json
import logging
import unicodedata
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "verified_problem_specs.json"
SOURCE = "VERIFIED_PROBLEM_SPEC"
TEXT_FIELDS = ("problem_statement", "input_format", "output_format")
_PLURAL = {"problem_statement": "problem_statements", "input_format": "input_formats",
           "output_format": "output_formats"}

logger = logging.getLogger(__name__)


class RegistryError(ValueError):
    pass


class VerificationRequired(ValueError):
    """No trusted verifier supports this assessment's exact specification."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


TEACHER_MESSAGE = (
    "Expected outputs for this problem cannot be verified automatically yet. "
    "No test cases were generated. The problem, its input/output format, the "
    "approved bounds and the input schema must exactly match a reviewed problem "
    "specification, or an approved verifier (reviewed reference solution or "
    "secure sandbox) must be set up for it.")


def normalize_text(value):
    text = unicodedata.normalize("NFKC", value or "")
    for a, b in (("≤", "<="), ("≥", ">="), ("−", "-"), ("–", "-")):
        text = text.replace(a, b)
    return " ".join(text.split()).casefold()


def _constraint_set(value_constraints):
    if isinstance(value_constraints, str):
        value_constraints = value_constraints.splitlines()
    return frozenset(normalize_text(line) for line in (value_constraints or []) if line.strip())


def canonical_schema(schema):
    if schema is None:
        return None
    from input_schema import check_schema
    return json.dumps(check_schema(schema), sort_keys=True, separators=(",", ":"))


def _validated_spec(raw, oracles):
    if not isinstance(raw, dict):
        raise RegistryError("spec must be an object")
    spec_id, oracle_id = raw.get("spec_id"), raw.get("oracle_id")
    if not isinstance(spec_id, str) or not spec_id.strip():
        raise RegistryError("spec_id is required")
    if oracle_id not in oracles:
        raise RegistryError(f"{spec_id}: unknown oracle {oracle_id!r}")
    oracle = oracles[oracle_id]
    texts = {}
    for field in TEXT_FIELDS:
        values = raw.get(_PLURAL[field])
        if (not isinstance(values, list) or not values
                or not all(isinstance(v, str) and v.strip() for v in values)):
            raise RegistryError(f"{spec_id}: {_PLURAL[field]} must be a non-empty text list")
        texts[field] = frozenset(normalize_text(v) for v in values)
    constraints = _constraint_set(raw.get("value_constraints"))
    if not constraints:
        raise RegistryError(f"{spec_id}: value_constraints are required")
    schema = raw.get("input_schema")
    if getattr(oracle, "structured", False):
        if schema is None:
            raise RegistryError(f"{spec_id}: structured oracle needs input_schema")
        canonical = canonical_schema(schema)            # SchemaError is a ValueError
        oracle.check_schema(json.loads(canonical))     # inside the verified domain
        from teacher_approval_service import schema_constraint_mismatches
        if schema_constraint_mismatches(list(raw["value_constraints"]), schema):
            raise RegistryError(f"{spec_id}: value_constraints contradict input_schema")
    else:
        if schema is not None:
            raise RegistryError(f"{spec_id}: legacy oracle cannot take an input_schema")
        canonical = None
        from generic_test_inputs import parse_approved_value_constraints
        oracle.check_domain(parse_approved_value_constraints("\n".join(raw["value_constraints"])))
    fingerprint = hashlib.sha256(json.dumps(
        {"spec_id": spec_id, "oracle_id": oracle_id,
         "texts": {f: sorted(v) for f, v in texts.items()},
         "constraints": sorted(constraints), "schema": canonical},
        sort_keys=True).encode("utf-8")).hexdigest()
    return {"spec_id": spec_id.strip(), "oracle_id": oracle_id, "oracle": oracle,
            "description": raw.get("description", ""), "texts": texts,
            "constraints": constraints, "schema": canonical, "fingerprint": fingerprint}


def load_specs(path=None, oracles=None):
    """All reviewed specs, validated. Raises RegistryError when malformed."""
    if oracles is None:
        from verified_oracles import VERIFIED_ORACLES
        oracles = VERIFIED_ORACLES
    path = Path(path or REGISTRY_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RegistryError(f"verified problem spec registry unreadable: {error}")
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(
            data.get("specs"), list):
        raise RegistryError("verified problem spec registry has an unknown format")
    specs, seen = [], set()
    for raw in data["specs"]:
        try:
            spec = _validated_spec(raw, oracles)
        except RegistryError:
            raise
        except ValueError as error:                      # SchemaError / OracleError
            raise RegistryError(f"{raw.get('spec_id')}: {error}")
        if spec["spec_id"] in seen:
            raise RegistryError(f"duplicate spec_id {spec['spec_id']}")
        seen.add(spec["spec_id"])
        specs.append(spec)
    return specs


def match_problem_spec(problem_statement, input_format, output_format,
                       approved_value_constraints, approved_input_schema, specs=None):
    """
    Return (spec, None) for the single exactly matching reviewed spec, or
    (None, details) explaining which fields differ from the closest spec.
    Never raises for a non-matching problem; RegistryError propagates.
    """
    if specs is None:
        specs = load_specs()
    fields = {"problem_statement": normalize_text(problem_statement),
              "input_format": normalize_text(input_format),
              "output_format": normalize_text(output_format)}
    constraints = _constraint_set(approved_value_constraints)
    try:
        schema = canonical_schema(approved_input_schema)
    except ValueError:
        schema = "invalid"

    matches, closest = [], None
    for spec in specs:
        differs = [f for f in TEXT_FIELDS if fields[f] not in spec["texts"][f]]
        if constraints != spec["constraints"]:
            differs.append("value_constraints")
        if schema != spec["schema"]:
            differs.append("input_schema")
        if not differs:
            matches.append(spec)
        elif closest is None or len(differs) < len(closest[1]):
            closest = (spec, differs)

    if len(matches) == 1:
        spec = matches[0]
        if spec["schema"] is not None:
            spec["oracle"].check_schema(json.loads(spec["schema"]))
        return spec, None
    if len(matches) > 1:              # ambiguous registry: refuse rather than guess
        return None, {"reason": "AMBIGUOUS_SPEC", "spec_ids": [m["spec_id"] for m in matches]}
    if closest is None:
        return None, {"reason": "NO_REVIEWED_SPECS"}
    return None, {"reason": "SPEC_MISMATCH", "closest_spec_id": closest[0]["spec_id"],
                  "differs_in": closest[1]}


def safe_match(*args, **kwargs):
    """match_problem_spec, but a broken registry counts as 'not verified'."""
    try:
        return match_problem_spec(*args, **kwargs)
    except RegistryError as error:
        logger.error("Verified problem spec registry unavailable: %s", error)
        return None, {"reason": "REGISTRY_UNAVAILABLE"}


def public_source(spec):
    return {"source": SOURCE, "endpoint": "generate-test-cases/verified",
            "oracle_id": spec["oracle_id"], "spec_id": spec["spec_id"],
            "reference_required": False}
