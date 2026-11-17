from numeric_constraint_validator import extract_explicit_bounds

import os
import re

from functools import lru_cache

from constraint_codec import (
    INPUT_TOKEN_LIMIT,
    MAX_NEW_TOKENS,
    decode_model_output,
    input_length_warning,
    output_truncated_warning,
    unsupported_numbers,
)

BASE_PATH = os.environ.get(
    "FYP_MODEL_BASE_PATH",
    "/content/drive/MyDrive/FYP_AI_Training"
)

MODEL_PATH = os.path.join(
    BASE_PATH,
    "flan_t5_constraint_final"
)

# Generation settings validated in the training notebook (cell 93):
# beam search with 4 beams, early stopping, greedy (no sampling).
NUM_BEAMS = 4

# RAG feature flag (docs/AI_RAG_CONSTRAINT_PLAN.md). Server-side only; OFF until
# a measured validation result justifies enabling it. "off" keeps the original
# prompt byte-for-byte. Other values: "bge", "flan", "tfidf", "hybrid".
RAG_MODE = "off"
RAG_K_MAX = 3
RAG_EXAMPLE_FORMAT = "encoded"


def _retrieval_unavailable(detail):
    return {
        "code": "RETRIEVAL_UNAVAILABLE",
        "message": ("Similar-problem retrieval failed; the suggestion was generated "
                    "without retrieved examples (baseline prompt). " + detail)[:500],
    }

CODE_PATTERNS = {
    "recursion_required": [
        r"\brecursiv(?:e|ely)\b",
        r"\brecursion\b"
    ],
    "recursion_prohibited": [
        r"\b(?:without|avoid|do not|don't|must not)\s+(?:using\s+)?recursion\b"
    ],
    "dynamic_programming_required": [
        r"\b(?:use|using|apply|implement(?:ing)?)\s+dynamic\s+programming\b"
    ],
    "function_required": [
        r"\b(?:dedicated|separate|user-defined)\s+(?:value-returning\s+)?function\b",
        r"\bfunction\s+(?:must|should)\b"
    ],
    "loop_required": [
        r"\busing\s+(?:a\s+)?loop\b",
        r"\b(?:must|should)\s+use\s+(?:a\s+)?loop\b"
    ],
    "loops_prohibited": [
        r"\bwithout\s+(?:using|utilizing)\s+loops?\b",
        r"\bwithout\s+(?:using|utilizing)\s+iterative\s+looping\s+constructs\b"
    ],
    "stl_container_required": [
        r"\bstd::(?:set|map|vector|unordered_map)\b"
    ],
    "hash_map_required": [
        r"\b(?:hash\s*map|unordered_map)\b"
    ],
    "gaussian_elimination_prohibited": [
        r"\bwithout\s+using\s+gaussian\s+elimination\b"
    ]
}


@lru_cache(maxsize=1)
def load_model():
    # Imported lazily so the pure logic of this module (prompting, decoding,
    # validation) can be tested without torch / transformers installed.
    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

    device = "cuda" if torch.cuda.is_available() else "cpu"

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_PATH,
        local_files_only=True
    )

    model = AutoModelForSeq2SeqLM.from_pretrained(
        MODEL_PATH,
        local_files_only=True
    ).to(device)

    model.eval()

    return tokenizer, model, device


def clean_constraint_text(raw_text):
    """
    Decode the model's placeholder encoding ([SP], [NL], [BXX], ...) with the
    authoritative mapping from the training notebook (constraint_codec).
    Kept for backward compatibility; returns the decoded text only.
    """
    text, _warnings = decode_model_output(str(raw_text))
    return text


def extract_code_constraints(statement):

    results = []

    for constraint_type, patterns in CODE_PATTERNS.items():

        for pattern in patterns:

            match = re.search(
                pattern,
                statement,
                flags=re.IGNORECASE
            )

            if match:
                results.append({
                    "type": constraint_type,
                    "evidence": match.group(0),
                    "status": "NEEDS_TEACHER_REVIEW"
                })
                break

    # Avoid contradictory requirements from naive matching
    types = {item["type"] for item in results}

    if "recursion_prohibited" in types:
        results = [
            x for x in results
            if x["type"] != "recursion_required"
        ]

    if "loops_prohibited" in types:
        results = [
            x for x in results
            if x["type"] != "loop_required"
        ]

    return results


def build_prompt(problem, input_format, output_format):
    # Identical to the training prompt (notebook cell 84).
    return (
        "Generate value constraints for the following C++ "
        "programming problem.\n"
        f"Problem: {problem}\n"
        f"Input Format: {input_format}\n"
        f"Output Format: {output_format}"
    )


def _generated_token_count(sequence, eos_token_id):
    """
    sequence = decoder output ids, starting with the decoder start token.
    Returns (tokens generated incl. </s>, finished_with_eos).
    """
    generated = list(sequence[1:])
    if eos_token_id in generated:
        return generated.index(eos_token_id) + 1, True
    return len(generated), False


