"""
Validation-only evaluation harness: baseline vs RAG constraint generation.
READ-ONLY on datasets, models and databases; writes only the files given.

1) Near-duplicate flags for all 716 validation problems (bge top-1 cosine vs the
   training index), computed ONCE so later runs do not need the bge model:
  .venv_ai\\Scripts\\python.exe dev_tools\\evaluate_rag.py near-duplicates ^
      --original <original.json> --splits <FYP_Complete_Dataset.json> --out <flags.json>

2) Run one configuration (checkpointed: one JSONL row per problem, flushed;
   re-running the same command resumes; a different configuration or run
   metadata in the same file is refused; a torn last line is dropped):
  .venv_ai\\Scripts\\python.exe dev_tools\\evaluate_rag.py run ^
      --original <original.json> --splits <FYP_Complete_Dataset.json> ^
      --near-duplicates <flags.json> --config baseline|bge|flan|tfidf|hybrid ^
      [--k-max 3] [--format encoded|raw] --subset dev|heldout [--limit N] --out <x.jsonl>

3) Compare two runs (paired, exact McNemar), overall and without near-duplicates:
  .venv_ai\\Scripts\\python.exe dev_tools\\evaluate_rag.py compare A.jsonl B.jsonl

Guards: queries come only from the validation split (dev/heldout by sha256(id) % 3);
any non-validation id is refused, so the 709-problem test split is never used.
Retrieval uses only the verified 5,636-problem training indexes.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dev_tools"))
os.environ.setdefault("FYP_MODEL_BASE_PATH", str(ROOT / "models"))

import rag_eval_metrics as metrics  # noqa: E402

NEAR_DUPLICATE = 0.95
HARNESS_VERSION = "rag-eval-v1"
FLAN_WEIGHTS = ROOT / "models" / "flan_t5_constraint_final" / "model.safetensors"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _row(pid, subset, label, run_meta, reference, result, near_duplicate, generation_s):
    text = result["text"]
    retrieval = result.get("retrieval") or {}
    examples = retrieval.get("example_constraints", [])
    precision, recall = metrics.number_precision_recall(text, reference)
    copied, invented = metrics.split_unsupported(result["unsupported_numbers"], examples)
    codes = [w["code"] for w in result["warnings"]] + \
            [w["code"] for w in result.get("retrieval_warnings", [])]
    copy_of_example = any(text.strip() == e for e in examples)
    return {
        "problem_id": pid, "subset": subset, "config": label, "run": run_meta,
        "near_duplicate": near_duplicate,
        "reference": reference, "generated": text, "status": result["status"],
        "exact": metrics.exact_match(text, reference),
        "numeric_match": metrics.numeric_match(text, reference),
        "number_precision": round(precision, 4), "number_recall": round(recall, 4),
        "decode_clean": not any(c.startswith("DECODE_") for c in codes),
        "complete": bool(result["output_complete"]) and bool(text.strip()),
        "input_too_long": result["status"] == "NOT_GENERATED",
        "unsupported_numbers": result["unsupported_numbers"],
        "has_unsupported": bool(result["unsupported_numbers"]),
        "unsupported_copied_from_examples": copied, "has_invented": bool(invented),
        "copy_of_example": copy_of_example,
        "copy_correct": copy_of_example and metrics.exact_match(text, reference),
        "warnings": codes,
        "input_tokens": result["input_tokens"],
        "generated_tokens": result["generated_tokens"],
        "retrieval_status": retrieval.get("status"),
        "k_used": retrieval.get("k_used", 0),
        "context_tokens": retrieval.get("context_tokens", 0),
        "skipped_for_budget": retrieval.get("skipped_for_budget", 0),
        "retrieval_ms": retrieval.get("retrieval_ms"),
        "examples": retrieval.get("examples", []),
        "generation_s": round(generation_s, 3),
    }


def _read_checkpoint(out, label, run_meta):
    """Problem ids already done. Drops a torn (unparsable) LAST line; refuses mismatches."""
    if not out.exists():
        return set()
    lines = out.read_text(encoding="utf-8").splitlines()
    rows = []
    for index, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise SystemExit(f"REFUSED: corrupt line {index + 1} in {out}")
            out.write_text("".join(x + "\n" for x in lines[:-1]), encoding="utf-8")
            print("dropped a torn last line from an interrupted run", flush=True)
    for row in rows:
        if row["config"] != label or row["run"] != run_meta:
            raise SystemExit("REFUSED: --out holds a different configuration or run metadata")
    return {row["problem_id"] for row in rows}


def near_duplicates(args):
    import embedding_model as em
    import problem_embedding_index as pei
    import rag_retrievers as rr
    from build_problem_index import load_inputs

    original, split_of, _ = load_inputs(args.original, args.splits)
    index = pei.load_index(rr.BGE_INDEX_DIR, split_of)
    ids = metrics.validation_queries(split_of, "dev") + metrics.validation_queries(split_of, "heldout")
    metrics.assert_validation_only(ids, split_of)
    texts = [em.problem_text(original[p]["Problem_Description"], original[p]["Input_Specification"],
                             original[p]["Output_Specification"]) for p in ids]
    vectors = em.encode(texts)
    flags = {}
    for pid, vector in zip(ids, vectors):
        top_id, top = index.rank(vector, 1)[0]
        flags[pid] = {"top1_train_id": top_id, "top1_cosine": round(top, 6),
                      "near_duplicate": top >= NEAR_DUPLICATE}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"threshold": NEAR_DUPLICATE, "model": em.MODEL_REPO,
                                          "revision": em.MODEL_REVISION,
                                          "index_vectors_sha256": index.manifest["vectors_sha256"],
                                          "flags": flags}, indent=1), encoding="utf-8")
    print("validation problems:", len(flags), "| near-duplicates:",
          sum(f["near_duplicate"] for f in flags.values()))


def run(args):
    import ai_constraint_service as svc
    import rag_retrievers as rr
    from build_problem_index import ORIGINAL_SHA256, load_inputs, peak_memory_mb

    started_wall = time.perf_counter()
    original, split_of, splits_sha = load_inputs(args.original, args.splits)
    queries = metrics.validation_queries(split_of, args.subset)
    metrics.assert_validation_only(queries, split_of)
    if args.limit:
        queries = queries[:args.limit]
    flags = json.loads(Path(args.near_duplicates).read_text(encoding="utf-8"))["flags"]
    if not set(queries) <= set(flags):
        raise SystemExit("REFUSED: near-duplicate flags missing for some queries")

    svc.MODEL_PATH = str(ROOT / "models" / "flan_t5_constraint_final")
    run_meta = {
        "harness": HARNESS_VERSION, "config": args.config,
        "k_max": None if args.config == "baseline" else args.k_max,
        "example_format": None if args.config == "baseline" else args.format,
        "model": "flan_t5_constraint_final",
        "model_weights_sha256": _sha256_file(FLAN_WEIGHTS),
        "generation": {"num_beams": svc.NUM_BEAMS, "max_new_tokens": svc.MAX_NEW_TOKENS,
                       "input_token_limit": svc.INPUT_TOKEN_LIMIT, "early_stopping": True},
        "original_dataset_sha256": ORIGINAL_SHA256, "split_source_sha256": splits_sha,
        "indexes": {},
    }

    provider = None
    if args.config != "baseline":
        tokenizer, model, _ = svc.load_model()
        retriever, meta, allowed = rr.load_retriever(args.config, generator=(tokenizer, model),
                                                     split_of=split_of)
        for name, folder in (("bge", rr.BGE_INDEX_DIR), ("flan", rr.FLAN_INDEX_DIR),
                             ("tfidf", rr.TFIDF_INDEX_DIR)):
            if args.config in (name, "hybrid"):
                m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                run_meta["indexes"][name] = {"problem_ids_sha256": m["problem_ids_sha256"],
                                             "data_sha256": m.get("vectors_sha256") or m.get("tfidf_sha256")}
        provider = rr.RagContextProvider(retriever, meta, allowed, k_max=args.k_max,
                                         example_format=args.format)

    out = Path(args.out)
    done = _read_checkpoint(out, args.label, run_meta)
    todo = [p for p in queries if p not in done]
    print(f"{args.label} {args.subset}: {len(done)} already done, {len(todo)} to run", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        for n, pid in enumerate(todo, 1):
            r = original[pid]
            p, i, o = r["Problem_Description"], r["Input_Specification"], r["Output_Specification"]
            started = time.perf_counter()
            result = svc.generate_value_constraints(p, i, o, context_provider=provider)
            elapsed = time.perf_counter() - started
            fh.write(json.dumps(_row(pid, args.subset, args.label, run_meta,
                                     r["Value_Constraints"].strip(), result,
                                     flags[pid]["near_duplicate"], elapsed),
                                ensure_ascii=False) + "\n")
            fh.flush()
            if n % 20 == 0 or n == len(todo):
                print(f"{n}/{len(todo)} {pid}", flush=True)

    info = Path(str(out) + ".runinfo.json")
    history = json.loads(info.read_text(encoding="utf-8")) if info.exists() else []
    history.append({"finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "problems_run": len(todo), "wall_seconds": round(time.perf_counter() - started_wall, 1),
                    "peak_memory_mb": peak_memory_mb()})
    info.write_text(json.dumps(history, indent=1), encoding="utf-8")


def _load_rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def compare(args):
    a, b = _load_rows(args.a), _load_rows(args.b)
    report = {"A_config": a[0]["config"] if a else None, "B_config": b[0]["config"] if b else None}
    for name, keep in (("all", lambda r: True), ("no_near_duplicate", lambda r: not r["near_duplicate"])):
        ra, rb = [r for r in a if keep(r)], [r for r in b if keep(r)]
        report[name] = {"A": metrics.summarize(ra), "B": metrics.summarize(rb),
                        "paired_exact_match": metrics.paired_compare(ra, rb)}
    print(json.dumps(report, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    nd = sub.add_parser("near-duplicates")
    nd.add_argument("--original", required=True)
    nd.add_argument("--splits", required=True)
    nd.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--original", required=True)
    r.add_argument("--splits", required=True)
    r.add_argument("--near-duplicates", required=True)
    r.add_argument("--config", required=True, choices=["baseline", "bge", "flan", "tfidf", "hybrid"])
    r.add_argument("--k-max", type=int, default=3)
    r.add_argument("--format", default="encoded", choices=["encoded", "raw"])
    r.add_argument("--subset", required=True, choices=list(metrics.SUBSETS))
    r.add_argument("--limit", type=int)
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = parser.parse_args()
    if args.command == "near-duplicates":
        near_duplicates(args)
    elif args.command == "run":
        args.label = (args.config if args.config == "baseline"
                      else f"{args.config}-k{args.k_max}-{args.format}")
        run(args)
    else:
        compare(args)


if __name__ == "__main__":
    main()
