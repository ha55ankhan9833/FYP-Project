"""
Retrievers for RAG over the 5,636 TRAINING-split problems.

  bge     dense, BAAI/bge-small-en-v1.5 (index_train_v1, 384-d)
  flan    dense, mean-pooled encoder of the fine-tuned FLAN-T5 constraint model
          (index_flan_train_v1, 512-d) - the generator's own encoder, no extra model
  tfidf   lexical TF-IDF (tfidf_train_v1)
  hybrid  Reciprocal Rank Fusion of bge + flan + tfidf (no tuned weights)

Every index must contain exactly the same problem-id list as the bge reference
index (same problem_ids_sha256), and every manifest must say split == "train".
All query/corpus text uses embedding_model.problem_text (description + input
format + output format). Requires numpy (.venv_ai); torch only for bge/flan.
"""

import json
import math
import re
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import embedding_model as em
import problem_embedding_index as pei
from rag_context import (DEFAULT_K_MAX, ContextError, reciprocal_rank_fusion,
                         select_context)

EMBEDDINGS_DIR = Path(__file__).resolve().parent / "models" / "embeddings"
BGE_INDEX_DIR = EMBEDDINGS_DIR / "index_train_v1"
FLAN_INDEX_DIR = EMBEDDINGS_DIR / "index_flan_train_v1"
TFIDF_INDEX_DIR = EMBEDDINGS_DIR / "tfidf_train_v1"
RETRIEVAL_MODES = ("bge", "flan", "tfidf", "hybrid")
N_CANDIDATES = 10        # candidates offered to the budget-filling step
HYBRID_DEPTH = 50        # depth of each list fused by RRF
TOKEN = re.compile(r"[a-z0-9]+")
FLAN_MAX_LENGTH = 512


# ------------------------------------------------------------------ TF-IDF
class TfidfIndex:
    """Sublinear tf, smoothed idf, L2-normalised documents; inverted postings."""

    def __init__(self, problem_ids, terms, idf, ptr, docs, weights):
        self.problem_ids = list(problem_ids)
        self.term_row = {t: i for i, t in enumerate(terms)}
        self.terms, self.idf, self.ptr, self.docs, self.weights = terms, idf, ptr, docs, weights

    @classmethod
    def build(cls, problem_ids, texts):
        import numpy as np
        counts = [Counter(TOKEN.findall(t.lower())) for t in texts]
        df = Counter(term for c in counts for term in c)
        n = len(texts)
        terms = sorted(df)
        idf = np.array([math.log((1 + n) / (1 + df[t])) + 1 for t in terms], np.float32)
        row = {t: i for i, t in enumerate(terms)}
        postings = defaultdict(list)
        for d, c in enumerate(counts):
            w = {t: (1 + math.log(v)) * float(idf[row[t]]) for t, v in c.items()}
            norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
            for t, x in w.items():
                postings[row[t]].append((d, x / norm))
        ptr, docs, weights = [0], [], []
        for i in range(len(terms)):
            for d, x in postings[i]:
                docs.append(d)
                weights.append(x)
            ptr.append(len(docs))
        return cls(problem_ids, terms, idf, np.array(ptr, np.int64),
                   np.array(docs, np.int32), np.array(weights, np.float32))

    def scores(self, text):
        import numpy as np
        c = Counter(TOKEN.findall(text.lower()))
        w = {self.term_row[t]: (1 + math.log(v)) * float(self.idf[self.term_row[t]])
             for t, v in c.items() if t in self.term_row}
        norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
        out = np.zeros(len(self.problem_ids), np.float32)
        for i, x in w.items():
            lo, hi = int(self.ptr[i]), int(self.ptr[i + 1])
            out[self.docs[lo:hi]] += self.weights[lo:hi] * (x / norm)
        return out

    def rank(self, text, n):
        import numpy as np
        s = self.scores(text)
        order = np.argsort(-s, kind="stable")[:n]
        return [(self.problem_ids[int(i)], float(s[int(i)])) for i in order]

    def save(self, directory, metadata, manifest_extra):
        import numpy as np
        directory = Path(directory)
        directory.mkdir(parents=True)
        terms_blob = np.frombuffer("\n".join(self.terms).encode("utf-8"), dtype=np.uint8)
        np.savez(directory / "tfidf.npz", terms=terms_blob, idf=self.idf, ptr=self.ptr,
                 docs=self.docs, weights=self.weights)
        (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False),
                                                 encoding="utf-8")
        manifest = {"format": pei.INDEX_FORMAT, "kind": "tfidf", "split": pei.INDEX_SPLIT,
                    "count": len(self.problem_ids), "vocabulary": len(self.terms),
                    "problem_ids_sha256": pei.ids_digest(self.problem_ids),
                    "tfidf_sha256": pei.sha256_bytes((directory / "tfidf.npz").read_bytes()),
                    **manifest_extra}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        return manifest

    @classmethod
    def load(cls, directory, split_of=None):
        import numpy as np
        directory = Path(directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if manifest.get("kind") != "tfidf":
            raise pei.IndexIntegrityError("Not a TF-IDF index")
        ids = pei.validate_manifest(manifest, metadata, split_of)
        if pei.sha256_bytes((directory / "tfidf.npz").read_bytes()) != manifest.get("tfidf_sha256"):
            raise pei.IndexIntegrityError("tfidf.npz does not match the manifest hash")
        data = np.load(directory / "tfidf.npz", allow_pickle=False)
        terms = bytes(data["terms"]).decode("utf-8").split("\n")
        if len(terms) != manifest["vocabulary"] or len(data["ptr"]) != len(terms) + 1:
            raise pei.IndexIntegrityError("TF-IDF arrays are inconsistent")
        index = cls(ids, terms, data["idf"], data["ptr"], data["docs"], data["weights"])
        return index, metadata, manifest


# ------------------------------------------------------------------ FLAN-T5 encoder
def flan_embed(texts, tokenizer, model, batch_size=16):
    """Mean-pooled, L2-normalised encoder states of the FLAN-T5 constraint model."""
    import numpy as np
    import torch
    encoder = model.get_encoder()
    out = []
    for start in range(0, len(texts), batch_size):
        batch = tokenizer(list(texts[start:start + batch_size]), padding=True, truncation=True,
                          max_length=FLAN_MAX_LENGTH, return_tensors="pt")
        batch = {k: v.to(encoder.device) for k, v in batch.items()}
        with torch.no_grad():
            hidden = encoder(input_ids=batch["input_ids"],
                             attention_mask=batch["attention_mask"]).last_hidden_state
        mask = batch["attention_mask"].unsqueeze(-1).float()
        pooled = (hidden * mask).sum(1) / mask.sum(1)
        out.append(torch.nn.functional.normalize(pooled, p=2, dim=1).cpu().numpy()
                   .astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, 512), np.float32)


