"""
Evaluate a test-INPUT generator on VALIDATION problems (never the test split).

    run           baseline (now) or model (after training) on validation_problems.jsonl
    schema-check  coverage and gold-input agreement of the conservative schema
                  inference on a split of the ORIGINAL dataset (train or validation)

Metrics per problem (then averaged):
    schema       supported?  gold inputs 2..10 accepted by the inferred schema
    validity     syntax / constraint / semantic validity of every raw candidate
    diversity    distinct ratio, mean pairwise normalised edit distance, distinct line counts
    boundary     share of scalar variables whose min / max appears in the candidate set
    overlap      candidates identical to one of the problem's gold inputs
    cost         generation latency
Only measured values are written; nothing is extrapolated.

    python dev_tools/evaluate_test_inputs.py run --problems data_prepared/test_input_v1/validation_problems.jsonl \
        --method baseline --subset dev --out dev_tools/reports/test_input_eval_v1/dev_baseline.json
    python dev_tools/evaluate_test_inputs.py schema-check --original <json> --split train
"""

import argparse
import itertools
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baseline_input_generator import generate_candidates  # noqa: E402
from input_schema import validate_input  # noqa: E402
from input_schema_inference import infer_schema  # noqa: E402
from test_input_codec import canonical_form, with_trailing_newline  # noqa: E402

SPLITS_PATH = ROOT / "tests" / "data" / "split_ids.json"


def load_split_of():
    return json.loads(SPLITS_PATH.read_text(encoding="utf-8"))["splits"]


def edit_distance(a, b, cap=400):
    a, b = a[:cap], b[:cap]
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1] / max(1, len(a), len(b))


def diversity(texts):
    if len(texts) < 2:
        return {"distinct_ratio": 1.0 if texts else 0.0, "mean_pairwise_distance": 0.0,
                "distinct_line_counts": len({t.count("\n") for t in texts})}
    pairs = list(itertools.combinations(texts[:20], 2))
    return {"distinct_ratio": len(set(texts)) / len(texts),
            "mean_pairwise_distance": round(sum(edit_distance(a, b) for a, b in pairs) / len(pairs), 4),
            "distinct_line_counts": len({t.count("\n") for t in texts})}


def boundary(schema, valid_texts):
    hits = {"min": 0, "max": 0, "variables": 0}
    observed = {}
    for text in valid_texts:
        values = validate_input(schema, text)["values"] or {}
        for name, value in values.items():
            if name in schema["variables"] and isinstance(value, int):
                observed.setdefault(name, set()).add(value)
    for name, spec in schema["variables"].items():
        hits["variables"] += 1
        seen = observed.get(name, set())
        hits["min"] += spec["min"] in seen
        hits["max"] += spec["max"] in seen
    return hits


def candidates_for(method, row, count, seed):
    if method == "baseline":
        result = generate_candidates(row["_schema"], row["sample_input"], count=count, seed=seed)
        return [c["input"] for c in result["candidates"]], result["rejected"]
    import ai_test_input_service as ai
    raw = ai.generate_raw_candidates(row, count)
    return [t for t in raw["texts"] if t is not None], [None] * sum(t is None for t in raw["texts"])


def run(args):
    split_of = load_split_of()
    rows = [json.loads(line) for line in Path(args.problems).read_text(encoding="utf-8").splitlines()]
    bad = [r["problem_id"] for r in rows if split_of.get(r["problem_id"]) != "validation"]
    if bad:
        raise SystemExit(f"REFUSED: non-validation problems {bad[:3]} (test split stays untouched)")
    if args.subset != "all":
        rows = [r for r in rows if r["subset"] == args.subset]
    if args.limit:
        rows = rows[:args.limit]

    per_problem = []
    for row in rows:
        inferred = infer_schema(row)
        item = {"problem_id": row["problem_id"], "near_duplicate": row.get("near_duplicate"),
                "supported": inferred["supported"], "shape": inferred["shape"]}
        if not inferred["supported"]:
            item["reason"] = inferred["reasons"][0][:120]
            per_problem.append(item)
            continue
        schema = inferred["schema"]
        row["_schema"] = schema
        gold = [with_trailing_newline(g) for g in row["gold_inputs"][1:]]
        item["gold_accepted"] = sum(validate_input(schema, g)["valid"] for g in gold)
        item["gold_total"] = len(gold)
        start = time.perf_counter()
        texts, rejected = candidates_for(args.method, row, args.count, args.seed)
        item["latency_s"] = round(time.perf_counter() - start, 4)
        checks = [validate_input(schema, t) for t in texts]
        item["candidates"] = len(texts)
        item["generation_rejections"] = len(rejected)
        item["syntax_valid"] = sum(c["syntax_valid"] for c in checks)
        item["constraints_valid"] = sum(bool(c["constraints_valid"]) for c in checks)
        item["semantic_valid"] = sum(bool(c["semantic_valid"]) for c in checks)
        item["valid"] = sum(c["valid"] for c in checks)
        valid_texts = [t for t, c in zip(texts, checks) if c["valid"]]
        item["diversity"] = diversity(valid_texts)
        item["boundary"] = boundary(schema, valid_texts)
        gold_keys = {canonical_form(g) for g in row["gold_inputs"]}
        item["identical_to_gold"] = sum(canonical_form(t) in gold_keys for t in valid_texts)
        per_problem.append(item)

    report = {"method": args.method, "subset": args.subset, "count": args.count, "seed": args.seed,
              "problems": len(per_problem), "summary": summarize(per_problem),
              "summary_no_near_duplicate": summarize([p for p in per_problem if not p.get("near_duplicate")]),
              "per_problem": per_problem}
    if args.out:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"REFUSED: {out} exists")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("method", "subset", "problems", "summary",
                                             "summary_no_near_duplicate")}, indent=1))


