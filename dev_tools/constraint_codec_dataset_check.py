"""
READ-ONLY D1/D2/D4 evidence on the full original dataset.

For every Value_Constraints string in the original 7,411-problem dataset:
  encode (notebook mapping) -> real model tokenizer -> token ids -> decode
  -> constraint_codec decode, and require the original text back exactly
  (the same check the training notebook ran in cell 86, here on all 7,411).
Also reports encoded target lengths against MAX_NEW_TOKENS (D2) and prompt
lengths against INPUT_TOKEN_LIMIT (D4).

Run with the AI environment (needs only the `tokenizers` package):
    .venv_ai\\Scripts\\python.exe dev_tools\\constraint_codec_dataset_check.py <dataset.json>
Nothing is written.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tokenizers import Tokenizer  # noqa: E402

import constraint_codec as codec  # noqa: E402
from ai_constraint_service import build_prompt  # noqa: E402

TOKENIZER = (Path(__file__).resolve().parents[1] / "models" /
             "flan_t5_constraint_final" / "tokenizer.json")


def main(dataset_path):
    tokenizer = Tokenizer.from_file(str(TOKENIZER))
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))

    exact, failures, unk = 0, [], 0
    target_lengths, prompt_lengths = [], []
    unk_id = tokenizer.token_to_id("<unk>")

    for record in data:
        original = record["Value_Constraints"]
        encoded = codec.encode_constraint(original)
        ids = tokenizer.encode(encoded).ids
        unk += unk_id in ids
        target_lengths.append(len(ids))
        through_tokenizer = tokenizer.decode(ids, skip_special_tokens=True)
        decoded, warnings = codec.decode_model_output(through_tokenizer)
        if decoded == original.strip() and not warnings:
            exact += 1
        else:
            failures.append(record["Problem_ID"])
        prompt = build_prompt(record["Problem_Description"],
                              record["Input_Specification"],
                              record["Output_Specification"])
        prompt_lengths.append(len(tokenizer.encode(prompt).ids))

    total = len(data)
    print(f"problems: {total}")
    print(f"D1 exact round trips through the model tokenizer: {exact}/{total}; "
          f"targets with <unk>: {unk}; failures: {failures[:10]}")
    print(f"D2 encoded target tokens: max {max(target_lengths)}; "
          f"> old limit 128: {sum(n > 128 for n in target_lengths)}; "
          f"> MAX_NEW_TOKENS {codec.MAX_NEW_TOKENS}: "
          f"{sum(n > codec.MAX_NEW_TOKENS for n in target_lengths)}")
    print(f"D4 prompts > INPUT_TOKEN_LIMIT {codec.INPUT_TOKEN_LIMIT}: "
          f"{sum(n > codec.INPUT_TOKEN_LIMIT for n in prompt_lengths)} "
          f"(max {max(prompt_lengths)}) - now refused with INPUT_TOO_LONG "
          "instead of silently truncated")
    return 0 if exact == total else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
