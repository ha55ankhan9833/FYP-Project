"""
D1-D4 regression tests for the FLAN-T5 value-constraint service.

D1 placeholder decoding (authoritative notebook cell-86 mapping, real dataset samples)
D2 output length (384 = training target limit; truncation detected, never hidden)
D3 numeric validation runs on decoded text only
D4 over-long prompts are measured and refused, never silently truncated

No model, torch or transformers is needed: the tokenizer, model and torch are faked.
The real-model before/after check is dev_tools/constraint_model_comparison.py (.venv_ai).
"""

import contextlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_constraint_service as svc
import constraint_codec as codec
import teacher_approval_service as drafts

SAMPLES = json.loads(
    (Path(__file__).parent / "data" / "constraint_codec_samples.json")
    .read_text(encoding="utf-8"))["samples"]

EOS = 1


# ---------------------------------------------------------------- fakes

class FakeEncoding(dict):
    def to(self, _device):
        return self


class FakeTokenizer:
    eos_token_id = EOS

    def __init__(self, token_count, raw_output):
        self.token_count = token_count
        self.raw_output = raw_output
        self.calls = []

    def __call__(self, prompt, truncation=None, return_tensors=None, **kwargs):
        self.calls.append({"prompt": prompt, "truncation": truncation,
                           "return_tensors": return_tensors, **kwargs})
        return FakeEncoding(input_ids=[7] * self.token_count)

    def decode(self, sequence, skip_special_tokens=False,
               clean_up_tokenization_spaces=True):
        self.decode_args = (skip_special_tokens, clean_up_tokenization_spaces)
        return self.raw_output


class FakeSequence(list):
    def tolist(self):
        return list(self)


class FakeModel:
    def __init__(self, generated_ids):
        # decoder start token 0, then the generated ids
        self.sequence = FakeSequence([0] + list(generated_ids))
        self.generate_kwargs = None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return [self.sequence]


FAKE_TORCH = types.SimpleNamespace(no_grad=contextlib.nullcontext)


def run_service(raw_output, token_count=100, generated_ids=(5, 6, EOS),
                problem="Given N (1 <= N <= 10^9), print N.",
                input_format="One integer N.", output_format="Print N."):
    tokenizer = FakeTokenizer(token_count, raw_output)
    model = FakeModel(generated_ids)
    with patch.object(svc, "load_model", return_value=(tokenizer, model, "cpu")), \
            patch.dict(sys.modules, {"torch": FAKE_TORCH}):
        result = svc.analyze_cpp_problem(problem, input_format, output_format)
    return result, tokenizer, model


# ---------------------------------------------------------------- D1

class PlaceholderDecodingTests(unittest.TestCase):

    def test_samples_cover_every_marker_in_the_dataset(self):
        markers = set()
        for sample in SAMPLES:
            markers.update(codec._MARKER.findall(sample["notebook_encoded"]))
        for expected in ("SP", "NL", "B3C", "B3E", "B5E", "B5B", "B5D",
                         "B7B", "B7D", "B7E", "B5C", "BE2", "B80", "B93",
                         "BC2", "BB2"):
            self.assertIn(expected, markers)

    def test_encoder_matches_the_notebook_on_real_dataset_constraints(self):
        for sample in SAMPLES:
            with self.subTest(problem=sample["problem_id"]):
                self.assertEqual(codec.encode_constraint(sample["text"]),
                                 sample["notebook_encoded"])

    def test_round_trip_real_dataset_constraints(self):
        for sample in SAMPLES:
            with self.subTest(problem=sample["problem_id"]):
                decoded, warnings = codec.decode_model_output(
                    sample["notebook_encoded"])
                self.assertEqual(decoded, sample["text"].strip())
                self.assertEqual(warnings, [])

    def test_every_marker_type(self):
        cases = {
            "[SP]": " ", "[NL]": "\n", "[TAB]": "\t", "[B3C]": "<",
            "[B3E]": ">", "[B5E]": "^", "[B5B]": "[", "[B5D]": "]",
            "[B7B]": "{", "[B7D]": "}", "[B7E]": "~", "[B5C]": "\\",
            "[B60]": "`", "[BE2][B89][BA4]": "≤",
        }
        for encoded, expected in cases.items():
            with self.subTest(marker=encoded):
                self.assertEqual(codec.decode_constraint("a" + encoded + "b"),
                                 "a" + expected + "b")

    def test_operators_and_exponents_preserved(self):
        raw = "1[SP][B3C]=[SP]N[SP][B3C]=[SP]10[B5E]9;[SP]|A[B5B]i[B5D]|[SP][B3E]=[SP]0"
        self.assertEqual(svc.clean_constraint_text(raw),
                         "1 <= N <= 10^9; |A[i]| >= 0")

    def test_b3a_is_a_colon_byte_not_ampersand(self):
        # The old backend table mapped [B3A] to "&"; 0x3A is ":".
        self.assertEqual(codec.decode_constraint("a[B3A]b"), "a:b")

    def test_newlines_and_inner_spaces_are_kept(self):
        raw = "1[SP][B3C]=[SP]N[SP][B3C]=[SP]100[NL]1[SP][B3C]=[SP]M[SP][SP]x"
        self.assertEqual(svc.clean_constraint_text(raw),
                         "1 <= N <= 100\n1 <= M  x")

    def test_invalid_utf8_bytes_warn_instead_of_raising(self):
        text, warnings = codec.decode_model_output("N[SP][BE2]")
        self.assertEqual(text, "N �")
        self.assertEqual([w["code"] for w in warnings], ["DECODE_INVALID_BYTES"])

    def test_malformed_markers_are_reported(self):
        for raw in ("1[SP][B3C=[SP]N", "N[SP][b5e]9", "N[B5]", "x]"):
            with self.subTest(raw=raw):
                _text, warnings = codec.decode_model_output(raw)
                self.assertIn("DECODE_INCOMPLETE", [w["code"] for w in warnings])

    def test_decoded_brackets_are_not_mistaken_for_markers(self):
        _text, warnings = codec.decode_model_output("A[B5B]1[B5D][SP][B3C]=[SP]5")
        self.assertEqual(warnings, [])


