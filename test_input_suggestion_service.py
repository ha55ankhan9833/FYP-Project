"""
Teacher-only test-INPUT suggestions (no database writes, no code execution,
no expected outputs).

Schema resolution order:
    1. explicit input_schema from the teacher (validated by input_schema.check_schema)
    2. the legacy integer grammar of the value constraints (1-3 integers, unchanged rule)
    3. conservative inference from the sample input (input_schema_inference)
    otherwise -> UnsupportedFormat with reasons (nothing is guessed)

Generation methods:
    deterministic_baseline  baseline_input_generator (default, always available)
    model                   ai_test_input_service (TEST_INPUT_AI_MODE, off by default)

Integration with the existing additional_inputs workflow is offered ONLY for
the legacy integer format, the only one test generation accepts today. The
suggested list is checked with the real test_case_builder.select_inputs rule
(exactly 10 cases), which is pure and runs nothing.
"""

from baseline_input_generator import DEFAULT_COUNT, MAX_COUNT, generate_candidates
from generic_test_inputs import generate_inputs, parse_approved_value_constraints
from input_schema import SchemaError, check_schema, schema_from_legacy_constraints, validate_input
from input_schema_inference import infer_schema
from test_case_policy import REQUIRED_TEST_CASE_COUNT
from test_input_codec import canonical_form

METHODS = ("deterministic_baseline", "model")
MAX_VALIDATE_INPUTS = 50
MAX_VALIDATE_CHARS = 100_000
ADDITIONAL_INPUT_MAX_CHARS = 200          # test_case_builder.merge_additional_inputs
CANDIDATE_NOTE = ("Candidates only: not verified test cases. No expected outputs are produced; "
                  "they come from the approved reference solution or a verified oracle when the "
                  "teacher generates the 10 test cases.")


class UnsupportedFormat(ValueError):
    def __init__(self, reasons):
        super().__init__("Input format is not supported for automatic suggestions")
        self.reasons = reasons


def resolve_schema(problem, input_schema=None, approved_schema=None):
    """(checked_schema, source, shape). Raises SchemaError or UnsupportedFormat.
    approved_schema: the CHECKED schema from the assessment's finalized
    approval draft (reused so the teacher does not repeat it)."""
    if input_schema is not None:
        return check_schema(input_schema), "explicit", "explicit"
    if approved_schema is not None:
        return approved_schema, "approved_input_schema", "approved"
    constraints = problem.get("value_constraints")
    legacy_error = None
    if isinstance(constraints, str) and constraints.strip():
        try:
            return schema_from_legacy_constraints(constraints), "legacy_constraints", "scalars"
        except ValueError as error:
            legacy_error = str(error)
    inferred = infer_schema(problem)
    if inferred["supported"]:
        return inferred["schema"], "inferred_from_sample", inferred["shape"]
    reasons = list(inferred["reasons"])
    if legacy_error:
        reasons.insert(0, f"LEGACY_CONSTRAINTS: {legacy_error}")
    reasons.append("Provide an explicit input_schema to describe this format.")
    raise UnsupportedFormat(reasons)


def _public_schema(schema):
    return {k: v for k, v in schema.items() if k != "legacy_constraints"}


def _additional_inputs_plan(schema, source, candidates, assessment_context):
    """Which candidates the existing generate endpoint would accept as additional_inputs."""
    plan = {"supported": False, "slots": 0, "structural_inputs": [], "suggested": [],
            "reason": None}
    if not assessment_context:
        plan["reason"] = "Only available for an existing assessment"
        return plan, set()
    if source == "approved_input_schema":
        return _structured_plan(schema, candidates, plan)
    if source != "legacy_constraints":
        plan["reason"] = ("Test generation currently accepts additional inputs only for the "
                          "1-3 integer format of the approved value constraints")
        return plan, set()

    from test_case_builder import select_inputs   # pure: no compiler / execution imports

    variables = parse_approved_value_constraints(schema["legacy_constraints"])
    structural = generate_inputs(variables, random_count=0)
    structural_keys = {" ".join(c["input_data"].split()) for c in structural}
    slots = max(0, REQUIRED_TEST_CASE_COUNT - len(structural))
    usable = []
    for candidate in candidates:
        text = candidate["input"]
        normalized = " ".join(text.split())
        if "\n" in text.rstrip("\n") or len(normalized) > ADDITIONAL_INPUT_MAX_CHARS:
            continue
        if normalized in structural_keys or normalized in usable:
            continue
        usable.append(normalized)
    suggested = usable[:slots]
    try:
        select_inputs(variables, suggested or None)   # the real 10-case rule (pure)
    except ValueError as error:
        plan.update({"slots": slots, "reason": f"Test generation would refuse: {error}"})
        return plan, set()
    plan.update({"supported": True, "slots": slots,
                 "structural_inputs": [c["input_data"] for c in structural],
                 "suggested": suggested,
                 "reason": ("Boundary cases are always included by test generation; "
                            "'suggested' fills the remaining slots")})
    return plan, set(usable)


