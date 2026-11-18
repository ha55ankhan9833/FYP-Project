"""
Phase A: light test-input codec and dataset preparation (pure Python).
The real-dataset integrity test is read-only and skips if the file is absent.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests import fakes  # noqa: F401

import test_input_codec as codec
from dev_tools import build_test_input_dataset as builder

ORIGINAL = Path(r"D:\Combine dataset to p2237 to p4411\FYP_DATASET_P001_P7411_FINAL_VERIFIED"
                r"\FYP_DATASET_P001_P7411_FINAL_VERIFIED.json")


class CodecTests(unittest.TestCase):
    SAMPLES = ["5\n", "8\n3 -1 -2 5 -5 2 -2 4\n", "Red fish, blue FISH; red bird.\n",
               "a<b>c\\d^e`f{g}h~i[j]k\n", "1 0 1\n\n", "\n3\n", "x\ty\n", "caf\u00e9\n",
               "A[1] [NL] [B5B]\n", "3 3\n#.#\n...\n#.#\n"]

    def test_round_trip(self):
        for text in self.SAMPLES:
            if not codec.is_canonical(text):
                continue
            encoded = codec.encode_input(text)
            self.assertEqual(codec.decode_input(encoded), text, text)
            self.assertNotIn("\n", encoded)

    def test_missing_trailing_newline_is_canonical(self):
        self.assertTrue(codec.is_canonical("1 2"))
        self.assertEqual(codec.decode_input(codec.encode_input("1 2")), "1 2\n")

    def test_non_canonical_refused(self):
        for text in ("1  2\n", "1 2 \n", " 1\n"):
            self.assertFalse(codec.is_canonical(text))
            with self.assertRaises(codec.CodecError):
                codec.encode_input(text)

    def test_non_latin1_refused(self):
        with self.assertRaises(codec.CodecError):
            codec.encode_input("\u4e2d\n")

    def test_brackets_and_markers_are_escaped(self):
        encoded = codec.encode_input("[NL]\n")
        self.assertNotIn("[NL]", encoded.replace("[B5B]NL[B5D]", ""))
        self.assertEqual(codec.decode_input(encoded), "[NL]\n")

    def test_malformed_model_output(self):
        self.assertEqual(codec.decode_model_output("1 2 [NL")[0], None)
        self.assertEqual(codec.decode_model_output("1 2 [B5")[1], ["DECODE_INVALID"])
        self.assertEqual(codec.decode_model_output("   ")[1], ["DECODE_EMPTY"])
        self.assertEqual(codec.decode_model_output("3 [NL] 1 2 3"), ("3\n1 2 3\n", []))

    def test_prompt_is_shared_and_contains_all_fields(self):
        prompt = codec.build_generation_prompt("Sum  them.", "Two ints.", "One int.",
                                               "1 <= a <= 9", "1 2\n")
        self.assertTrue(prompt.startswith(codec.PROMPT_HEADER))
        for part in ("Problem: Sum them.", "Input format: Two ints.", "Output format: One int.",
                     "Constraints: 1 <= a <= 9", "Sample input: 1 2"):
            self.assertIn(part, prompt)
        with self.assertRaises(codec.CodecError):
            codec.build_generation_prompt("x" * 10001, "", "", "", "1\n")


def _problem(pid, sample="1 2\n", n_cases=10, extra=None):
    cases = [{"input": sample, "expected_output": "3\n"}]
    cases += [{"input": f"{i} {i + 1}", "expected_output": "x\n"} for i in range(2, n_cases + 1)]
    row = {"Problem_ID": pid, "Problem_Description": f"Add numbers {pid}.",
           "Input_Specification": "Two integers a and b.", "Output_Specification": "Their sum.",
           "Sample_Input": sample, "Sample_Output": "3\n", "Value_Constraints": "1 <= a, b <= 100",
           "Code_Constraints": "", "CPP_Solution": "int main(){}", "Test_Cases": json.dumps(cases)}
    row.update(extra or {})
    return row


class DatasetBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        problems = [_problem(f"P{i:03d}") for i in range(1, 13)]
        # P011: a target with a double space is dropped, not altered.
        cases = json.loads(problems[10]["Test_Cases"])
        cases[3]["input"] = "4  5\n"
        problems[10]["Test_Cases"] = json.dumps(cases)
        self.original = self.dir / "original.json"
        self.original.write_text(json.dumps(problems), encoding="utf-8")
        # sha256(id) % 3 decides dev/heldout; ids chosen so both appear.
        splits = {"P001": "train", "P002": "train", "P003": "train", "P004": "train",
                  "P005": "validation", "P006": "validation", "P007": "validation",
                  "P008": "validation", "P009": "test", "P010": "test", "P011": "train"}
        self.splits = self.dir / "splits.json"
        self.splits.write_text(json.dumps({"splits": splits}), encoding="utf-8")
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        return builder.build(self.original, self.out, None, splits_path=self.splits,
                             expected_sha=None, expected_splits=None, expected_problems=None,
                             near_duplicates_path=None, **kw)

    def rows(self, name):
        return [json.loads(l) for l in (self.out / name).read_text(encoding="utf-8").splitlines()]

    def test_split_separation_and_targets(self):
        manifest = self.build()
        train = self.rows("train.jsonl")
        dev, heldout = self.rows("validation_dev.jsonl"), self.rows("validation_heldout.jsonl")
        problems = self.rows("validation_problems.jsonl")
        self.assertEqual({r["problem_id"] for r in train}, {"P001", "P002", "P003", "P004", "P011"})
        self.assertEqual({r["problem_id"] for r in dev + heldout}, {"P005", "P006", "P007", "P008"})
        everything = json.dumps([train, dev, heldout, problems])
        for test_id in ("P009", "P010"):
            self.assertNotIn(test_id, everything)            # test split untouched
        self.assertNotIn("P012", everything)                # no split -> excluded
        self.assertEqual(manifest["drops"]["problem_test_split_untouched"], 2)
        self.assertEqual(manifest["drops"]["problem_excluded_no_split"], 1)
        self.assertEqual(manifest["drops"]["target_not_canonical"], 1)
        self.assertEqual({r["case_index"] for r in train}, set(range(2, 11)))
        self.assertEqual(len([r for r in train if r["problem_id"] == "P011"]), 8)
        for r in train:
            self.assertIn("Sample input: 1 2", r["prompt"])
            self.assertEqual(codec.decode_input(r["target"]),
                             f"{r['case_index']} {r['case_index'] + 1}\n")
        self.assertTrue(all(len(p["gold_inputs"]) == 10 for p in problems))
        self.assertEqual(manifest["leakage_checks"]["test_problems_written"], 0)

    def test_refuses_overwrite(self):
        self.build()
        with self.assertRaises(SystemExit):
            self.build()

    def test_leakage_check_rejects_overlap(self):
        files = {"train": [{"problem_id": "P1", "target": "a"}],
                 "validation_dev": [{"problem_id": "P1", "target": "a"}],
                 "validation_heldout": []}
        with self.assertRaises(SystemExit):
            builder.check_leakage(files, [], {"P1": "train"})
        files["validation_dev"] = [{"problem_id": "P2", "target": "a"}]
        with self.assertRaises(SystemExit):
            builder.check_leakage(files, [], {"P1": "train", "P2": "test"})

    def test_hash_mismatch_refused(self):
        with self.assertRaises(SystemExit):
            builder.build(self.original, self.out, None, splits_path=self.splits,
                          expected_sha="0" * 64, near_duplicates_path=None)


@unittest.skipUnless(ORIGINAL.exists(), "original dataset not present")
class RealDatasetIntegrityTests(unittest.TestCase):
    """Read-only checks on the verified original dataset."""

    @classmethod
    def setUpClass(cls):
        cls.problems, cls.split_of, cls.sha, _ = builder.load_sources(ORIGINAL)

    def test_counts_and_split(self):
        self.assertEqual(self.sha, builder.ORIGINAL_SHA256)
        self.assertEqual(len(self.problems), 7411)
        counts = builder.check_split(self.problems, self.split_of)
        self.assertEqual(dict(counts), {"train": 5636, "validation": 716, "test": 709})
        self.assertEqual(7411 - sum(counts.values()), 350)

    def test_cases_and_codec_round_trip(self):
        total = canonical = 0
        for p in self.problems:
            cases = json.loads(p["Test_Cases"])
            self.assertEqual(len(cases), 10)
            self.assertEqual(cases[0]["input"], p["Sample_Input"])
            for case in cases:
                total += 1
                text = codec.with_trailing_newline(case["input"])
                if codec.is_canonical(text):
                    canonical += 1
                    self.assertEqual(codec.decode_input(codec.encode_input(text)), text)
        self.assertEqual(total, 74110)
        self.assertGreaterEqual(canonical / total, 0.995)


if __name__ == "__main__":
    unittest.main()
