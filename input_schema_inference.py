"""
Conservative input-schema inference from a problem's constraint text and
sample input (pure Python). Used only when the teacher gives no explicit
input_schema and the constraints are not in the legacy integer grammar.

Recognised shapes ONLY (anything else -> UNSUPPORTED with reasons):
    scalars      one line of 1-6 integers, every token mapped to a bounded name
    array        "n" line (1-3 ints) then one line of n integers
    string       one line of text with a stated length bound and character set
    graph        "n m" then m edge lines (2 ints, or 3 with a stated weight
                 bound), directed/undirected stated in the text
    tree         "n" then n-1 edge lines, the text says "tree"
    multi-test   "t" then t lines of one recognised scalar shape
Every inferred schema must accept the sample input, otherwise it is refused.
Unstated graph properties are treated conservatively: self-loops and
multiple edges are not allowed unless the text says they are.
"""

import re

from input_schema import SchemaError, check_schema, validate_input

_NUM = r"[-+]?\s*(?:\d+(?:\.\d+)?\s*[*x×·]\s*)?(?:10\s*\^\s*\d+|\d+(?:\s*\^\s*\d+)?|\d+e\d+)"
_LE = r"(<=|=<|≤|<|⩽)"
# Bounds must be plain numbers: "k <= 2n" or "a_i <= n" is NOT a numeric bound.
_BOUND = re.compile(rf"(?<![A-Za-z0-9_^.])({_NUM})\s*{_LE}\s*([^<≤⩽=\n;]{{1,60}}?)\s*{_LE}\s*({_NUM})"
                    r"(?![A-Za-z0-9_^(*×·/+]|\s*[*×·/+]|\.\d)")
# Wording that means the input has more lines than a degenerate sample shows.
_LINES_OF = r"(?:next|following|then|remaining)\s+(?:\S+\s+){0,3}lines?"
_MORE_THAN_ONE_LINE = re.compile(rf"second line|{_LINES_OF}|each of the (?:next|following)|"
                                 r"\b[A-Za-z]\s*(?:-\s*1\s*)?lines\b|several lines|multiple lines",
                                 re.I)
_MORE_THAN_TWO_LINES = re.compile(r"third line|last line|next\s+\S+\s+lines|following\s+\S+\s+lines|"
                                  r"each of the next|absent|then\s+\S+\s+lines", re.I)
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_ELEMENT = re.compile(r"^(?:each\s+|every\s+|all\s+)?([A-Za-z][A-Za-z0-9]*)\s*(?:\[\s*[a-z]\s*\]|_\s*\{?[a-z]\}?|\([a-z]\))$")
_LENGTH = re.compile(r"^(?:\|\s*([A-Za-z]\w*)\s*\||len(?:gth)?\s*\(\s*([A-Za-z]\w*)\s*\)|"
                     r"(?:the\s+)?length\s+of\s+(?:the\s+)?(?:string\s+)?([A-Za-z]\w*)|"
                     r"([A-Za-z]\w*)\s*\.\s*length|(?:line|string|text)\s+length|"
                     r"length\s+of\s+(?:the\s+)?(?:line|string|text))$", re.I)
_INT_TOKEN = re.compile(r"[+-]?\d+")


def _number(text):
    text = text.replace(" ", "")
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    factor = 1
    m = re.match(r"(\d+(?:\.\d+)?)[*x×·](.*)$", text)
    if m:
        factor, text = float(m.group(1)), m.group(2)
    if "^" in text:
        base, exp = text.split("^")
        value = int(base) ** int(exp)
    elif "e" in text:
        mant, exp = text.split("e")
        value = int(mant) * 10 ** int(exp)
    else:
        if "." in text:
            return None
        value = int(text)
    value = value * factor
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    return sign * value


def parse_bounds(constraint_text):
    """{'scalars': {name: (lo, hi)}, 'elements': {...}, 'lengths': {...}, 'generic_length': (lo,hi)|None}"""
    out = {"scalars": {}, "elements": {}, "lengths": {}, "generic_length": None}
    text = (constraint_text or "").replace("≥", ">=")
    for match in _BOUND.finditer(text):
        lo_text, lo_op, names, hi_op, hi_text = match.groups()
        lo, hi = _number(lo_text), _number(hi_text)
        if lo is None or hi is None:
            continue
        lo += 1 if lo_op == "<" else 0
        hi -= 1 if hi_op == "<" else 0
        if lo > hi:
            continue
        for raw in names.split(","):
            name = " ".join(raw.split()).strip(" .")
            if not name:
                continue
            length = _LENGTH.match(name)
            element = _ELEMENT.match(name)
            if length:
                target = next((g for g in length.groups() if g), None)
                if target:
                    out["lengths"][target] = (lo, hi)
                else:
                    out["generic_length"] = (lo, hi)
            elif element:
                out["elements"][element.group(1)] = (lo, hi)
            elif _IDENT.fullmatch(name):
                out["scalars"].setdefault(name, (lo, hi))
    return out