# ---------------------------------------------------------------- D2

class OutputLengthTests(unittest.TestCase):

    def test_limit_covers_every_training_target(self):
        self.assertEqual(codec.MAX_NEW_TOKENS, 384)
        longest = max(s["encoded_tokens"] for s in SAMPLES)
        self.assertEqual(longest, 384)  # P1599, the longest of all 7,411
        self.assertLessEqual(longest, codec.MAX_NEW_TOKENS)

    def test_generation_settings_match_the_validated_notebook_run(self):
        _result, tokenizer, model = run_service("N")
        kwargs = model.generate_kwargs
        self.assertEqual(kwargs["max_new_tokens"], 384)
        self.assertEqual(kwargs["num_beams"], 4)
        self.assertIs(kwargs["early_stopping"], True)
        self.assertIs(kwargs["do_sample"], False)
        self.assertEqual(tokenizer.decode_args, (True, False))

    def test_long_complete_output_is_not_flagged(self):
        long_sample = max(SAMPLES, key=lambda s: s["encoded_tokens"])
        ids = [5] * (long_sample["encoded_tokens"] - 1) + [EOS]
        result, _tok, _model = run_service(
            long_sample["notebook_encoded"], generated_ids=ids,
            problem="A problem without explicit bounds.")
        self.assertTrue(result["model"]["suggestion_used"])
        item = result["value_constraints"][0]
        self.assertTrue(result["model"]["output_complete"])
        self.assertEqual(result["model"]["generated_tokens"], 384)
        self.assertEqual(item["text"], long_sample["text"].strip())
        self.assertNotIn("OUTPUT_TRUNCATED", [w["code"] for w in item["warnings"]])
        self.assertNotIn("DECODE_INCOMPLETE", [w["code"] for w in item["warnings"]])

    def test_output_cut_at_the_limit_is_reported(self):
        result, _tok, _model = run_service(
            "1[SP][B3C]=[SP]N[SP][B3C]=[SP]10",
            generated_ids=[5] * codec.MAX_NEW_TOKENS,
            problem="Read N and print it.")
        item = result["value_constraints"][0]
        self.assertFalse(result["model"]["output_complete"])
        self.assertIn("OUTPUT_TRUNCATED", [w["code"] for w in item["warnings"]])
        self.assertEqual(item["status"], "UNVERIFIED_SUGGESTION")


# ---------------------------------------------------------------- D3

class NumericValidationTests(unittest.TestCase):

    def test_encoding_artifacts_are_not_unsupported_numbers(self):
        # [B5E] / [B3C] contain hex digits (5, 3); the old check flagged them.
        result, _tok, _model = run_service(
            "1[SP][B3C]=[SP]N[SP][B3C]=[SP]10[B5E]9",
            problem="Read N and print it. N is at least 1 and at most 10^9.")
        item = result["value_constraints"][0]
        self.assertEqual(item["text"], "1 <= N <= 10^9")
        self.assertEqual(item["unsupported_numbers"], [])
        self.assertEqual(item["status"], "NEEDS_SEMANTIC_REVIEW")

    def test_genuinely_unsupported_numbers_are_still_reported(self):
        result, _tok, _model = run_service(
            "1[SP][B3C]=[SP]N[SP][B3C]=[SP]100000",
            problem="Read N and print it. N is positive.")
        item = result["value_constraints"][0]
        self.assertEqual(item["unsupported_numbers"], ["1", "100000"])
        self.assertEqual(item["status"], "UNVERIFIED_SUGGESTION")

    def test_unsupported_numbers_helper(self):
        self.assertEqual(codec.unsupported_numbers("0 <= K <= 2*n*n", "k up to 2n^2, k >= 0"), [])
        self.assertEqual(codec.unsupported_numbers("1 <= N <= 7", "N is small"), ["1", "7"])


