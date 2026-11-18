"""
LoRA preparation fixes (no download, upload, install or training):
  1. the notebook's target round-trip cell (executed here with fake tokenizers)
  2. evaluation-only model loading for evaluate_test_inputs.py --method model,
     which never reads or changes TEST_INPUT_AI_MODE
torch / transformers are never imported: the heavy loader is patched.
"""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_test_input_service as ai
from dev_tools import evaluate_test_inputs as evaluator
from test_input_codec import encode_input

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "training" / "test_input_lora.ipynb"
EOS = 1


class CharTokenizer:
    """Lossless stand-in: one id per character, EOS appended."""
    eos_token_id = EOS

    def __call__(self, text=None, text_target=None, truncation=False, return_tensors=None):
        source = text_target if text_target is not None else text
        return {"input_ids": [ord(c) + 2 for c in source] + [EOS]}

    def decode(self, ids, skip_special_tokens=True, clean_up_tokenization_spaces=False):
        return "".join(chr(int(i) - 2) for i in ids if int(i) >= 2)   # skip pad 0 / EOS 1


class LossyTokenizer(CharTokenizer):
    """Drops the space before '[' on decode (a whitespace-merging tokenizer)."""

    def decode(self, ids, **kwargs):
        return super().decode(ids, **kwargs).replace(" [", "[")


def round_trip_cell():
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    sources = ["".join(c["source"]) for c in cells]
    index = next(i for i, s in enumerate(sources) if s.startswith("# 5a. Target round-trip"))
    return index, sources


def run_cell(rows, tokenizer):
    _, sources = round_trip_cell()
    cell = next(s for s in sources if s.startswith("# 5a. Target round-trip"))
    namespace = {"sys": sys, "BUNDLE": ROOT, "tokenizer": tokenizer,
                 "train_rows": rows, "eval_rows": []}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(cell, "notebook-cell-5a", "exec"), namespace)
    return namespace


def row(text, index=2):
    return {"problem_id": "P1", "case_index": index, "target": encode_input(text)}


class NotebookRoundTripTests(unittest.TestCase):

    def test_cell_is_placed_after_tokenizer_and_before_training(self):
        index, sources = round_trip_cell()
        self.assertTrue(sources[index - 1].startswith("# 4. Model, tokenizer"))
        self.assertTrue(sources[index + 1].startswith("# 5. Tokenise"))
        self.assertTrue(any(s.startswith("# 6. Train") for s in sources[index + 1:]))
        cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
        self.assertEqual(sum(len(c.get("outputs", [])) for c in cells), 0)
        self.assertTrue(all(c.get("execution_count") is None for c in cells if c["cell_type"] == "code"))

    def test_valid_targets_pass(self):
        rows = [row("3\n10 20 30\n"), row("hello world\n", 3), row("a[b]~\n", 4),
                row("1 2\n3 4\n", 5)]
        namespace = run_cell(rows, CharTokenizer())
        self.assertEqual(namespace["target_round_trip_failures"](rows), [])

    def test_lossy_tokenizer_stops_training(self):
        rows = [row("x y\n"), {"problem_id": "P2", "case_index": 7,
                               "target": encode_input("1\n5\n")}]
        with self.assertRaises(AssertionError) as caught:
            run_cell(rows, LossyTokenizer())
        self.assertIn("1 train targets do not round-trip", str(caught.exception))
        self.assertIn("('P2', 7)", str(caught.exception))

    def test_malformed_or_non_canonical_targets_stop_training(self):
        bad = [{"problem_id": "P3", "case_index": 2, "target": "1 [B4"},          # malformed marker
               {"problem_id": "P4", "case_index": 3, "target": "1 [NL]  [NL] 2"}]  # not re-encodable
        with self.assertRaises(AssertionError) as caught:
            run_cell(bad, CharTokenizer())
        self.assertIn("2 train targets", str(caught.exception))


def make_model_dir(folder, weights=b"fake-weights", manifest_hash=None):
    folder = Path(folder)
    (folder / "model.safetensors").write_bytes(weights)
    digest = manifest_hash or hashlib.sha256(weights).hexdigest()
    (folder / ai.MANIFEST_NAME).write_text(json.dumps({
        "model_safetensors_sha256": digest, "base_model_revision": "pinned",
        "dataset_manifest_sha256": "d" * 64}), encoding="utf-8")
    return folder


class FakeTensor(list):
    @property
    def shape(self):
        return (1, len(self[0]))


class FakeModel:
    def __init__(self, outputs):
        self.outputs = outputs
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return self.outputs


class SeqTokenizer(CharTokenizer):
    def __call__(self, text=None, return_tensors=None, truncation=False, **kwargs):
        return {"input_ids": FakeTensor([[5] * 10])}


def fake_torch():
    seeds = []
    module = types.SimpleNamespace(no_grad=contextlib.nullcontext, manual_seed=seeds.append)
    return module, seeds


def encode_ids(text, eos=True):
    return [0] + [ord(c) + 2 for c in encode_input(text)] + ([EOS] if eos else [])


