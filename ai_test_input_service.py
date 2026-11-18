"""
Inference interface for the (future) LoRA-trained test-input generator.

OFF by default. The suggestion endpoint calls generate_raw_candidates() for
method="model"; every returned text is then validated and deduplicated by
test_input_suggestion_service exactly like baseline candidates, so enabling a
trained model later needs no endpoint change.

Expected model folder (git-ignored), produced by training/test_input_lora.ipynb:
    models/test_input_generator_v1/
        config.json, generation_config.json, model.safetensors, tokenizer files
        TRAINING_MANIFEST.json   includes "model_safetensors_sha256"
The merged (LoRA merge_and_unload) checkpoint is a plain T5 model, so local
CPU inference needs only torch + transformers (already in .venv_ai), not peft.
torch/transformers are imported lazily; this module imports nothing heavy.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from test_input_codec import CodecError, build_generation_prompt, decode_model_output

TEST_INPUT_AI_MODE = "off"            # "off" | "on" (changing it needs approval)
MODEL_DIR = Path(__file__).resolve().parent / "models" / "test_input_generator_v1"
MANIFEST_NAME = "TRAINING_MANIFEST.json"
INPUT_TOKEN_LIMIT = 512
MAX_NEW_TOKENS = 256
MAX_SAMPLES = 16
GENERATION = {"do_sample": True, "top_p": 0.95, "temperature": 0.9}
MODEL_NAME = "flan_t5_small_lora_test_input_v1"


class ModelUnavailable(RuntimeError):
    pass


def status():
    if TEST_INPUT_AI_MODE != "on":
        return {"mode": TEST_INPUT_AI_MODE, "available": False,
                "reason": "AI test-input generation is disabled (TEST_INPUT_AI_MODE='off')"}
    manifest = MODEL_DIR / MANIFEST_NAME
    weights = MODEL_DIR / "model.safetensors"
    if not manifest.is_file() or not weights.is_file():
        return {"mode": TEST_INPUT_AI_MODE, "available": False,
                "reason": "Trained test-input model is not installed"}
    return {"mode": TEST_INPUT_AI_MODE, "available": True, "reason": None}


def _verify_weights():
    manifest = json.loads((MODEL_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected = manifest.get("model_safetensors_sha256")
    digest = hashlib.sha256()
    with open(MODEL_DIR / "model.safetensors", "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if not expected or digest.hexdigest() != expected:
        raise ModelUnavailable("Test-input model weights do not match TRAINING_MANIFEST.json")
    return manifest


@lru_cache(maxsize=1)
def _load():
    manifest = _verify_weights()
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    del torch  # imported to fail early with a clear error if torch is missing
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    model = AutoModelForSeq2SeqLM.from_pretrained(str(MODEL_DIR))
    model.eval()
    return tokenizer, model, manifest


def generate_raw_candidates(problem, count):
    """{'texts': [decoded input or None], 'model': {...}} -- texts are NOT validated here."""
    state = status()
    if not state["available"]:
        raise ModelUnavailable(state["reason"])
    try:
        prompt = build_generation_prompt(problem.get("problem_statement"), problem.get("input_format"),
                                         problem.get("output_format"), problem.get("value_constraints"),
                                         problem.get("sample_input"))
    except CodecError as error:
        raise ValueError(f"Cannot build the model prompt: {error}") from error

    tokenizer, model, manifest = _load()
    import torch

    encoded = tokenizer(prompt, return_tensors="pt", truncation=False)
    input_tokens = int(encoded["input_ids"].shape[1])
    if input_tokens > INPUT_TOKEN_LIMIT:
        raise ValueError(f"INPUT_TOO_LONG: the prompt has {input_tokens} tokens "
                         f"(limit {INPUT_TOKEN_LIMIT}); use the deterministic baseline")
    samples = max(1, min(MAX_SAMPLES, count))
    with torch.no_grad():
        outputs = model.generate(**encoded, max_new_tokens=MAX_NEW_TOKENS,
                                 num_return_sequences=samples, **GENERATION)
    texts, warnings = [], []
    for row in outputs:
        raw = tokenizer.decode(row, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        text, problems = decode_model_output(raw)
        if tokenizer.eos_token_id not in [int(t) for t in row[1:]]:   # no EOS -> cut off
            text, problems = None, problems + ["OUTPUT_TRUNCATED"]
        texts.append(text)
        warnings.extend(problems)
    return {"texts": texts,
            "model": {"name": MODEL_NAME, "input_tokens": input_tokens, "samples": samples,
                      "generation": dict(GENERATION, max_new_tokens=MAX_NEW_TOKENS),
                      "training_manifest": {k: manifest.get(k) for k in
                                            ("dataset_manifest_sha256", "base_model_revision",
                                             "model_safetensors_sha256")},
                      "warnings": sorted(set(warnings))}}
