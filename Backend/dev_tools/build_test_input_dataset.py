"""
Phase A: build the test-INPUT generation dataset from the verified original
7,411-problem dataset (read-only; never modified or overwritten).

Split: the existing problem-level split in tests/data/split_ids.json
(train 5,636 / validation 716 / test 709). The 350 problems without a split
stay excluded. Test problems are NEVER written anywhere.

Outputs (refuses to overwrite; folder is git-ignored):
    train.jsonl                one row per (training problem, case 2..10)
    validation_dev.jsonl       same rows for validation problems, dev subset
    validation_heldout.jsonl   same rows for validation problems, heldout subset
    validation_problems.jsonl  one row per validation problem (prompt + all 10
                               gold inputs) for candidate-level evaluation
    manifest.json              hashes, counts, drop reasons, length statistics

Prompt  = build_generation_prompt(description, input spec, output spec,
          constraints, sample input)       (test_input_codec, shared with inference)
Targets = light-encoded canonical inputs of cases 2..10 (case 1 == sample).

Usage (.venv_ai for token statistics; .venv works with --no-tokenizer):
    python dev_tools/build_test_input_dataset.py --original <json> [--out DIR]
"""

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag_eval_metrics import subset_of  # noqa: E402
from test_input_codec import (  # noqa: E402
    CodecError,
    build_generation_prompt,
    canonical_form,
    decode_input,
    encode_input,
    with_trailing_newline,
)

ORIGINAL_SHA256 = "b77745760e7ff750ff204c8093532035dca40326b291bc565a8c3e0a0a4f6b09"
SPLITS_PATH = ROOT / "tests" / "data" / "split_ids.json"
EXPECTED_SPLITS = {"train": 5636, "validation": 716, "test": 709}
EXPECTED_PROBLEMS = 7411
CASES_PER_PROBLEM = 10
PROMPT_TOKEN_LIMIT = 512
TARGET_TOKEN_LIMIT = 256
DEFAULT_OUT = ROOT / "data_prepared" / "test_input_v1"
DEFAULT_TOKENIZER = ROOT / "models" / "flan_t5_constraint_final"
NEAR_DUPLICATES = ROOT / "dev_tools" / "reports" / "rag_eval_v1" / "near_duplicates.json"


class BuildError(SystemExit):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_sources(original_path, splits_path=SPLITS_PATH, expected_sha=ORIGINAL_SHA256):
    raw = Path(original_path).read_bytes()
    if expected_sha and sha256_bytes(raw) != expected_sha:
        raise BuildError("REFUSED: original dataset hash mismatch")
    problems = json.loads(raw.decode("utf-8"))
    splits_raw = Path(splits_path).read_bytes()
    split_of = json.loads(splits_raw.decode("utf-8"))["splits"]
    return problems, split_of, sha256_bytes(raw), sha256_bytes(splits_raw)


def check_split(problems, split_of, expected=EXPECTED_SPLITS, expected_problems=EXPECTED_PROBLEMS):
    ids = [p["Problem_ID"] for p in problems]
    if len(ids) != len(set(ids)):
        raise BuildError("REFUSED: duplicate Problem_ID in original dataset")
    if expected_problems and len(ids) != expected_problems:
        raise BuildError(f"REFUSED: expected {expected_problems} problems, got {len(ids)}")
    unknown = set(split_of) - set(ids)
    if unknown:
        raise BuildError(f"REFUSED: split file names unknown problems {sorted(unknown)[:3]}")
    counts = Counter(split_of.values())
    if expected and dict(counts) != expected:
        raise BuildError(f"REFUSED: split counts {dict(counts)} != {expected}")
    return counts


