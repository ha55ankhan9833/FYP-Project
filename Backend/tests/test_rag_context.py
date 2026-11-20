"""
RAG phase 1 (pure Python, runs in .venv): baseline compatibility with RAG off,
512-token budget handling, leakage/field whitelist, RRF, fallback, evaluation guards.
No model, numpy or torch: tokenizer, model and torch are faked.
"""

import contextlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_constraint_service as svc
import rag_context as rc
import rag_eval_metrics as metrics
import teacher_approval_service as drafts

SPLITS = json.loads((Path(__file__).parent / "data" / "split_ids.json")
                    .read_text(encoding="utf-8"))["splits"]
EOS = 1
FAKE_TORCH = types.SimpleNamespace(no_grad=contextlib.nullcontext)


def words(text):
    """Deterministic stand-in for the FLAN-T5 tokenizer: one token per word, +1 for </s>."""
    return len(text.split()) + 1


class WordTokenizer:
    eos_token_id = EOS

    def __init__(self, raw_output="1[SP][B3C]=[SP]N[SP][B3C]=[SP]10"):
        self.raw_output = raw_output
        self.prompts_sent_to_model = []

    def __call__(self, text, truncation=None, return_tensors=None, **kwargs):
        if return_tensors:
            self.prompts_sent_to_model.append(text)
        return _Enc(input_ids=[7] * words(text))

    def decode(self, sequence, skip_special_tokens=False, clean_up_tokenization_spaces=True):
        return self.raw_output


class _Enc(dict):
    def to(self, _device):
        return self


class _Seq(list):
    def tolist(self):
        return list(self)


class FakeModel:
    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        return [_Seq([0, 5, 6, EOS])]


def example(pid, inp="N integers", cons="1 <= N <= 10", **extra):
    return {"problem_id": pid, "score": 0.9, "input_format": inp, "value_constraints": cons, **extra}


PROBLEM = ("Read N and print it.", "One integer N.", "Print N.")
TRAIN = sorted(p for p, s in SPLITS.items() if s == "train")[:20]


def run(provider=None, rag_mode="off", tokenizer=None, live=None):
    tokenizer = tokenizer or WordTokenizer()
    model = FakeModel()
    patches = [patch.object(svc, "load_model", return_value=(tokenizer, model, "cpu")),
               patch.object(svc, "RAG_MODE", rag_mode),
               patch.dict(sys.modules, {"torch": FAKE_TORCH})]
    if live is not None:
        patches.append(patch.object(svc, "_rag_provider", live))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        if provider is not None:
            result = svc.generate_value_constraints(*PROBLEM, context_provider=provider)
        else:
            result = svc.analyze_cpp_problem(*PROBLEM)
    return result, tokenizer, model


# ---------------------------------------------------------------- RAG off = unchanged

class RagOffCompatibilityTests(unittest.TestCase):

    def test_flag_is_off_by_default(self):
        self.assertEqual(svc.RAG_MODE, "off")

    def test_no_examples_gives_the_baseline_prompt_byte_for_byte(self):
        self.assertEqual(rc.build_rag_prompt(*PROBLEM), svc.build_prompt(*PROBLEM))
        sel = rc.select_context(*PROBLEM, [example(TRAIN[0])], words, set(TRAIN), k_max=0)
        self.assertEqual(sel["prompt"], svc.build_prompt(*PROBLEM))
        self.assertEqual(sel["k_used"], 0)

    def test_rag_off_sends_the_original_prompt_and_response_shape(self):
        result, tokenizer, model = run()
        self.assertEqual(tokenizer.prompts_sent_to_model, [svc.build_prompt(*PROBLEM)])
        self.assertEqual(model.calls, 1)
        self.assertEqual(set(result), {"value_constraints", "code_constraints",
                                       "teacher_approval_required", "warnings", "model"})
        self.assertNotIn("retrieval", result["model"])
        self.assertEqual(set(result["value_constraints"][0]),
                         {"text", "status", "unsupported_numbers", "warnings"})

    def test_generate_without_provider_has_no_retrieval_keys(self):
        tokenizer, model = WordTokenizer(), FakeModel()
        with patch.object(svc, "load_model", return_value=(tokenizer, model, "cpu")), \
                patch.dict(sys.modules, {"torch": FAKE_TORCH}):
            result = svc.generate_value_constraints(*PROBLEM)
        self.assertNotIn("retrieval", result)
        self.assertNotIn("retrieval_warnings", result)


