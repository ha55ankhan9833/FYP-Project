"""
Pure-Python metrics and split guards for the RAG evaluation (no numpy/torch).

* Queries are VALIDATION problems only. The validation split is divided
  deterministically: dev if sha256(problem_id) % 3 == 0, otherwise heldout.
  Choices are made on dev; the final comparison uses heldout.
* The test split is never returned by any function here.
"""

import hashlib
import math
import re
from collections import Counter

SUBSETS = ("dev", "heldout")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")     # notebook cell 93 extract_numbers


class SplitGuardError(ValueError):
    pass


def subset_of(problem_id):
    digest = int(hashlib.sha256(problem_id.encode("utf-8")).hexdigest(), 16)
    return "dev" if digest % 3 == 0 else "heldout"


def validation_queries(split_of, subset):
    """Validation problem ids of one subset, in numeric id order. Never test ids."""
    if subset not in SUBSETS:
        raise SplitGuardError(f"subset must be one of {SUBSETS}")
    ids = [pid for pid, split in split_of.items() if split == "validation"
           and subset_of(pid) == subset]
    return sorted(ids, key=lambda pid: int(pid[1:]))


def assert_validation_only(problem_ids, split_of):
    bad = [pid for pid in problem_ids if split_of.get(pid) != "validation"]
    if bad:
        raise SplitGuardError(f"Refusing non-validation problems (e.g. {bad[:3]}); "
                              "the test split must stay untouched")


def numbers(text):
    return _NUMBER.findall(text)


def exact_match(generated, reference):
    return generated.strip() == reference.strip()


def numeric_match(generated, reference):
    return numbers(generated) == numbers(reference)


def number_precision_recall(generated, reference):
    gen, ref = Counter(numbers(generated)), Counter(numbers(reference))
    overlap = sum((gen & ref).values())
    precision = overlap / sum(gen.values()) if gen else (1.0 if not ref else 0.0)
    recall = overlap / sum(ref.values()) if ref else 1.0
    return precision, recall


def split_unsupported(unsupported, example_constraints):
    """Unsupported numbers copied from a retrieved example vs present in neither."""
    seen = set()
    for text in example_constraints:
        seen.update(re.findall(r"\d+", text))
    copied = sorted(n for n in unsupported if n in seen)
    invented = sorted(n for n in unsupported if n not in seen)
    return copied, invented


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value for discordant counts b and c."""
    if b < 0 or c < 0:
        raise ValueError("counts must be non-negative")
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def summarize(rows):
    n = len(rows)
    if not n:
        return {"problems": 0}

    def rate(key):
        return round(sum(bool(r[key]) for r in rows) / n, 4)

    lat = sorted(r["generation_s"] + (r.get("retrieval_ms") or 0) / 1000 for r in rows)
    return {
        "problems": n,
        "exact_match": rate("exact"),
        "numeric_match": rate("numeric_match"),
        "number_precision": round(sum(r["number_precision"] for r in rows) / n, 4),
        "number_recall": round(sum(r["number_recall"] for r in rows) / n, 4),
        "valid_clean_decode": rate("decode_clean"),
        "valid_complete": rate("complete"),
        "with_unsupported_numbers": rate("has_unsupported"),
        "with_invented_numbers": rate("has_invented"),
        "copy_of_example": rate("copy_of_example"),
        "copy_correct": rate("copy_correct"),
        "copy_incorrect": round(sum(bool(r["copy_of_example"]) and not r["exact"]
                                    for r in rows) / n, 4),
        "unsupported_copied_from_examples": round(
            sum(bool(r.get("unsupported_copied_from_examples")) for r in rows) / n, 4),
        "retrieval_failed_fallback": round(
            sum(r.get("retrieval_status") == "FAILED" for r in rows) / n, 4),
        "no_example_fit": round(
            sum(r.get("retrieval_status") == "NO_EXAMPLE_FITS" for r in rows) / n, 4),
        "output_truncated": round(1 - sum(bool(r["complete"]) for r in rows) / n, 4),
        "input_too_long": rate("input_too_long"),
        "mean_k_used": round(sum(r.get("k_used", 0) for r in rows) / n, 3),
        "examples_skipped_for_budget": sum(r.get("skipped_for_budget", 0) for r in rows),
        "latency_s": {"mean": round(sum(lat) / n, 3), "median": round(lat[n // 2], 3),
                      "p95": round(lat[min(n - 1, int(0.95 * n))], 3), "max": round(lat[-1], 3)},
    }


def paired_compare(rows_a, rows_b):
    """Exact-match comparison on the problems both runs contain."""
    a = {r["problem_id"]: r for r in rows_a}
    b = {r["problem_id"]: r for r in rows_b}
    common = sorted(set(a) & set(b), key=lambda pid: int(pid[1:]))
    only_b = [p for p in common if b[p]["exact"] and not a[p]["exact"]]
    only_a = [p for p in common if a[p]["exact"] and not b[p]["exact"]]
    return {"problems": len(common), "b_wins": len(only_b), "a_wins": len(only_a),
            "mcnemar_p": round(mcnemar_exact(len(only_a), len(only_b)), 6),
            "b_win_ids": only_b, "a_win_ids": only_a}