def summarize(per_problem):
    supported = [p for p in per_problem if p["supported"]]
    if not per_problem:
        return {}
    out = {"problems": len(per_problem), "supported": len(supported),
           "supported_rate": round(len(supported) / len(per_problem), 4)}
    if not supported:
        return out
    shapes = {}
    for p in supported:
        shapes[p["shape"]] = shapes.get(p["shape"], 0) + 1
    total = sum(p["candidates"] for p in supported) or 1
    latencies = sorted(p["latency_s"] for p in supported)
    variables = sum(p["boundary"]["variables"] for p in supported) or 1
    out.update({
        "shapes": shapes,
        "gold_inputs_accepted_by_schema": round(sum(p["gold_accepted"] for p in supported)
                                                / max(1, sum(p["gold_total"] for p in supported)), 4),
        "problems_all_gold_accepted": sum(p["gold_accepted"] == p["gold_total"] for p in supported),
        "mean_candidates": round(total / len(supported), 2),
        "candidate_syntax_valid": round(sum(p["syntax_valid"] for p in supported) / total, 4),
        "candidate_constraints_valid": round(sum(p["constraints_valid"] for p in supported) / total, 4),
        "candidate_semantic_valid": round(sum(p["semantic_valid"] for p in supported) / total, 4),
        "candidate_valid": round(sum(p["valid"] for p in supported) / total, 4),
        "mean_distinct_ratio": round(statistics.mean(p["diversity"]["distinct_ratio"] for p in supported), 4),
        "mean_pairwise_distance": round(statistics.mean(p["diversity"]["mean_pairwise_distance"] for p in supported), 4),
        "scalar_min_coverage": round(sum(p["boundary"]["min"] for p in supported) / variables, 4),
        "scalar_max_coverage": round(sum(p["boundary"]["max"] for p in supported) / variables, 4),
        "candidates_identical_to_gold": round(sum(p["identical_to_gold"] for p in supported) / total, 4),
        "latency_s": {"mean": round(statistics.mean(latencies), 4), "max": latencies[-1]},
    })
    return out


def schema_check(args):
    split_of = load_split_of()
    if args.split == "test":
        raise SystemExit("REFUSED: the test split stays untouched")
    problems = json.loads(Path(args.original).read_text(encoding="utf-8"))
    supported = accepted = total = full = 0
    shapes = {}
    selected = 0
    for p in problems:
        if split_of.get(p["Problem_ID"]) != args.split:
            continue
        selected += 1
        row = {"problem_statement": p["Problem_Description"], "input_format": p["Input_Specification"],
               "output_format": p["Output_Specification"], "value_constraints": p["Value_Constraints"],
               "sample_input": p["Sample_Input"]}
        inferred = infer_schema(row)
        if not inferred["supported"]:
            continue
        supported += 1
        shapes[inferred["shape"]] = shapes.get(inferred["shape"], 0) + 1
        gold = [c["input"] for c in json.loads(p["Test_Cases"])[1:]]
        ok = sum(validate_input(inferred["schema"], g)["valid"] for g in gold)
        accepted += ok
        total += len(gold)
        full += ok == len(gold)
    print(json.dumps({"split": args.split, "problems": selected, "supported": supported,
                      "supported_rate": round(supported / max(1, selected), 4), "shapes": shapes,
                      "gold_inputs_accepted": accepted, "gold_inputs_checked": total,
                      "gold_acceptance_rate": round(accepted / max(1, total), 4),
                      "problems_all_gold_accepted": full}, indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validation-only test-input generator evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--problems", required=True)
    r.add_argument("--method", choices=["baseline", "model"], default="baseline")
    r.add_argument("--subset", choices=["dev", "heldout", "all"], default="dev")
    r.add_argument("--count", type=int, default=10)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--limit", type=int)
    r.add_argument("--out")
    s = sub.add_parser("schema-check")
    s.add_argument("--original", required=True)
    s.add_argument("--split", choices=["train", "validation"], default="train")
    args = parser.parse_args(argv)
    (run if args.command == "run" else schema_check)(args)


if __name__ == "__main__":
    main()
