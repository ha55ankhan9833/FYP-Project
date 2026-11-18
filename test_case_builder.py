"""
Pure test-case construction: generic inputs + teacher edge cases, and
expected outputs from a caller-supplied run_batch function.

This module must never import anything that compiles or executes code
(cpp_runner, cpp_compiler, subprocess). Execution back-ends live in
test_case_pipeline (host / sandbox) and oracle_test_case_service
(verified Python oracle).
"""

from generic_test_inputs import (
    MAX_RANDOM_CASES,
    generate_inputs,
    parse_approved_value_constraints,
    validate_inputs
)
from test_case_policy import REQUIRED_TEST_CASE_COUNT, enforce_test_case_count


MAX_EXPECTED_OUTPUT_CHARS = 64 * 1024
MAX_ADDITIONAL_INPUTS = REQUIRED_TEST_CASE_COUNT


def _check_output(index, output):
    if not isinstance(output, str):
        raise RuntimeError(f"Reference produced no output on test {index}")
    if not output.strip():
        raise RuntimeError(f"Reference produced empty output on test {index}")
    if len(output) > MAX_EXPECTED_OUTPUT_CHARS:
        raise RuntimeError(f"Reference output too large on test {index}")
    return output


def merge_additional_inputs(variables, generated, additional_inputs):
    """
    Append teacher-specified inputs (e.g. known primes, perfect squares)
    after the generated ones. Each must satisfy the approved constraints.
    Inputs equal to a generated one are skipped; repeats inside the
    teacher list are rejected.
    """

    if additional_inputs is None:
        return list(generated)

    if not isinstance(additional_inputs, list):
        raise ValueError("additional_inputs must be a list")

    if len(additional_inputs) > MAX_ADDITIONAL_INPUTS:
        raise ValueError(
            f"At most {MAX_ADDITIONAL_INPUTS} additional inputs are allowed")

    merged = list(generated)
    existing = {" ".join(c["input_data"].split()) for c in generated}
    teacher_seen = set()
    added = 0

    for raw in additional_inputs:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 200:
            raise ValueError("Each additional input must be short non-empty text")
        normalized = " ".join(raw.split())
        if normalized in teacher_seen:
            raise ValueError(f"Duplicate additional input: {normalized}")
        teacher_seen.add(normalized)
        validate_inputs(variables, [{"input_data": normalized}])
        if normalized in existing:
            continue
        existing.add(normalized)
        added += 1
        merged.append({"input_data": normalized,
                       "category": "teacher_specified",
                       "test_name": f"Teacher Case {added}"})

    return merged


def select_inputs(variables, additional_inputs=None):
    """
    Exactly REQUIRED_TEST_CASE_COUNT unique inputs, in priority order:
      1. structural cases (boundaries, near-boundaries, midpoint, 0/1/-1)
      2. teacher-specified additional_inputs
      3. deterministic random cases (seeded) to fill the remainder
    Refuses (never silently drops) when 1 + 2 exceed the required count.
    """

    structural = [c for c in generate_inputs(variables, random_count=0)]
    chosen = merge_additional_inputs(variables, structural, additional_inputs)

    if len(chosen) > REQUIRED_TEST_CASE_COUNT:
        excess = len(chosen) - REQUIRED_TEST_CASE_COUNT
        raise ValueError(
            f"{len(structural)} boundary/edge cases plus "
            f"{len(chosen) - len(structural)} additional inputs exceed the "
            f"fixed {REQUIRED_TEST_CASE_COUNT} test cases; remove {excess} "
            f"additional input(s)")

    pool = [c for c in generate_inputs(variables,
                                       random_count=MAX_RANDOM_CASES)
            if c["category"] == "random"]
    used = {" ".join(c["input_data"].split()) for c in chosen}
    filled = 0

    for candidate in pool:
        if len(chosen) == REQUIRED_TEST_CASE_COUNT:
            break
        key = " ".join(candidate["input_data"].split())
        if key in used:
            continue
        used.add(key)
        filled += 1
        chosen.append({"input_data": candidate["input_data"],
                       "category": "random",
                       "test_name": f"Random Case {filled}"})

    if len(chosen) != REQUIRED_TEST_CASE_COUNT:
        raise ValueError(
            f"The approved constraints allow only {len(chosen)} distinct "
            f"inputs; {REQUIRED_TEST_CASE_COUNT} unique test cases are required")

    return chosen


# --------------------------------------------------------------------------
# Structured inputs (teacher-approved input schema). The legacy functions
# above are unchanged; these are used only when the finalized approval draft
# carries an input schema.
# --------------------------------------------------------------------------

MAX_STRUCTURED_INPUT_CHARS = 10_000      # secure_test_case_save.MAX_INPUT_CHARS
STRUCTURED_FILL_SEED = 42