# ---------------------------------------------------------------- 512-token budget

class BudgetTests(unittest.TestCase):

    def test_examples_added_whole_while_within_limit(self):
        base = words(svc.build_prompt(*PROBLEM))
        small = example(TRAIN[0], "N", "1 <= N <= 5")
        big = example(TRAIN[1], "N " * 40, "1 <= N <= 7")
        small2 = example(TRAIN[2], "M", "1 <= M <= 9")
        limit = base + 30
        sel = rc.select_context(*PROBLEM, [small, big, small2], words, set(TRAIN),
                                k_max=3, limit=limit)
        self.assertLessEqual(sel["prompt_tokens"], limit)
        self.assertEqual(words(sel["prompt"]), sel["prompt_tokens"])
        self.assertEqual([e["problem_id"] for e in sel["examples"]], [TRAIN[0], TRAIN[2]])
        self.assertEqual(sel["skipped_for_budget"], 1)
        self.assertIn(rc.format_example(small), sel["prompt"])          # whole example
        self.assertNotIn("N N N", sel["prompt"])                        # skipped, not cut
        self.assertTrue(sel["prompt"].endswith(svc.build_prompt(*PROBLEM)[len(rc.INSTRUCTION_LINE):]))

    def test_problem_at_limit_and_one_over(self):
        base = words(svc.build_prompt(*PROBLEM))
        at = rc.select_context(*PROBLEM, [example(TRAIN[0])], words, set(TRAIN), limit=base)
        self.assertEqual((at["k_used"], at["prompt"]), (0, svc.build_prompt(*PROBLEM)))
        self.assertEqual(at["skipped_for_budget"], 1)
        with self.assertRaises(rc.ContextError):
            rc.select_context(*PROBLEM, [example(TRAIN[0])], words, set(TRAIN), limit=base - 1)

    def test_k_max_and_rank_order(self):
        cands = [example(TRAIN[i], f"F{i}", f"1 <= X{i} <= {i + 2}") for i in range(6)]
        sel = rc.select_context(*PROBLEM, cands, words, set(TRAIN), k_max=2)
        self.assertEqual([e["problem_id"] for e in sel["examples"]], TRAIN[:2])
        with self.assertRaises(rc.ContextError):
            rc.select_context(*PROBLEM, cands, words, set(TRAIN), k_max=11)

    def test_d4_refusal_happens_before_retrieval(self):
        calls = []

        def provider(*args):
            calls.append(args)
            raise AssertionError("must not be called")

        with patch.object(svc, "load_model",
                          return_value=(_FixedTokenizer(513), FakeModel(), "cpu")):
            result = svc.generate_value_constraints(*PROBLEM, context_provider=provider)
        self.assertEqual(result["status"], "NOT_GENERATED")
        self.assertEqual(calls, [])

    def test_service_rejects_a_selection_over_the_limit(self):
        def provider(problem, inp, out, count):
            return {"prompt": "x " * 600, "prompt_tokens": 601, "k_used": 1, "context_tokens": 400,
                    "skipped_for_budget": 0, "skipped_duplicates": 0, "examples": [],
                    "example_constraints": []}
        result, tokenizer, _ = run(provider=provider)
        self.assertEqual(tokenizer.prompts_sent_to_model, [svc.build_prompt(*PROBLEM)])
        self.assertEqual(result["retrieval"]["status"], "FAILED")
        self.assertEqual([w["code"] for w in result["retrieval_warnings"]], ["RETRIEVAL_UNAVAILABLE"])


class _FixedTokenizer(WordTokenizer):
    def __init__(self, n):
        super().__init__()
        self.n = n

    def __call__(self, text, truncation=None, return_tensors=None, **kwargs):
        return _Enc(input_ids=[7] * self.n)


# ---------------------------------------------------------------- leakage and fields