def _structured_plan(schema, candidates, plan):
    """Exact-text additional_inputs for an assessment with an approved schema."""
    from test_case_builder import MAX_STRUCTURED_INPUT_CHARS, select_structured_inputs

    usable = [c["input"] for c in candidates if len(c["input"]) <= MAX_STRUCTURED_INPUT_CHARS]
    suggested = usable[:REQUIRED_TEST_CASE_COUNT]
    try:
        select_structured_inputs(schema, suggested or None)   # the real rule (pure)
    except ValueError as error:
        plan.update({"slots": REQUIRED_TEST_CASE_COUNT,
                     "reason": f"Test generation would refuse: {error}"})
        return plan, set()
    plan.update({"supported": True, "format": "exact_text",
                 "slots": REQUIRED_TEST_CASE_COUNT, "structural_inputs": [],
                 "suggested": suggested,
                 "reason": ("Approved input schema: inputs are kept exactly (spaces and "
                            "newlines); generated cases fill any remaining slots")})
    return plan, {" ".join(t.split()) for t in suggested}


def suggest(problem, input_schema=None, method="deterministic_baseline", count=DEFAULT_COUNT,
            seed=42, assessment_context=None):
    if method not in METHODS:
        raise ValueError(f"method must be one of {list(METHODS)}")
    if type(count) is not int or not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count must be an integer between 1 and {MAX_COUNT}")
    if type(seed) is not int or not 0 <= seed <= 2 ** 31:
        raise ValueError("seed must be a non-negative integer")

    approved = (assessment_context or {}).get("approved_input_schema")
    schema, source, shape = resolve_schema(problem, input_schema, approved)
    sample = problem.get("sample_input") or None

    if method == "deterministic_baseline":
        result = generate_candidates(schema, sample, count=count, seed=seed)
        candidates, rejected = result["candidates"], result["rejected"]
        method_info = {"name": result["method"], "seed": seed, "stats": result["stats"]}
    else:
        import ai_test_input_service as ai
        raw = ai.generate_raw_candidates(problem, count)
        candidates, rejected = [], []
        seen = {canonical_form(sample)} if sample else set()
        for text in raw["texts"]:
            if text is None:
                rejected.append({"category": "model", "stage": "decode",
                                 "errors": [{"code": "DECODE_INVALID",
                                             "message": "model output could not be decoded"}]})
                continue
            key = canonical_form(text)
            if key in seen:
                continue
            seen.add(key)
            check = validate_input(schema, text)
            if not check["valid"]:
                rejected.append({"category": "model", "stage": "validation",
                                 "input_preview": text[:120], "errors": check["errors"]})
                continue
            candidates.append({"input": text, "category": "model_sample",
                               "generation_method": raw["model"]["name"], "status": "CANDIDATE",
                               "validation": {"valid": True, "syntax_valid": True,
                                              "constraints_valid": True, "semantic_valid": True}})
        method_info = raw["model"]

    plan, usable = _additional_inputs_plan(schema, source, candidates, assessment_context)
    for candidate in candidates:
        candidate["usable_as_additional_input"] = (
            plan["supported"] and " ".join(candidate["input"].split()) in usable)

    return {
        "supported": True,
        "schema_source": source,
        "shape": shape,
        "input_schema": _public_schema(schema),
        "generation": method_info,
        "candidates": candidates,
        "rejected": rejected,
        "additional_inputs": plan,
        "note": CANDIDATE_NOTE,
    }


def validate_teacher_inputs(problem, inputs, input_schema=None):
    if not isinstance(inputs, list) or not inputs or len(inputs) > MAX_VALIDATE_INPUTS:
        raise ValueError(f"inputs must be a list of 1-{MAX_VALIDATE_INPUTS} strings")
    if not all(isinstance(i, str) and len(i) <= MAX_VALIDATE_CHARS for i in inputs):
        raise ValueError(f"each input must be a string of at most {MAX_VALIDATE_CHARS} characters")
    schema, source, shape = resolve_schema(problem, input_schema)
    results = []
    seen = {}
    for index, text in enumerate(inputs):
        check = validate_input(schema, text)
        key = canonical_form(text) if text.strip() else None
        duplicate_of = seen.get(key)
        if key is not None and duplicate_of is None:
            seen[key] = index
        results.append({"index": index, "valid": check["valid"],
                        "syntax_valid": check["syntax_valid"],
                        "constraints_valid": check["constraints_valid"],
                        "semantic_valid": check["semantic_valid"],
                        "duplicate_of": duplicate_of, "errors": check["errors"]})
    return {"schema_source": source, "shape": shape, "input_schema": _public_schema(schema),
            "valid_count": sum(r["valid"] and r["duplicate_of"] is None for r in results),
            "results": results}