# ------------------------------------------------------------------ retrievers
class DenseRetriever:
    def __init__(self, name, index, encode_text):
        self.name, self.index, self.encode_text = name, index, encode_text

    def rank(self, text, n):
        return self.index.rank(self.encode_text(text), n)


class TfidfRetriever:
    name = "tfidf"

    def __init__(self, index):
        self.index = index

    def rank(self, text, n):
        return self.index.rank(text, n)


class HybridRetriever:
    name = "hybrid"

    def __init__(self, retrievers, depth=HYBRID_DEPTH):
        self.retrievers, self.depth = list(retrievers), depth

    def rank(self, text, n):
        lists = [[pid for pid, _ in r.rank(text, self.depth)] for r in self.retrievers]
        return reciprocal_rank_fusion(lists, n=n)


class RagContextProvider:
    """
    Callable used by ai_constraint_service.generate_value_constraints:
        provider(problem, input_format, output_format, count_tokens) -> selection
    """

    def __init__(self, retriever, metadata_by_id, allowed_ids, k_max=DEFAULT_K_MAX,
                 example_format="encoded", n_candidates=N_CANDIDATES):
        self.retriever = retriever
        self.metadata_by_id = metadata_by_id
        self.allowed_ids = frozenset(allowed_ids)
        self.k_max, self.example_format, self.n_candidates = k_max, example_format, n_candidates

    def __call__(self, problem, input_format, output_format, count_tokens, exclude_ids=()):
        started = time.perf_counter()
        ranked = self.retriever.rank(em.problem_text(problem, input_format, output_format),
                                     self.n_candidates + len(set(exclude_ids)))
        retrieval_ms = (time.perf_counter() - started) * 1000
        candidates = []
        for pid, score in ranked:
            if pid not in self.allowed_ids:
                raise ContextError(f"Retrieved problem {pid!r} is not a training-split problem")
            meta = self.metadata_by_id[pid]
            candidates.append({"problem_id": pid, "score": score,
                               "input_format": meta["input_format"],
                               "value_constraints": meta["value_constraints"]})
        selection = select_context(problem, input_format, output_format, candidates,
                                   count_tokens, self.allowed_ids, k_max=self.k_max,
                                   example_format=self.example_format, exclude_ids=exclude_ids)
        selection["method"] = self.retriever.name
        selection["retrieval_ms"] = round(retrieval_ms, 2)
        return selection


# ------------------------------------------------------------------ loading
def _check_same_ids(reference_manifest, manifest, name):
    if manifest.get("problem_ids_sha256") != reference_manifest.get("problem_ids_sha256"):
        raise pei.IndexIntegrityError(f"{name} index does not cover the same training problems")


def load_retriever(mode, generator=None, split_of=None):
    """
    mode: bge | flan | tfidf | hybrid. generator=(tokenizer, model) of the FLAN-T5
    constraint model (needed for flan / hybrid). Returns (retriever, metadata_by_id,
    allowed_ids). Every index is verified; all must share the bge index's id list.
    """
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode {mode!r}")
    reference = pei.load_index(BGE_INDEX_DIR, split_of)
    metadata_by_id = {row["problem_id"]: row for row in reference.metadata}
    allowed = frozenset(reference.problem_ids)

    def bge():
        return DenseRetriever("bge", reference, lambda text: em.encode([text])[0])

    def flan():
        if generator is None:
            raise ValueError("The flan retriever needs the FLAN-T5 generator")
        index = pei.load_index(FLAN_INDEX_DIR, split_of)
        _check_same_ids(reference.manifest, index.manifest, "FLAN")
        tokenizer, model = generator
        return DenseRetriever("flan", index, lambda text: flan_embed([text], tokenizer, model)[0])

    def tfidf():
        index, _meta, manifest = TfidfIndex.load(TFIDF_INDEX_DIR, split_of)
        _check_same_ids(reference.manifest, manifest, "TF-IDF")
        return TfidfRetriever(index)

    if mode == "bge":
        retriever = bge()
    elif mode == "flan":
        retriever = flan()
    elif mode == "tfidf":
        retriever = tfidf()
    else:
        retriever = HybridRetriever([bge(), flan(), tfidf()])
    return retriever, metadata_by_id, allowed


@lru_cache(maxsize=4)
def live_provider(mode, k_max=DEFAULT_K_MAX, example_format="encoded"):
    """Provider for the running AI service (only used when RAG_MODE != "off")."""
    import ai_constraint_service as svc
    tokenizer, model, _device = svc.load_model()
    retriever, metadata_by_id, allowed = load_retriever(mode, generator=(tokenizer, model))
    return RagContextProvider(retriever, metadata_by_id, allowed, k_max=k_max,
                              example_format=example_format)