def prepare_problem(problem):
    """(sample_canonical, [(case_index, canonical_input)], drops Counter) for one problem."""
    drops = Counter()
    cases = json.loads(problem["Test_Cases"])
    if len(cases) != CASES_PER_PROBLEM:
        raise BuildError(f"REFUSED: {problem['Problem_ID']} has {len(cases)} cases")
    if cases[0]["input"] != problem["Sample_Input"]:
        raise BuildError(f"REFUSED: {problem['Problem_ID']} case 1 is not the sample input")
    sample = with_trailing_newline(problem["Sample_Input"])
    sample_ok = canonical_form(sample) == sample
    targets = []
    for index, case in enumerate(cases[1:], start=2):
        text = with_trailing_newline(case["input"])
        if canonical_form(text) != text:
            drops["target_not_canonical"] += 1
            continue
        targets.append((index, text))
    return (sample if sample_ok else None), targets, drops


def build(original_path, out_dir=DEFAULT_OUT, tokenizer=None, splits_path=SPLITS_PATH,
          expected_sha=ORIGINAL_SHA256, expected_splits=EXPECTED_SPLITS,
          expected_problems=EXPECTED_PROBLEMS, near_duplicates_path=NEAR_DUPLICATES):
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise BuildError(f"REFUSED: {out_dir} already exists (outputs are never overwritten)")

    problems, split_of, original_sha, splits_sha = load_sources(
        original_path, splits_path, expected_sha)
    split_counts = check_split(problems, split_of, expected_splits, expected_problems)

    near_dup = {}
    if near_duplicates_path and Path(near_duplicates_path).exists():
        flags = json.loads(Path(near_duplicates_path).read_text(encoding="utf-8"))
        near_dup = flags.get("flags", flags) if isinstance(flags, dict) else {}

    def count_tokens(text):
        return len(tokenizer(text, truncation=False)["input_ids"]) if tokenizer else None

    files = {"train": [], "validation_dev": [], "validation_heldout": []}
    problem_rows = []
    drops = Counter()
    prompt_tokens, target_tokens = {k: [] for k in files}, {k: [] for k in files}
    problems_written = Counter()

    for problem in problems:
        pid = problem["Problem_ID"]
        split = split_of.get(pid)
        if split is None:
            drops["problem_excluded_no_split"] += 1
            continue
        if split == "test":
            drops["problem_test_split_untouched"] += 1
            continue
        key = "train" if split == "train" else f"validation_{subset_of(pid)}"

        sample, targets, problem_drops = prepare_problem(problem)
        drops.update(problem_drops)
        if sample is None:
            drops["problem_sample_not_canonical"] += 1
            continue
        try:
            prompt = build_generation_prompt(
                problem["Problem_Description"], problem["Input_Specification"],
                problem["Output_Specification"], problem["Value_Constraints"], sample)
        except CodecError:
            drops["problem_prompt_codec_error"] += 1
            continue
        p_tokens = count_tokens(prompt)
        if p_tokens is not None and p_tokens > PROMPT_TOKEN_LIMIT:
            drops["problem_prompt_over_512_tokens"] += 1
            continue

        rows = []
        for index, text in targets:
            try:
                encoded = encode_input(text)
            except CodecError:
                drops["target_codec_error"] += 1
                continue
            if decode_input(encoded) != text:
                drops["target_round_trip_failed"] += 1
                continue
            t_tokens = count_tokens(encoded)
            if t_tokens is not None:
                if t_tokens > TARGET_TOKEN_LIMIT:
                    drops["target_over_256_tokens"] += 1
                    continue
                if tokenizer.decode(tokenizer(encoded)["input_ids"], skip_special_tokens=True,
                                    clean_up_tokenization_spaces=False) != encoded:
                    drops["target_tokenizer_round_trip_failed"] += 1
                    continue
                target_tokens[key].append(t_tokens)
            rows.append({"problem_id": pid, "split": split,
                         "subset": None if split == "train" else subset_of(pid),
                         "case_index": index, "prompt": prompt, "target": encoded})
        if not rows:
            drops["problem_no_usable_target"] += 1
            continue
        if p_tokens is not None:
            prompt_tokens[key].append(p_tokens)
        files[key].extend(rows)
        problems_written[key] += 1
        if split == "validation":
            gold = [with_trailing_newline(c["input"]) for c in json.loads(problem["Test_Cases"])]
            problem_rows.append({
                "problem_id": pid, "subset": subset_of(pid),
                "near_duplicate": (near_dup.get(pid) or {}).get("near_duplicate"),
                "prompt": prompt,
                "problem_statement": problem["Problem_Description"],
                "input_format": problem["Input_Specification"],
                "output_format": problem["Output_Specification"],
                "value_constraints": problem["Value_Constraints"],
                "sample_input": sample, "gold_inputs": gold})

    leakage = check_leakage(files, problem_rows, split_of)

    out_dir.mkdir(parents=True)
    file_hashes = {}
    for name, rows in list(files.items()) + [("validation_problems", problem_rows)]:
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
        path = out_dir / f"{name}.jsonl"
        path.write_bytes(data)
        file_hashes[path.name] = {"sha256": sha256_bytes(data), "rows": len(rows)}

    def stats(values):
        if not values:
            return None
        values = sorted(values)
        return {"count": len(values), "median": statistics.median(values),
                "p95": values[min(len(values) - 1, int(0.95 * len(values)))],
                "max": values[-1]}

    manifest = {
        "dataset": "test_input_v1",
        "source": {"original_sha256": original_sha, "splits_file": "tests/data/split_ids.json",
                   "splits_sha256": splits_sha, "split_counts": dict(split_counts)},
        "prompt_format": "test_input_codec.build_generation_prompt",
        "target_format": "test_input_codec.encode_input (cases 2..10; case 1 is the sample)",
        "limits": {"prompt_tokens": PROMPT_TOKEN_LIMIT, "target_tokens": TARGET_TOKEN_LIMIT,
                   "tokenizer": getattr(tokenizer, "name_or_path", None) and "flan-t5 (local)"},
        "problems_written": dict(problems_written),
        "drops": dict(sorted(drops.items())),
        "files": file_hashes,
        "leakage_checks": leakage,
        "length_stats": {k: {"prompt_tokens": stats(prompt_tokens[k]),
                             "target_tokens": stats(target_tokens[k])} for k in files},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def check_leakage(files, problem_rows, split_of):
    """Problem-level separation; raises on any violation."""
    ids = {name: {r["problem_id"] for r in rows} for name, rows in files.items()}
    names = list(ids)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if ids[a] & ids[b]:
                raise BuildError(f"REFUSED: problems shared by {a} and {b}")
    for name, pids in ids.items():
        wanted = "train" if name == "train" else "validation"
        bad = [p for p in pids if split_of.get(p) != wanted]
        if bad:
            raise BuildError(f"REFUSED: {name} contains non-{wanted} problems {bad[:3]}")
    all_written = set().union(*ids.values()) | {r["problem_id"] for r in problem_rows}
    test_written = [p for p in all_written if split_of.get(p) == "test"]
    if test_written:
        raise BuildError("REFUSED: test-split problem would be written")
    train_targets = {r["target"] for r in files["train"]}
    val_targets = [r["target"] for k in ("validation_dev", "validation_heldout") for r in files[k]]
    shared_targets = sum(t in train_targets for t in val_targets)
    return {"problem_sets_disjoint": True, "train_only_train_split": True,
            "validation_only_validation_split": True, "test_problems_written": 0,
            "validation_targets_identical_to_a_train_target": shared_targets,
            "validation_targets": len(val_targets)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--original", required=True)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--tokenizer", default=str(DEFAULT_TOKENIZER))
    parser.add_argument("--no-tokenizer", action="store_true",
                        help="skip token-length limits/statistics (not for training data)")
    args = parser.parse_args(argv)
    tokenizer = None
    if not args.no_tokenizer:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    manifest = build(args.original, args.out, tokenizer)
    print(json.dumps({k: manifest[k] for k in ("problems_written", "drops", "files",
                                               "leakage_checks", "length_stats")}, indent=1))


if __name__ == "__main__":
    main()