class LeakageAndFieldTests(unittest.TestCase):

    def test_non_training_candidate_refused(self):
        val_id = next(p for p, s in SPLITS.items() if s == "validation")
        test_id = next(p for p, s in SPLITS.items() if s == "test")
        for leaked in (val_id, test_id, "P9999"):
            with self.subTest(leaked=leaked), self.assertRaises(rc.ContextError):
                rc.select_context(*PROBLEM, [example(leaked)], words, set(TRAIN))

    def test_only_input_format_and_constraints_are_used(self):
        cand = example(TRAIN[0], CPP_Solution="int main(){SECRET_CODE}",
                       Test_Cases='[{"input":"SECRET_INPUT"}]', Sample_Input="SECRET_SAMPLE",
                       Sample_Output="SECRET_OUT", problem_statement="SECRET_DESCRIPTION",
                       expected_output="SECRET_EXPECTED")
        sel = rc.select_context(*PROBLEM, [cand], words, set(TRAIN))
        self.assertEqual(sel["k_used"], 1)
        self.assertNotIn("SECRET", sel["prompt"])
        with self.assertRaises(rc.ContextError):
            rc.format_example({"problem_id": TRAIN[0], "input_format": "N"})

    def test_exclusion_and_duplicate_content(self):
        cands = [example(TRAIN[0]), example(TRAIN[1]), example(TRAIN[2], "M", "1 <= M <= 3")]
        sel = rc.select_context(*PROBLEM, cands, words, set(TRAIN), exclude_ids={TRAIN[2]})
        self.assertEqual([e["problem_id"] for e in sel["examples"]], [TRAIN[0]])
        self.assertEqual(sel["skipped_duplicates"], 1)

    def test_encoded_and_raw_example_formats(self):
        enc = rc.format_example(example(TRAIN[0], cons="1 <= N <= 10^9"), "encoded")
        raw = rc.format_example(example(TRAIN[0], cons="1 <= N <= 10^9"), "raw")
        self.assertIn("1[SP][B3C]=[SP]N[SP][B3C]=[SP]10[B5E]9", enc)
        self.assertIn("1 <= N <= 10^9", raw)
        with self.assertRaises(rc.ContextError):
            rc.format_example(example(TRAIN[0]), "html")


class FusionTests(unittest.TestCase):

    def test_rrf_order_ties_and_repeats(self):
        fused = rc.reciprocal_rank_fusion([["A", "B", "C"], ["B", "A", "D"], ["B", "B", "E"]])
        self.assertEqual([p for p, _ in fused][:2], ["B", "A"])
        self.assertAlmostEqual(dict(fused)["B"], 1 / 62 + 1 / 61 + 1 / 61)
        tie = rc.reciprocal_rank_fusion([["X", "Y"], ["Y", "X"]])
        self.assertEqual([p for p, _ in tie], ["X", "Y"])          # equal score, equal best rank -> id
        self.assertEqual(len(rc.reciprocal_rank_fusion([["A", "B", "C"]], n=2)), 2)


# ---------------------------------------------------------------- RAG on: use and fallback

class RagOnTests(unittest.TestCase):

    def _provider(self):
        def provider(problem, inp, out, count):
            sel = rc.select_context(problem, inp, out, [example(TRAIN[0], cons="1 <= N <= 10")],
                                    count, set(TRAIN))
            sel.update(method="bge", retrieval_ms=1.0)
            return sel
        return provider

    def test_context_reaches_the_model_and_is_reported(self):
        provider = self._provider()
        result, tokenizer, _ = run(rag_mode="bge", live=lambda: (provider, None))
        self.assertIn(rc.CONTEXT_HEADER, tokenizer.prompts_sent_to_model[0])
        retrieval = result["model"]["retrieval"]
        self.assertEqual((retrieval["status"], retrieval["k_used"]), ("USED", 1))
        self.assertEqual(retrieval["examples"], [{"problem_id": TRAIN[0], "score": 0.9}])
        self.assertNotIn("example_constraints", retrieval)
        self.assertEqual(result["warnings"], [])

    def test_provider_failure_falls_back_to_baseline(self):
        def broken(*args):
            raise RuntimeError("index missing")
        result, tokenizer, _ = run(rag_mode="bge", live=lambda: (broken, None))
        self.assertEqual(tokenizer.prompts_sent_to_model, [svc.build_prompt(*PROBLEM)])
        self.assertEqual([w["code"] for w in result["warnings"]], ["RETRIEVAL_UNAVAILABLE"])
        self.assertEqual(result["model"]["retrieval"]["status"], "FAILED")
        self.assertEqual(result["value_constraints"][0]["status"], "UNVERIFIED_SUGGESTION")

    def test_provider_construction_failure_falls_back(self):
        with patch.dict(sys.modules, {"rag_retrievers": None}):
            result, tokenizer, _ = run(rag_mode="hybrid")
        self.assertEqual(tokenizer.prompts_sent_to_model, [svc.build_prompt(*PROBLEM)])
        self.assertEqual([w["code"] for w in result["warnings"]], ["RETRIEVAL_UNAVAILABLE"])

    def test_draft_and_approval_unaffected(self):
        provider = self._provider()
        result, _, _ = run(rag_mode="bge", live=lambda: (provider, None))
        draft = drafts.create_draft(result)
        draft = drafts.apply_action(draft, "ACCEPT", "value",
                                    constraint_id=draft["constraints"][0]["id"])
        draft = drafts.approve_category(drafts.approve_category(draft, "value"), "code")
        self.assertEqual(len(drafts.finalize_draft(draft)["approved_constraints"]), 1)