def _order_in_text(names, text):
    positions = {}
    for name in names:
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
        if m is None:
            return None
        positions[name] = m.start()
    return sorted(names, key=positions.get)


def _map_scalars(count, scalars, input_format, exclude=()):
    names = [n for n in scalars if n not in exclude]
    if count == 1 and len(names) == 1:
        return names
    if len(names) != count:
        return None
    return _order_in_text(names, input_format or "")


def _charset(text_blob, sample_text):
    blob = text_blob.lower()
    if "binary string" in blob or re.search(r"only (?:of )?(?:the )?characters? '?0'? and '?1'?", blob):
        return "binary"
    if re.search(r"lowercase (?:english |latin )?letters", blob) and "uppercase" not in blob:
        return "lower"
    if re.search(r"uppercase (?:english |latin )?letters", blob) and "lowercase" not in blob:
        return "upper"
    if re.search(r"(?:english |latin )?letters and digits|alphanumeric", blob):
        return "alnum"
    if re.search(r"\bascii\b|printable|any characters?", blob):
        return "printable"
    if re.search(r"(?:english |latin )?letters", blob):
        return "alpha"
    if re.search(r"only digits|consists of digits", blob):
        return "digits"
    return None


def _graph_flags(blob):
    blob = blob.lower()
    undirected = "undirected" in blob or "bidirectional" in blob or "two-way" in blob
    directed = re.search(r"(?<!un)directed", blob) is not None and not undirected
    if not undirected and not directed:
        return None
    tree = re.search(r"\btree\b", blob) is not None
    simple = "simple graph" in blob
    self_loops = (re.search(r"self[- ]loops? (?:are|is|may be) allowed|may contain self[- ]loops", blob)
                  is not None) and not simple
    multi = (re.search(r"multiple edges (?:are|may be) allowed|may contain multiple edges|"
                       r"parallel edges (?:are|may be) allowed", blob) is not None) and not simple
    connected = tree or re.search(r"(?<!not )(?<!dis)connected", blob) is not None
    return {"directed": directed, "self_loops": self_loops, "multi_edges": multi,
            "connected": bool(connected), "tree": tree and not directed}


def _lines(sample):
    return sample.rstrip("\n").split("\n")


def _all_int(line):
    tokens = line.split()
    return bool(tokens) and all(_INT_TOKEN.fullmatch(t) for t in tokens)


def _var_specs(names, scalars):
    return {n: {"min": scalars[n][0], "max": scalars[n][1]} for n in names}


