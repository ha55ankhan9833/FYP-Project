"""
READ-ONLY retrieval evaluation: validation problems (queries) against the
training-split index (corpus). The test split is never touched.

    .venv_ai\\Scripts\\python.exe dev_tools\\evaluate_retrieval.py ^
        <original dataset .json> <FYP_Complete_Dataset.json> [index_dir]

Methods compared on the SAME corpus (5,636 training problems) and the same text
(description + input format + output format):
  bge            BAAI/bge-small-en-v1.5, CLS pooling, L2 (index vectors as built)
  bge+instr      same corpus vectors; queries get the model's retrieval instruction
  tfidf          lexical baseline (sublinear tf, smoothed idf, L2), NumPy only
  flan_encoder   existing fine-tuned FLAN-T5 constraint model encoder, mean pooling, L2

Weak relevance labels derived from the data (no human annotation):
  same_constraints   training problem with identical Value_Constraints text
  same_input_format  training problem with identical Input_Specification text
Metrics: Recall@1/5/10 and MRR over queries that have >= 1 relevant item.
"""

import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev_tools"))

import embedding_model as em  # noqa: E402
import problem_embedding_index as pei  # noqa: E402
from build_problem_index import DEFAULT_OUT, load_inputs, peak_memory_mb  # noqa: E402

FLAN_DIR = ROOT / "models" / "flan_t5_constraint_final"
TOKEN = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- TF-IDF
class TfIdf:
    def __init__(self, corpus):
        import numpy as np
        self.np = np
        docs = [Counter(TOKEN.findall(t.lower())) for t in corpus]
        df = Counter(term for d in docs for term in d)
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.n = n
        postings = defaultdict(list)
        for i, d in enumerate(docs):
            w = {t: (1 + math.log(c)) * self.idf[t] for t, c in d.items()}
            norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
            for t, v in w.items():
                postings[t].append((i, v / norm))
        self.post = {t: (np.array([i for i, _ in p]), np.array([v for _, v in p], np.float32))
                     for t, p in postings.items()}

    def scores(self, text):
        q = Counter(TOKEN.findall(text.lower()))
        w = {t: (1 + math.log(c)) * self.idf[t] for t, c in q.items() if t in self.idf}
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        s = self.np.zeros(self.n, self.np.float32)
        for t, v in w.items():
            idx, vals = self.post[t]
            s[idx] += vals * (v / norm)
        return s


# ---------------------------------------------------------------- FLAN-T5 encoder
def flan_encode(texts, batch_size=16):
    import numpy as np
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(str(FLAN_DIR), local_files_only=True)
    enc = AutoModelForSeq2SeqLM.from_pretrained(str(FLAN_DIR), local_files_only=True).get_encoder()
    enc.eval()
    out = []
    for i in range(0, len(texts), batch_size):
        b = tok(texts[i:i + batch_size], padding=True, truncation=True, max_length=512,
                return_tensors="pt")
        with torch.no_grad():
            h = enc(input_ids=b["input_ids"], attention_mask=b["attention_mask"]).last_hidden_state
        m = b["attention_mask"].unsqueeze(-1).float()
        v = torch.nn.functional.normalize((h * m).sum(1) / m.sum(1), p=2, dim=1)
        out.append(v.numpy().astype(np.float32))
    return np.concatenate(out)


# ---------------------------------------------------------------- metrics
def evaluate(score_rows, relevant_sets):
    import numpy as np
    hits = {1: 0, 5: 0, 10: 0}
    rr, n = 0.0, 0
    for scores, relevant in zip(score_rows, relevant_sets):
        if not relevant:
            continue
        n += 1
        order = np.argsort(-scores, kind="stable")
        ranks = np.nonzero(np.isin(order, list(relevant)))[0]
        first = int(ranks[0]) + 1
        rr += 1.0 / first
        for k in hits:
            hits[k] += first <= k
    return {"queries": n, **{f"recall@{k}": round(v / n, 4) for k, v in hits.items()},
            "mrr": round(rr / n, 4)}


def main(original_path, splits_path, index_dir=DEFAULT_OUT):
    import numpy as np

    original, split_of, _ = load_inputs(original_path, splits_path)
    t0 = time.perf_counter()
    index = pei.load_index(index_dir, split_of)
    load_seconds = time.perf_counter() - t0
    train_ids = index.problem_ids
    val_ids = sorted((p for p, s in split_of.items() if s == "validation"), key=lambda p: int(p[1:]))
    assert not set(val_ids) & set(train_ids)

    def text(pid):
        r = original[pid]
        return em.problem_text(r["Problem_Description"], r["Input_Specification"],
                               r["Output_Specification"])

    train_texts = [text(p) for p in train_ids]
    val_texts = [text(p) for p in val_ids]

    def norm(s):
        return s.strip()

    by_cons, by_inp = defaultdict(set), defaultdict(set)
    for i, p in enumerate(train_ids):
        by_cons[norm(original[p]["Value_Constraints"])].add(i)
        by_inp[norm(original[p]["Input_Specification"])].add(i)
    labels = {
        "same_constraints": [by_cons.get(norm(original[p]["Value_Constraints"]), set()) for p in val_ids],
        "same_input_format": [by_inp.get(norm(original[p]["Input_Specification"]), set()) for p in val_ids],
    }

    results, timing = {}, {}

    # bge (query = same procedure as corpus) and bge + instruction
    t = time.perf_counter()
    q_bge = em.encode(val_texts)
    timing["bge_query_encode_s_per_query"] = (time.perf_counter() - t) / len(val_texts)
    s_bge = q_bge @ index.vectors.T
    t = time.perf_counter()
    for row in q_bge[:200]:
        index.search(row, k=5)
    timing["search_ms_per_query"] = (time.perf_counter() - t) / 200 * 1000
    q_bgei = em.encode(val_texts, instruction=em.QUERY_INSTRUCTION)
    s_bgei = q_bgei @ index.vectors.T

    t = time.perf_counter()
    tfidf = TfIdf(train_texts)
    s_tfidf = np.stack([tfidf.scores(x) for x in val_texts])
    timing["tfidf_total_s"] = time.perf_counter() - t

    t = time.perf_counter()
    flan_corpus = flan_encode(train_texts)
    flan_q = flan_encode(val_texts)
    timing["flan_encode_total_s"] = time.perf_counter() - t
    s_flan = flan_q @ flan_corpus.T

    for name, s in (("bge", s_bge), ("bge+instr", s_bgei), ("tfidf", s_tfidf), ("flan_encoder", s_flan)):
        results[name] = {lab: evaluate(s, rel) for lab, rel in labels.items()}

    top1 = s_bge.max(axis=1)
    single = em.encode([val_texts[0]])  # warm
    t = time.perf_counter()
    for x in val_texts[:20]:
        index.search(em.encode([x])[0], k=5)
    timing["end_to_end_query_s"] = (time.perf_counter() - t) / 20

    report = {
        "queries": len(val_ids), "corpus": len(train_ids),
        "queries_with_relevant": {k: sum(bool(r) for r in v) for k, v in labels.items()},
        "results": results,
        "bge_top1_cosine": {"mean": round(float(top1.mean()), 4),
                            "median": round(float(np.median(top1)), 4),
                            "min": round(float(top1.min()), 4), "max": round(float(top1.max()), 4)},
        "validation_near_duplicates_in_train_cos_ge_0.95": int((top1 >= 0.95).sum()),
        "timing": {k: round(v, 4) for k, v in timing.items()},
        "index_load_s": round(load_seconds, 3),
        "peak_memory_mb": peak_memory_mb(),
    }
    del single
    out = Path(index_dir) / "evaluation.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
