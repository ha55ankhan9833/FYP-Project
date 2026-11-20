"""
Local BAAI/bge-small-en-v1.5 sentence encoder (pinned revision, offline).

Procedure from the model's own files (models/embeddings/bge-small-en-v1.5):
  1_Pooling/config.json     -> CLS-token pooling (pooling_mode_cls_token = true)
  modules.json              -> Transformer -> Pooling -> Normalize (L2)
  sentence_bert_config.json -> max_seq_length 512 (lower-casing is done by the tokenizer)
  README                    -> the query instruction is only for short-query ->
                               passage search; for other cases (problem <-> problem
                               similarity here) no instruction is needed. Both were
                               evaluated (dev_tools/evaluate_retrieval.py).
Loading is local-only (local_files_only=True); nothing is downloaded at runtime.
Requires torch + transformers (.venv_ai).
"""

from functools import lru_cache
from pathlib import Path

MODEL_REPO = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_DIR = Path(__file__).resolve().parent / "models" / "embeddings" / "bge-small-en-v1.5"
MAX_LENGTH = 512
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def problem_text(description, input_format, output_format):
    """The text that represents a problem (what a teacher provides)."""
    return (f"{description.strip()}\n"
            f"Input format: {input_format.strip()}\n"
            f"Output format: {output_format.strip()}")


@lru_cache(maxsize=1)
def _load(model_dir=str(MODEL_DIR)):
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModel.from_pretrained(model_dir, local_files_only=True)
    model.eval()
    torch.set_grad_enabled(False)
    return tokenizer, model


def token_counts(texts):
    """Token counts with the model's own tokenizer, incl. [CLS]/[SEP], no truncation."""
    tokenizer, _model = _load()
    return [len(ids) for ids in tokenizer(list(texts), truncation=False)["input_ids"]]


def encode(texts, batch_size=16, instruction=None):
    """Return a float32 (len(texts), 384) array of L2-normalised CLS embeddings."""
    import numpy as np
    import torch

    tokenizer, model = _load()
    texts = [(instruction or "") + t for t in texts]
    chunks = []
    for start in range(0, len(texts), batch_size):
        batch = tokenizer(texts[start:start + batch_size], padding=True,
                          truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
        with torch.no_grad():
            cls = model(**batch).last_hidden_state[:, 0]
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)
        chunks.append(cls.cpu().numpy().astype(np.float32))
    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 384), np.float32)