def _candidates(problem, bounds, lines):
    """Yield (shape, schema_dict) attempts in priority order."""
    fmt = problem.get("input_format") or ""
    blob = " ".join(str(problem.get(k) or "") for k in
                    ("problem_statement", "input_format", "value_constraints"))
    scalars = bounds["scalars"]
    first = lines[0].split()

    flags = _graph_flags(blob)
    single_vertex_tree = (flags is not None and flags["tree"] and len(lines) == 1
                          and len(first) == 1 and re.search(r"-\s*1\s+lines", fmt) is not None)

    # graph / tree
    if _all_int(lines[0]) and len(first) in (1, 2) and (len(lines) >= 2 or single_vertex_tree) \
            and all(_all_int(l) and len(l.split()) in (2, 3) for l in lines[1:]):
        widths = {len(l.split()) for l in lines[1:]} or {2}
        if flags and len(widths) == 1:
            names = _map_scalars(len(first), scalars, fmt)
            weighted = widths == {3}
            weight_names = [n for n in scalars if names and n not in names]
            weight = None
            if weighted:
                if len(weight_names) == 1:
                    lo, hi = scalars[weight_names[0]]
                    weight = {"min": lo, "max": hi}
            if names and (not weighted or weight):
                n_name = names[0]
                if len(first) == 2:
                    count = names[1]
                elif flags["tree"]:
                    count = f"{n_name}-1"
                else:
                    count = None
                if count:
                    yield "graph", {
                        "variables": _var_specs(names, scalars),
                        "items": [{"type": "ints", "vars": names},
                                  {"type": "edges", "count": count, "vertices": n_name,
                                   "index_base": 0 if re.search(r"0-(?:based|indexed)", blob.lower()) else 1,
                                   "weight": weight, **flags}]}

    # count + array
    if len(lines) == 2 and _all_int(lines[0]) and 1 <= len(first) <= 3 and _all_int(lines[1]) \
            and len(bounds["elements"]) == 1 and len(lines[1].split()) >= 2 \
            and not _MORE_THAN_TWO_LINES.search(fmt):
        names = _map_scalars(len(first), scalars, fmt)
        size = len(lines[1].split())
        if names:
            length_vars = [n for n, tok in zip(names, first) if int(tok) == size]
            if len(length_vars) == 1:
                array_name, (lo, hi) = next(iter(bounds["elements"].items()))
                yield "array", {
                    "variables": _var_specs(names, scalars),
                    "items": [{"type": "ints", "vars": names},
                              {"type": "array", "name": array_name, "length": length_vars[0],
                               "min": lo, "max": hi}]}

    # multi-test scalars
    if len(lines) >= 2 and _all_int(lines[0]) and len(first) == 1 \
            and int(first[0]) == len(lines) - 1 >= 2 and all(_all_int(l) for l in lines[1:]):
        widths = {len(l.split()) for l in lines[1:]}
        if len(widths) == 1:
            width = widths.pop()
            names = _map_scalars(1 + width, scalars, fmt)
            if names:
                yield "multi_test", {
                    "variables": _var_specs(names, scalars),
                    "items": [{"type": "ints", "vars": names[:1]},
                              {"type": "repeat", "count": names[0],
                               "items": [{"type": "ints", "vars": names[1:]}]}]}

    # scalars on one line
    if len(lines) == 1 and _all_int(lines[0]) and 1 <= len(first) <= 6 \
            and not _MORE_THAN_ONE_LINE.search(fmt):
        names = _map_scalars(len(first), scalars, fmt)
        if names:
            yield "scalars", {"variables": _var_specs(names, scalars),
                              "items": [{"type": "ints", "vars": names}]}

    # single string line
    if len(lines) == 1 and not _all_int(lines[0]) and not _MORE_THAN_ONE_LINE.search(fmt):
        length = None
        if len(bounds["lengths"]) == 1:
            length = next(iter(bounds["lengths"].values()))
        elif not bounds["lengths"] and bounds["generic_length"]:
            length = bounds["generic_length"]
        # The input format is the most specific description of the characters.
        charset = next((c for c in (_charset(problem.get(k) or "", lines[0]) for k in
                                    ("input_format", "value_constraints", "problem_statement"))
                        if c), None)
        if length and charset and length[0] >= 0:
            name = next(iter(bounds["lengths"]), "S")
            yield "string", {"variables": {},
                             "items": [{"type": "string", "name": name,
                                        "min_length": length[0], "max_length": length[1],
                                        "charset": charset}]}


def infer_schema(problem):
    """{'supported': bool, 'shape', 'schema' (checked) | None, 'reasons': [...]}."""
    sample = problem.get("sample_input")
    if not isinstance(sample, str) or not sample.strip():
        return {"supported": False, "shape": None, "schema": None,
                "reasons": ["NO_SAMPLE_INPUT: a sample input is needed to infer the format"]}
    sample = sample.replace("\r\n", "\n")
    bounds = parse_bounds(problem.get("value_constraints"))
    lines = _lines(sample)
    reasons = []
    for shape, raw in _candidates(problem, bounds, lines):
        try:
            schema = check_schema(raw)
        except SchemaError as error:
            reasons.append(f"{shape}: {error}")
            continue
        check = validate_input(schema, sample)
        if check["valid"]:
            return {"supported": True, "shape": shape, "schema": schema, "reasons": []}
        reasons.append(f"{shape}: SCHEMA_REJECTS_SAMPLE ({check['errors'][0]['code']})")
    if not reasons:
        reasons.append("UNRECOGNISED_FORMAT: the sample input does not match a supported "
                       "shape with stated bounds (scalars, array, string, graph, tree, multi-test)")
    return {"supported": False, "shape": None, "schema": None, "reasons": reasons}