# ---------------------------------------------------------------- D4

class InputLengthTests(unittest.TestCase):

    def test_boundary(self):
        self.assertIsNone(codec.input_length_warning(511))
        self.assertIsNone(codec.input_length_warning(512))
        warning = codec.input_length_warning(513)
        self.assertEqual(warning["code"], "INPUT_TOO_LONG")
        self.assertEqual((warning["input_tokens"], warning["limit"]), (513, 512))
        with self.assertRaises(ValueError):
            codec.input_length_warning("513")

    def test_prompt_at_limit_runs_the_model_without_truncation(self):
        result, tokenizer, model = run_service("N", token_count=512)
        self.assertIsNotNone(model.generate_kwargs)
        self.assertEqual(result["model"]["status"], "GENERATED")
        self.assertEqual(result["warnings"], [])
        self.assertIs(tokenizer.calls[0]["truncation"], False)
        self.assertNotIn("max_length", tokenizer.calls[1])

    def test_prompt_over_limit_is_refused_not_truncated(self):
        result, _tok, model = run_service(
            "N", token_count=513,
            problem="Sort the array without using loops.",
            input_format="N then N integers.", output_format="Sorted array.")
        self.assertIsNone(model.generate_kwargs)          # model never ran
        self.assertEqual(result["value_constraints"], [])  # no guess presented
        self.assertEqual([w["code"] for w in result["warnings"]], ["INPUT_TOO_LONG"])
        self.assertEqual(result["model"]["status"], "NOT_RUN_INPUT_TOO_LONG")
        self.assertEqual(result["model"]["input_tokens"], 513)
        self.assertTrue(result["teacher_approval_required"])
        self.assertEqual([c["type"] for c in result["code_constraints"]],
                         ["loops_prohibited"])

    def test_explicit_bounds_still_offered_when_input_too_long(self):
        result, _tok, _model = run_service(
            "N", token_count=900, problem="Print N where 0 <= N <= 12.")
        self.assertEqual([c["text"] for c in result["value_constraints"]],
                         ["0 <= N <= 12"])
        self.assertEqual(result["warnings"][0]["code"], "INPUT_TOO_LONG")

    def test_prompt_is_the_training_format(self):
        _result, tokenizer, _model = run_service("N", problem="P", input_format="I",
                                                 output_format="O")
        self.assertEqual(
            tokenizer.calls[0]["prompt"],
            "Generate value constraints for the following C++ programming problem.\n"
            "Problem: P\nInput Format: I\nOutput Format: O")


# ---------------------------------------------------------------- drafts

class DraftCompatibilityTests(unittest.TestCase):

    def test_old_shaped_ai_result_unchanged(self):
        draft = drafts.create_draft({
            "value_constraints": [{"text": "1 <= N <= 5", "status": "NEEDS_TEACHER_REVIEW"}],
            "code_constraints": []})
        self.assertNotIn("ai_warnings", draft)
        self.assertNotIn("warnings", draft["constraints"][0])

    def test_warnings_reach_the_teacher_and_approval_still_works(self):
        result, _tok, _model = run_service(
            "N", token_count=600, problem="Long statement.", input_format="I",
            output_format="O")
        draft = drafts.create_draft(result)
        self.assertEqual([w["code"] for w in draft["ai_warnings"]], ["INPUT_TOO_LONG"])
        self.assertEqual(draft["constraints"], [])

        draft = drafts.apply_action(draft, "ADD", "value", text="1 <= N <= 100")
        draft = drafts.approve_category(draft, "value")
        draft = drafts.approve_category(draft, "code")
        final = drafts.finalize_draft(draft)
        self.assertEqual([c["text"] for c in final["approved_constraints"]],
                         ["1 <= N <= 100"])
        self.assertEqual(final["draft"]["ai_warnings"][0]["code"], "INPUT_TOO_LONG")

    def test_item_warnings_are_kept_and_malformed_ones_dropped(self):
        draft = drafts.create_draft({
            "value_constraints": [{"text": "x", "status": "UNVERIFIED_SUGGESTION",
                                   "warnings": [{"code": "OUTPUT_TRUNCATED", "message": "m"},
                                                "junk", {"code": 5}]}],
            "warnings": "not a list"})
        self.assertEqual(draft["constraints"][0]["warnings"],
                         [{"code": "OUTPUT_TRUNCATED", "message": "m"}])
        self.assertNotIn("ai_warnings", draft)


if __name__ == "__main__":
    unittest.main()
