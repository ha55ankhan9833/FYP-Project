"""
RAG context selection for the FLAN-T5 value-constraint generator (pure Python).

Rules (docs/AI_RAG_CONSTRAINT_PLAN.md §3):
  * Retrieved examples come from the TRAINING split only; every candidate is
    checked against an explicit allow-list of training problem ids.
  * An example contains ONLY the neighbour's input format and value
    constraints - never its description, solution, sample I/O or tests.
  * The prompt is measured with the generator's own tokenizer. Whole examples
    are added in rank order only while the full prompt stays within the
    512-token limit; an example that does not fit is skipped, never cut, and
    the new problem itself is never cut. If the problem alone does not fit,
    no RAG is attempted (D4 refusal happens before retrieval).
  * With no examples the prompt is byte-identical to the baseline prompt.
"""

from ai_constraint_service import build_prompt
from constraint_codec import INPUT_TOKEN_LIMIT, encode_constraint

INSTRUCTION_LINE = ("Generate value constraints for the following C++ "
                    "programming problem.\n")
CONTEXT_HEADER = "Similar solved problems:\n"
EXAMPLE_FIELDS = frozenset({"input_format", "value_constraints"})
EXAMPLE_FORMATS = ("encoded", "raw")
DEFAULT_K_MAX = 3
MAX_K_MAX = 10
RRF_K = 60


class ContextError(ValueError):
    pass


def format_example(example, example_format="encoded"):
    """
    One context example from whitelisted fields only. "encoded" writes the
    constraints in the placeholder form the model learned to OUTPUT (e.g.
    [B3C]= for <=); "raw" writes them as plain text.
    """
    if example_format not in EXAMPLE_FORMATS:
        raise ContextError("Unknown example format")
    fields = {k: v for k, v in example.items() if k in EXAMPLE_FIELDS}
    if set(fields) != EXAMPLE_FIELDS or not all(isinstance(v, str) for v in fields.values()):
        raise ContextError("An example needs input_format and value_constraints strings")
    constraints = fields["value_constraints"].strip()
    if example_format == "encoded":
        constraints = encode_constraint(constraints)
    return (f"Input Format: {fields['input_format'].strip()}\n"
            f"Value Constraints: {constraints}\n")


def build_rag_prompt(problem, input_format, output_format, example_texts=()):
    baseline = build_prompt(problem, input_format, output_format)
    if not example_texts:
        return baseline
    if not baseline.startswith(INSTRUCTION_LINE):
        raise ContextError("Baseline prompt layout changed")
    return (INSTRUCTION_LINE + CONTEXT_HEADER + "".join(example_texts)
            + baseline[len(INSTRUCTION_LINE):])


def select_context(problem, input_format, output_format, candidates, count_tokens,
                   allowed_ids, k_max=DEFAULT_K_MAX, example_format="encoded",
                   limit=INPUT_TOKEN_LIMIT, exclude_ids=()):
    """
    candidates: ranked [{"problem_id", "score", "input_format", "value_constraints"}].
    count_tokens(text) -> int: full prompt length with the generator tokenizer.
    Returns {prompt, prompt_tokens, base_tokens, k_used, context_tokens,
             skipped_for_budget, skipped_duplicates, examples}.
    """
    if type(k_max) is not int or not 0 <= k_max <= MAX_K_MAX:
        raise ContextError(f"k_max must be an integer between 0 and {MAX_K_MAX}")

    baseline = build_prompt(problem, input_format, output_format)
    base_tokens = count_tokens(baseline)
    if base_tokens > limit:
        raise ContextError("Problem prompt exceeds the token limit; RAG not attempted")

    allowed = allowed_ids if isinstance(allowed_ids, (set, frozenset)) else set(allowed_ids)
    excluded = set(exclude_ids)
    chosen, texts, seen_content, example_constraints = [], [], set(), []
    skipped_budget = skipped_duplicates = 0
    prompt, prompt_tokens = baseline, base_tokens

    for candidate in candidates:
        if len(chosen) >= k_max:
            break
        pid = candidate.get("problem_id")
        if pid not in allowed:
            raise ContextError(f"Retrieved problem {pid!r} is not a training-split problem")
        if pid in excluded:
            continue
        text = format_example(candidate, example_format)
        if text in seen_content:                 # identical templated example
            skipped_duplicates += 1
            continue
        trial = build_rag_prompt(problem, input_format, output_format, texts + [text])
        trial_tokens = count_tokens(trial)
        if trial_tokens > limit:
            skipped_budget += 1                  # skipped whole, never cut
            continue
        seen_content.add(text)
        texts.append(text)
        chosen.append({"problem_id": pid, "score": round(float(candidate.get("score", 0.0)), 6)})
        example_constraints.append(candidate["value_constraints"].strip())
        prompt, prompt_tokens = trial, trial_tokens

    if prompt_tokens > limit:                    # defensive: cannot happen
        raise ContextError("Prompt exceeds the token limit")
    return {
        "prompt": prompt,
        "prompt_tokens": prompt_tokens,
        "base_tokens": base_tokens,
        "k_used": len(chosen),
        "context_tokens": prompt_tokens - base_tokens,
        "skipped_for_budget": skipped_budget,
        "skipped_duplicates": skipped_duplicates,
        "examples": chosen,
        # plain-text constraints of the used examples (evaluation: copy checks)
        "example_constraints": example_constraints,
    }


def reciprocal_rank_fusion(rankings, n=None, k=RRF_K):
    """
    rankings: list of ranked problem-id lists (best first).
    Score = sum over lists of 1 / (k + rank). Ties: best single rank, then id.
    Returns [(problem_id, score)].
    """
    scores, best = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, pid in enumerate(ranking, start=1):
            if pid in seen:                      # a list may not vote twice
                continue
            seen.add(pid)
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
            best[pid] = min(best.get(pid, rank), rank)
    ordered = sorted(scores, key=lambda pid: (-scores[pid], best[pid], pid))
    if n is not None:
        ordered = ordered[:n]
    return [(pid, scores[pid]) for pid in ordered]