class StructuredInputError(ValueError):
    """A teacher input failed schema validation; `details` lists every problem."""

    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def select_structured_inputs(input_schema, additional_inputs=None):
    """
    Exactly REQUIRED_TEST_CASE_COUNT unique inputs for a CHECKED schema:
      1. teacher additional_inputs, EXACT text (spaces and newlines kept),
         each validated against the schema (all problems reported together)
      2. deterministic baseline candidates (boundary cases first) to fill
    Duplicates (exact text, final newline optional) are rejected in the
    teacher list and skipped among generated candidates. Unknown or invalid
    inputs are never accepted.
    """
    from baseline_input_generator import MAX_COUNT, generate_candidates
    from input_schema import validate_input
    from test_case_validator import structured_input_key

    if additional_inputs is not None and not isinstance(additional_inputs, list):
        raise ValueError("additional_inputs must be a list")
    additional_inputs = additional_inputs or []
    if len(additional_inputs) > REQUIRED_TEST_CASE_COUNT:
        raise ValueError(f"At most {REQUIRED_TEST_CASE_COUNT} additional inputs are allowed")

    chosen, seen, problems = [], {}, []
    for index, raw in enumerate(additional_inputs):
        if not isinstance(raw, str) or not raw.strip():
            problems.append({"index": index, "errors": [{"code": "EMPTY_INPUT",
                                                         "message": "input is empty"}]})
            continue
        if len(raw) > MAX_STRUCTURED_INPUT_CHARS:
            problems.append({"index": index, "errors": [{
                "code": "INPUT_TOO_LARGE",
                "message": f"input exceeds {MAX_STRUCTURED_INPUT_CHARS} characters"}]})
            continue
        if "\r" in raw:
            problems.append({"index": index, "errors": [{
                "code": "CARRIAGE_RETURN", "message": "use \\n line endings only"}]})
            continue
        key = structured_input_key(raw)
        if key in seen:
            problems.append({"index": index, "errors": [{
                "code": "DUPLICATE_INPUT",
                "message": f"same as additional input {seen[key]}"}]})
            continue
        check = validate_input(input_schema, raw)
        if not check["valid"]:
            problems.append({"index": index, "errors": check["errors"]})
            continue
        seen[key] = index
        chosen.append({"input_data": raw, "category": "teacher_specified",
                       "test_name": f"Teacher Case {len(chosen) + 1}"})

    if problems:
        raise StructuredInputError(
            f"{len(problems)} additional input(s) are not valid for the approved input schema",
            problems)

    if len(chosen) < REQUIRED_TEST_CASE_COUNT:
        generated = generate_candidates(input_schema, count=MAX_COUNT,
                                        seed=STRUCTURED_FILL_SEED)["candidates"]
        filled = 0
        for candidate in generated:
            if len(chosen) == REQUIRED_TEST_CASE_COUNT:
                break
            key = structured_input_key(candidate["input"])
            if key in seen:
                continue
            seen[key] = None
            filled += 1
            chosen.append({"input_data": candidate["input"],
                           "category": candidate["category"][:50],
                           "test_name": f"Generated Case {filled}"})

    if len(chosen) != REQUIRED_TEST_CASE_COUNT:
        raise ValueError(
            f"The approved input schema allows only {len(chosen)} distinct inputs; "
            f"{REQUIRED_TEST_CASE_COUNT} unique test cases are required")

    return chosen


def build_structured_test_cases(input_schema, run_batch, additional_inputs=None):
    """
    Like build_test_cases, for a teacher-approved structured schema. The
    reference receives EXACTLY the stored input text (no newline appended,
    no whitespace collapsed), which is also exactly what grading feeds to
    student programs. run_batch is called twice (determinism check).
    """
    inputs = select_structured_inputs(input_schema, additional_inputs)
    stdin_values = [item["input_data"] for item in inputs]

    first = run_batch(stdin_values)
    second = run_batch(stdin_values)

    if len(first) != len(inputs) or len(second) != len(inputs):
        raise RuntimeError("Reference run returned wrong number of results")

    test_cases = []
    for index, (item, out1, out2) in enumerate(zip(inputs, first, second), start=1):
        out1 = _check_output(index, out1)
        if out1 != out2:
            raise RuntimeError(f"Reference solution is non-deterministic on test {index}")
        test_cases.append({"test_number": index, "test_name": item["test_name"],
                           "category": item["category"], "input_data": item["input_data"],
                           "expected_output": out1})

    enforce_test_case_count(len(test_cases), "Generated test set")
    return test_cases


def build_test_cases(constraints, run_batch, additional_inputs=None):
    """
    Generic, assessment-independent construction of EXACTLY
    REQUIRED_TEST_CASE_COUNT test cases.

    run_batch(list_of_input_strings) -> list of stdout strings (or
    raises). It is called TWICE; differing outputs mean the producer
    is non-deterministic and generation is refused.
    """

    variables = parse_approved_value_constraints(constraints)
    inputs = select_inputs(variables, additional_inputs)

    stdin_values = [item["input_data"] + "\n" for item in inputs]

    first = run_batch(stdin_values)
    second = run_batch(stdin_values)

    if len(first) != len(inputs) or len(second) != len(inputs):
        raise RuntimeError("Reference run returned wrong number of results")

    test_cases = []

    for index, (item, out1, out2) in enumerate(
            zip(inputs, first, second), start=1):
        out1 = _check_output(index, out1)
        if out1 != out2:
            raise RuntimeError(
                f"Reference solution is non-deterministic on test {index}")

        test_cases.append({
            "test_number": index,
            "test_name": item["test_name"],
            "category": item["category"],
            "input_data": item["input_data"],
            "expected_output": out1
        })

    enforce_test_case_count(len(test_cases), "Generated test set")
    return test_cases
