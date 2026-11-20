"""
Placeholder codec and output checks for the FLAN-T5 value-constraint model.

The model in models/flan_t5_constraint_final was fine-tuned on constraint
text ENCODED by the Colab notebook (G:/My Drive/Colab Notebooks/
FYP_Auto_Test_Case_Generator.ipynb, cell 86 "STEP 17.6", functions
encode_constraint / decode_constraint), because the FLAN-T5 SentencePiece
vocabulary cannot represent < > \\ ^ ` { } ~ and does not keep spaces or
newlines. The mapping below is a byte-for-byte transcription of that cell;
no marker is invented here:

    byte 33..126 except < > \\ ^ ` { } ~ [ ]  -> the character itself
    byte 32 (space)                          -> [SP]
    byte 10 (newline)                        -> [NL]
    byte 9  (tab)                            -> [TAB]
    any other byte (incl. each UTF-8 byte)   -> [BXX]  (two upper-case hex digits)

Decoding replaces every [SP] / [NL] / [TAB] / [BXX] marker by its byte(s)
and UTF-8-decodes the result, exactly as the notebook's decoder did.

This module has no torch / transformers dependency so it can be unit tested
in the backend virtual environment.
"""

import re

# ---------------------------------------------------------------- limits
# Training (notebook cell 88): MAX_INPUT_LENGTH = 512, MAX_TARGET_LENGTH = 384.
# The model config also declares n_positions = 512. Every training prompt was
# <= 476 tokens (cell 87), so 512 is the largest input the model was trained
# to read in full. Counts include the closing </s> token, as in training.
INPUT_TOKEN_LIMIT = 512
# The longest encoded training target is 384 tokens (cell 87 reported 330 on
# the training split; 384 over all 7,411 dataset constraints, measured with
# this model's tokenizer.json). 384 = the training target limit, so no
# constraint the model was trained on can be cut off.
MAX_NEW_TOKENS = 384

_UNESCAPED = "<>\\^`{}~[]"
_MARKER = re.compile(r"\[(SP|NL|TAB|B[0-9A-F]{2})\]")
_NUMBER = re.compile(r"\d+")


def has_malformed_markers(raw_text):
    """
    The encoder always escapes "[" and "]" as [B5B] / [B5D], so in ENCODED
    text a bracket can only belong to a well-formed marker. Any bracket left
    after removing well-formed markers is a malformed marker. (Checked on the
    raw text: decoded text may legitimately contain brackets, e.g. A[i].)
    """
    return bool(re.search(r"[\[\]]", _MARKER.sub("", raw_text)))


def encode_constraint(text):
    """Notebook cell 86 encode_constraint, unchanged."""
    result = []
    for byte in text.encode("utf-8"):
        char = chr(byte)
        if 33 <= byte <= 126 and char not in _UNESCAPED:
            result.append(char)
        elif byte == 32:
            result.append("[SP]")
        elif byte == 10:
            result.append("[NL]")
        elif byte == 9:
            result.append("[TAB]")
        else:
            result.append(f"[B{byte:02X}]")
    return "".join(result)


def decode_constraint(text, errors="strict"):
    """
    Notebook cell 86 decode_constraint. errors="strict" raises
    UnicodeDecodeError on invalid UTF-8 (what the notebook did);
    errors="replace" inserts U+FFFD instead.
    """
    parts = []
    last = 0
    for match in _MARKER.finditer(text):
        parts.append(text[last:match.start()].encode("utf-8"))
        marker = match.group(1)
        if marker == "SP":
            parts.append(b" ")
        elif marker == "NL":
            parts.append(b"\n")
        elif marker == "TAB":
            parts.append(b"\t")
        else:
            parts.append(bytes.fromhex(marker[1:]))
        last = match.end()
    parts.append(text[last:].encode("utf-8"))
    return b"".join(parts).decode("utf-8", errors=errors)


def decode_model_output(raw_text):
    """
    Decode raw model text for presentation.
    Returns (text, warnings). Never raises on model output.
    """
    warnings = []
    try:
        text = decode_constraint(raw_text)
    except UnicodeDecodeError:
        text = decode_constraint(raw_text, errors="replace")
        warnings.append({
            "code": "DECODE_INVALID_BYTES",
            "message": ("The model produced byte markers that are not valid "
                        "UTF-8; they are shown as \ufffd.")})

    if has_malformed_markers(raw_text):
        warnings.append({
            "code": "DECODE_INCOMPLETE",
            "message": ("The model output contains malformed placeholder "
                        "markers that could not be decoded.")})

    # Only surrounding whitespace is removed; inner spaces and newlines are
    # part of the constraint (multi-line constraints are parsed per line).
    return text.strip(), warnings


def unsupported_numbers(constraint_text, source_text):
    """
    Numbers in the DECODED constraint that do not appear in the problem
    text. Must be called on decoded text: placeholder markers such as
    [B5E] contain hex digits that are not numbers.
    """
    source = set(_NUMBER.findall(source_text))
    predicted = set(_NUMBER.findall(constraint_text))
    return sorted(predicted - source, key=lambda value: (len(value), value))


def input_length_warning(token_count, limit=INPUT_TOKEN_LIMIT):
    """None when the prompt fits; otherwise a structured warning."""
    if type(token_count) is not int or token_count < 0:
        raise ValueError("token_count must be a non-negative integer")
    if token_count <= limit:
        return None
    return {
        "code": "INPUT_TOO_LONG",
        "message": (f"The problem is {token_count} model tokens long; the "
                    f"constraint model reads at most {limit}. The model was "
                    "not run, so no AI value constraint is suggested. Add the "
                    "value constraints manually or shorten the statement."),
        "input_tokens": token_count,
        "limit": limit,
    }


def output_truncated_warning(generated_tokens, limit=MAX_NEW_TOKENS):
    return {
        "code": "OUTPUT_TRUNCATED",
        "message": (f"The model stopped at the {limit}-token output limit "
                    "before finishing; the suggestion is incomplete."),
        "generated_tokens": generated_tokens,
        "limit": limit,
    }
