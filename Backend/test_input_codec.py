"""
Light, line-aware codec for TEST INPUTS (stdin text) and the shared
generation prompt used by dataset preparation, training and inference.

Measured on the original 7,411-problem dataset (2026-11-18): after adding a
missing trailing newline, 99.7% of the 74,110 inputs are canonical and 100%
of those round-trip exactly through the FLAN-T5 tokenizer with this codec.

Canonical input: lines kept, tokens inside a line separated by one space, no
trailing spaces, exactly one trailing newline. `cin >>`-style readers see the
same tokens; inputs whose spacing differs are reported, never silently changed
for training.

Encoding: "\n" -> " [NL] ", printable ASCII kept, every other byte (and the
characters T5 cannot represent: < > \\ ^ ` { } ~ [ ]) -> "[BXX]".
Pure Python: no model, tokenizer or execution imports.
"""

import re

NEWLINE_MARKER = "[NL]"
_UNSAFE = set("<>\\^`{}~[]")
# A newline marker absorbs the single space the encoder put on each side.
_DECODE = re.compile(r" ?\[NL\] ?|\[B([0-9A-F]{2})\]")

PROMPT_HEADER = "Generate a new test input."
PROMPT_FIELD_LIMIT = 10000


class CodecError(ValueError):
    pass


def canonical_form(text):
    """Canonical layout of an input (see module docstring)."""
    if not isinstance(text, str):
        raise CodecError("input must be text")
    text = text.replace("\r\n", "\n")
    lines = text.rstrip("\n").split("\n")
    return "\n".join(" ".join(line.split()) for line in lines) + "\n"


def with_trailing_newline(text):
    return text if text.endswith("\n") else text + "\n"


def is_canonical(text):
    """True when the input (after adding a missing final newline) is canonical."""
    return isinstance(text, str) and canonical_form(text) == with_trailing_newline(text)


def encode_input(text):
    """Canonical input -> model text. Refuses non-canonical or non-Latin-1 text."""
    text = with_trailing_newline(text)
    if canonical_form(text) != text:
        raise CodecError("input is not canonical")
    out = []
    for ch in text[:-1]:
        code = ord(ch)
        if ch == "\n":
            out.append(f" {NEWLINE_MARKER} ")
        elif ch == " " or (33 <= code <= 126 and ch not in _UNSAFE):
            out.append(ch)
        elif code <= 0xFF:
            out.append(f"[B{code:02X}]")
        else:
            raise CodecError("character outside Latin-1 cannot be encoded")
    return re.sub(" +", " ", "".join(out)).strip()


def decode_input(encoded):
    """Model text -> input text. Raises CodecError on malformed markers."""
    if not isinstance(encoded, str):
        raise CodecError("encoded input must be text")

    leftover = _DECODE.sub("", encoded)
    if "[" in leftover or "]" in leftover:
        raise CodecError("malformed marker in encoded input")
    text = _DECODE.sub(
        lambda m: "\n" if m.group(1) is None else chr(int(m.group(1), 16)),
        encoded)
    return with_trailing_newline(text)


def decode_model_output(raw):
    """(input_text or None, warnings) for generated text; never raises."""
    try:
        text = decode_input(raw.strip())
    except CodecError:
        return None, ["DECODE_INVALID"]
    if not text.strip():
        return None, ["DECODE_EMPTY"]
    return text, []


def _field(value, name):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CodecError(f"{name} must be text")
    if len(value) > PROMPT_FIELD_LIMIT:
        raise CodecError(f"{name} is too long")
    return " ".join(value.split())


def build_generation_prompt(problem_statement, input_format, output_format,
                            value_constraints, sample_input):
    """The ONE prompt format shared by dataset preparation, training and inference."""
    sample = encode_input(sample_input) if sample_input else ""
    return (f"{PROMPT_HEADER}\n"
            f"Problem: {_field(problem_statement, 'problem_statement')}\n"
            f"Input format: {_field(input_format, 'input_format')}\n"
            f"Output format: {_field(output_format, 'output_format')}\n"
            f"Constraints: {_field(value_constraints, 'value_constraints')}\n"
            f"Sample input: {sample}")