PROBLEM = {"problem_statement": "Sum two numbers.", "input_format": "Two integers A B.",
           "output_format": "Print A+B.", "value_constraints": "1 <= A, B <= 10",
           "sample_input": "1 2\n"}


class EvaluationLoaderTests(unittest.TestCase):

    def setUp(self):
        ai._load.cache_clear()

    def tearDown(self):
        ai._load.cache_clear()

    def test_live_mode_stays_off_and_unavailable(self):
        self.assertEqual(ai.TEST_INPUT_AI_MODE, "off")
        self.assertFalse(ai.status()["available"])
        with self.assertRaises(ai.ModelUnavailable):
            ai.generate_raw_candidates(PROBLEM, 3)

    def test_missing_folder_or_files_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ai.ModelUnavailable):
                ai.evaluation_generator(Path(tmp) / "absent")
            (Path(tmp) / "model.safetensors").write_bytes(b"x")
            with self.assertRaises(ai.ModelUnavailable):
                ai.evaluation_generator(tmp)               # no manifest

    def test_hash_mismatch_refused_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_model_dir(tmp, manifest_hash="0" * 64)
            with patch.object(ai, "_load") as load:
                with self.assertRaises(ai.ModelUnavailable):
                    ai.evaluation_generator(tmp)
                load.assert_not_called()

    def test_verified_model_generates_without_enabling_live_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_model_dir(tmp)
            outputs = [encode_ids("3 4\n"), encode_ids("5 6\n", eos=False), encode_ids("7 8\n")]
            model = FakeModel(outputs)
            torch, seeds = fake_torch()
            with patch.object(ai, "_load", return_value=(SeqTokenizer(), model, {
                    "model_safetensors_sha256": "abc"})) as load, \
                    patch.dict(sys.modules, {"torch": torch}):
                generate = ai.evaluation_generator(tmp)
                result = generate(PROBLEM, 3, seed=7)
            load.assert_called_once_with(str(Path(tmp).resolve()))
            self.assertEqual(result["texts"], ["3 4\n", None, "7 8\n"])
            self.assertIn("OUTPUT_TRUNCATED", result["model"]["warnings"])
            self.assertEqual(model.kwargs["num_return_sequences"], 3)
            self.assertEqual(seeds, [7])
            self.assertEqual(ai.TEST_INPUT_AI_MODE, "off")
            self.assertFalse(ai.status()["available"])

    def test_evaluator_model_run_uses_evaluation_loader(self):
        split_of = evaluator.load_split_of()
        pid = next(p for p, s in sorted(split_of.items()) if s == "validation")
        problem = dict(PROBLEM, problem_id=pid, subset="dev", near_duplicate=False,
                       gold_inputs=["1 2\n", "3 3\n"])
        calls = []

        def fake_generate(row, count, seed=None):
            calls.append((row["problem_id"], count, seed))
            return {"texts": ["3 4\n", "11 1\n", None], "model": {}}

        with tempfile.TemporaryDirectory() as tmp:
            problems = Path(tmp) / "validation_problems.jsonl"
            problems.write_text(json.dumps(problem) + "\n", encoding="utf-8")
            out = Path(tmp) / "report.json"
            with patch.object(ai, "evaluation_generator", return_value=fake_generate) as loader, \
                    contextlib.redirect_stdout(io.StringIO()):
                evaluator.main(["run", "--problems", str(problems), "--method", "model",
                                "--subset", "all", "--count", "3", "--seed", "11",
                                "--model-dir", str(tmp), "--out", str(out)])
            loader.assert_called_once_with(str(tmp))
            report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(calls, [(pid, 3, 11)])
        item = report["per_problem"][0]
        self.assertTrue(item["supported"], item)
        self.assertEqual((item["candidates"], item["generation_rejections"], item["valid"]), (2, 1, 1))
        self.assertEqual(ai.TEST_INPUT_AI_MODE, "off")

    def test_evaluator_reports_unavailable_model_clearly(self):
        split_of = evaluator.load_split_of()
        pid = next(p for p, s in sorted(split_of.items()) if s == "validation")
        with tempfile.TemporaryDirectory() as tmp:
            problems = Path(tmp) / "p.jsonl"
            problems.write_text(json.dumps(dict(PROBLEM, problem_id=pid, subset="dev",
                                                gold_inputs=["1 2\n"])) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                evaluator.main(["run", "--problems", str(problems), "--method", "model",
                                "--model-dir", str(Path(tmp) / "none")])
        self.assertIn("MODEL UNAVAILABLE", str(caught.exception))

    def test_evaluator_still_refuses_test_split(self):
        split_of = evaluator.load_split_of()
        pid = next(p for p, s in sorted(split_of.items()) if s == "test")
        with tempfile.TemporaryDirectory() as tmp:
            problems = Path(tmp) / "p.jsonl"
            problems.write_text(json.dumps(dict(PROBLEM, problem_id=pid, subset="dev",
                                                gold_inputs=["1 2\n"])) + "\n", encoding="utf-8")
            with patch.object(ai, "evaluation_generator") as loader:
                with self.assertRaises(SystemExit) as caught:
                    evaluator.main(["run", "--problems", str(problems), "--method", "model"])
                loader.assert_not_called()
        self.assertIn("REFUSED", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
