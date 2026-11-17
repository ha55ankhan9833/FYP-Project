from numeric_constraint_validator import extract_explicit_bounds

import os
import re
import torch

from functools import lru_cache
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

BASE_PATH = os.environ.get(
    "FYP_MODEL_BASE_PATH",
    "/content/drive/MyDrive/FYP_AI_Training"
)

MODEL_PATH = os.path.join(
    BASE_PATH,
    "flan_t5_constraint_final"
)

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

    text = str(raw_text)

    replacements = {
        "[SP]": " ",
        "[B3C]": "<",
        "[B3E]": ">",
        "[B3A]": "&"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return re.sub(r"\s+", " ", text).strip()


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


def generate_value_constraints(
    problem,
    input_format,
    output_format
):

    tokenizer, model, device = load_model()

    prompt = (
        "Generate value constraints for the following C++ "
        "programming problem.\n"
        f"Problem: {problem}\n"
        f"Input Format: {input_format}\n"
        f"Output Format: {output_format}"
    )

    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=512
    ).to(device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=128,
            num_beams=4,
            do_sample=False
        )

    raw = tokenizer.decode(
        outputs[0],
        skip_special_tokens=True
    )

    cleaned = clean_constraint_text(raw)

    source = " ".join([
        problem,
        input_format,
        output_format
    ])

    source_numbers = set(re.findall(r"\d+", source))
    predicted_numbers = set(re.findall(r"\d+", cleaned))

    unsupported = sorted(
        predicted_numbers - source_numbers
    )

    return {
        "text": cleaned,
        "status": (
            "UNVERIFIED_SUGGESTION"
            if unsupported
            else "NEEDS_SEMANTIC_REVIEW"
        ),
        "unsupported_numbers": unsupported
    }


def analyze_cpp_problem(
    problem,
    input_format,
    output_format
):

    value_result = generate_value_constraints(
        problem,
        input_format,
        output_format
    )

    source = "\n".join([
        problem,
        input_format,
        output_format
    ])

    explicit_bounds = extract_explicit_bounds(source)

    if explicit_bounds:
        value_constraints = explicit_bounds
    else:
        value_constraints = [value_result]

    return {
        "value_constraints": value_constraints,
        "code_constraints": extract_code_constraints(source),
        "teacher_approval_required": True
    }