def generate_value_constraints(
    problem,
    input_format,
    output_format,
    context_provider=None
):
    """
    context_provider=None -> the original prompt, unchanged (RAG off).
    context_provider(problem, input_format, output_format, count_tokens) ->
    rag_context selection; used only by the RAG flag or the offline evaluation.
    """

    tokenizer, model, device = load_model()

    prompt = build_prompt(problem, input_format, output_format)

    def count_tokens(text):
        return len(tokenizer(text, truncation=False)["input_ids"])

    # D4: measure the full prompt (incl. </s>) before inference and refuse,
    # rather than silently truncating, anything the model cannot read whole.
    input_tokens = count_tokens(prompt)
    too_long = input_length_warning(input_tokens)

    if too_long:
        return {
            "text": "",
            "status": "NOT_GENERATED",
            "unsupported_numbers": [],
            "warnings": [too_long],
            "input_tokens": input_tokens,
            "generated_tokens": 0,
            "output_complete": False
        }

    retrieval = None
    retrieval_warnings = []

    if context_provider is not None:
        try:
            selection = context_provider(problem, input_format, output_format,
                                         count_tokens)
            if selection["prompt_tokens"] > INPUT_TOKEN_LIMIT:
                raise ValueError("context selection exceeded the token limit")
            prompt = selection["prompt"]
            input_tokens = selection["prompt_tokens"]
            retrieval = {
                "status": "USED" if selection["k_used"] else "NO_EXAMPLE_FITS",
                "method": selection.get("method"),
                "k_used": selection["k_used"],
                "context_tokens": selection["context_tokens"],
                "skipped_for_budget": selection["skipped_for_budget"],
                "skipped_duplicates": selection["skipped_duplicates"],
                "retrieval_ms": selection.get("retrieval_ms"),
                "examples": selection["examples"],
                "example_constraints": selection["example_constraints"],
            }
        except Exception as error:        # never fail the request because of RAG
            retrieval = {"status": "FAILED", "method": None, "k_used": 0}
            retrieval_warnings.append(_retrieval_unavailable(type(error).__name__))

    # Defence in depth: nothing longer than the limit ever reaches the model.
    if input_tokens > INPUT_TOKEN_LIMIT:
        raise RuntimeError("Prompt exceeds the model input limit")

    import torch

    inputs = tokenizer(
        prompt,
        return_tensors="pt"
    ).to(device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,      # D2
            num_beams=NUM_BEAMS,
            early_stopping=True,
            do_sample=False
        )

    sequence = outputs[0].tolist()
    generated_tokens, finished = _generated_token_count(
        sequence, tokenizer.eos_token_id)

    raw = tokenizer.decode(
        sequence,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False
    )

    # D1: full placeholder decoding before anything else looks at the text.
    cleaned, warnings = decode_model_output(raw)

    if not finished:
        warnings.append(output_truncated_warning(generated_tokens))

    source = " ".join([
        problem,
        input_format,
        output_format
    ])

    # D3: numeric support is checked on the DECODED text only.
    unsupported = unsupported_numbers(cleaned, source)

    result = {
        "text": cleaned,
        "status": (
            "UNVERIFIED_SUGGESTION"
            if unsupported or warnings
            else "NEEDS_SEMANTIC_REVIEW"
        ),
        "unsupported_numbers": unsupported,
        "warnings": warnings,
        "input_tokens": input_tokens,
        "generated_tokens": generated_tokens,
        "output_complete": finished
    }

    # Keys only exist when retrieval was attempted, so the RAG-off result is
    # exactly the pre-RAG result.
    if retrieval is not None:
        result["retrieval"] = retrieval
        result["retrieval_warnings"] = retrieval_warnings

    return result


def _rag_provider():
    """Live provider for RAG_MODE, or (None, warning) if it cannot be built."""
    if RAG_MODE == "off":
        return None, None
    try:
        import rag_retrievers
        return rag_retrievers.live_provider(RAG_MODE, RAG_K_MAX, RAG_EXAMPLE_FORMAT), None
    except Exception as error:
        return None, _retrieval_unavailable(type(error).__name__)


def analyze_cpp_problem(
    problem,
    input_format,
    output_format
):

    provider, provider_warning = _rag_provider()

    if provider is None:
        value_result = generate_value_constraints(
            problem,
            input_format,
            output_format
        )
    else:
        value_result = generate_value_constraints(
            problem,
            input_format,
            output_format,
            context_provider=provider
        )

    source = "\n".join([
        problem,
        input_format,
        output_format
    ])

    explicit_bounds = extract_explicit_bounds(source)
    model_ran = value_result["status"] != "NOT_GENERATED"

    if explicit_bounds:
        value_constraints = explicit_bounds
    elif model_ran:
        value_constraints = [{
            "text": value_result["text"],
            "status": value_result["status"],
            "unsupported_numbers": value_result["unsupported_numbers"],
            "warnings": value_result["warnings"]
        }]
    else:
        # Never present an empty or truncated-input guess as a suggestion.
        value_constraints = []

    warnings = [] if model_ran else list(value_result["warnings"])

    model_info = {
        "status": "GENERATED" if model_ran else "NOT_RUN_INPUT_TOO_LONG",
        "suggestion_used": bool(model_ran and not explicit_bounds),
        "input_tokens": value_result["input_tokens"],
        "input_token_limit": INPUT_TOKEN_LIMIT,
        "generated_tokens": value_result["generated_tokens"],
        "max_new_tokens": MAX_NEW_TOKENS,
        "output_complete": value_result["output_complete"]
    }

    # RAG fields appear only when RAG_MODE != "off" (response unchanged otherwise).
    if RAG_MODE != "off":
        retrieval = dict(value_result.get("retrieval") or
                         {"status": "FAILED" if provider_warning else "NOT_ATTEMPTED",
                          "method": None, "k_used": 0})
        retrieval.pop("example_constraints", None)   # IDs and scores are enough for display
        model_info["retrieval"] = retrieval
        if provider_warning:
            warnings.append(provider_warning)
        warnings.extend(value_result.get("retrieval_warnings", []))

    return {
        "value_constraints": value_constraints,
        "code_constraints": extract_code_constraints(source),
        "teacher_approval_required": True,
        "warnings": warnings,
        "model": model_info
    }