# ---------------------------------------------------------------- evaluation guards

class EvaluationGuardTests(unittest.TestCase):

    def test_dev_heldout_split_is_deterministic_and_validation_only(self):
        dev = metrics.validation_queries(SPLITS, "dev")
        held = metrics.validation_queries(SPLITS, "heldout")
        val = {p for p, s in SPLITS.items() if s == "validation"}
        self.assertEqual(set(dev) | set(held), val)
        self.assertFalse(set(dev) & set(held))
        self.assertEqual(len(dev) + len(held), 716)
        self.assertEqual(dev, metrics.validation_queries(SPLITS, "dev"))
        tests_ = {p for p, s in SPLITS.items() if s == "test"}
        self.assertFalse((set(dev) | set(held)) & tests_)
        with self.assertRaises(metrics.SplitGuardError):
            metrics.validation_queries(SPLITS, "test")

    def test_test_split_ids_are_refused(self):
        test_id = next(p for p, s in SPLITS.items() if s == "test")
        with self.assertRaises(metrics.SplitGuardError):
            metrics.assert_validation_only([test_id], SPLITS)

    def test_mcnemar_and_number_metrics(self):
        self.assertEqual(metrics.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(metrics.mcnemar_exact(10, 0), 2 / 1024)
        self.assertEqual(metrics.mcnemar_exact(5, 5), 1.0)
        self.assertAlmostEqual(metrics.mcnemar_exact(1, 9), 2 * 11 / 1024)
        p, r = metrics.number_precision_recall("1 <= N <= 100", "1 <= N <= 10")
        self.assertEqual((p, r), (0.5, 0.5))
        self.assertEqual(metrics.split_unsupported(["7", "100"], ["1 <= K <= 100"]), (["100"], ["7"]))


class CheckpointTests(unittest.TestCase):
    """dev_tools/evaluate_rag.py resume safety (no model needed)."""

    def setUp(self):
        import tempfile
        from dev_tools import evaluate_rag
        self.harness = evaluate_rag
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder)
        self.meta = {"harness": "rag-eval-v1", "config": "bge", "k_max": 3}
        self.out = self.folder / "run.jsonl"

    def _row(self, pid, meta=None, label="bge-k3-encoded"):
        return json.dumps({"problem_id": pid, "config": label, "run": meta or self.meta})

    def test_resume_skips_done_problems(self):
        self.out.write_text(self._row("P009") + "\n" + self._row("P024") + "\n", encoding="utf-8")
        self.assertEqual(self.harness._read_checkpoint(self.out, "bge-k3-encoded", self.meta),
                         {"P009", "P024"})

    def test_torn_last_line_is_dropped(self):
        self.out.write_text(self._row("P009") + "\n" + '{"problem_id": "P0', encoding="utf-8")
        self.assertEqual(self.harness._read_checkpoint(self.out, "bge-k3-encoded", self.meta), {"P009"})
        self.assertEqual(self.out.read_text(encoding="utf-8"), self._row("P009") + "\n")

    def test_corrupt_middle_line_or_mismatch_refused(self):
        self.out.write_text("garbage\n" + self._row("P009") + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.harness._read_checkpoint(self.out, "bge-k3-encoded", self.meta)
        self.out.write_text(self._row("P009", meta={**self.meta, "k_max": 5}) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.harness._read_checkpoint(self.out, "bge-k3-encoded", self.meta)
        self.out.write_text(self._row("P009", label="flan-k3-encoded") + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.harness._read_checkpoint(self.out, "bge-k3-encoded", self.meta)

    def test_missing_file_means_nothing_done(self):
        self.assertEqual(self.harness._read_checkpoint(self.folder / "none.jsonl", "x", {}), set())


if __name__ == "__main__":
    unittest.main()
