"""
Schema-based validation of stdin test inputs (pure Python, no execution).

Extends the narrow legacy rule (1-3 integers on one line, see
generic_test_inputs) WITHOUT changing it: a schema built from legacy approved
constraints is validated by the unchanged generic_test_inputs.validate_inputs.

Three separate layers in every result:
    syntax       line structure, token counts, integer tokens
    constraints  declared ranges, lengths and character sets
    semantic     problem-specific rules (distinct / sorted arrays, graph
                 rules, relations between variables such as K <= N)
Unknown item types, unknown keys, unbound counts and unspecified graph
semantics are rejected as schema errors: nothing unknown is treated as valid.

Schema (version 1):
{
  "variables": {"N": {"min": 1, "max": 100000}, ...},   integer scalars
  "items": [                                            consumed in order
    {"type": "ints",   "vars": ["N", "K"]},             one line
    {"type": "array",  "name": "A", "length": "N", "min": -1e9, "max": 1e9,
                       "distinct": false, "order": null|"asc"|"desc"},  one line
    {"type": "string", "name": "S", "min_length": 1, "max_length": 100,
                       "length": null|"N", "charset": "lower", "chars": null}, one line
    {"type": "grid",   "name": "G", "rows": "R", "cols": "C", "cell": "char"|"int",
                       "charset"/"chars" (char) | "min"/"max" (int)},  R lines
    {"type": "edges",  "count": "M"|"N-1"|int, "vertices": "N", "index_base": 1,
                       "directed": bool, "self_loops": bool, "multi_edges": bool,
                       "connected": bool, "tree": false, "weight": null|{"min","max"}},
    {"type": "repeat", "count": "T", "items": [ ...items, no nested repeat... ]}
  ],
  "relations": [{"left": "K", "op": "<=", "right": "N"}]    semantic, optional
}
Counts ("length", "rows", "cols", "count") are a declared variable bound by an
earlier "ints" item, "VAR-1", or a non-negative integer literal.
"""

import re

from generic_test_inputs import (
    MAX_ABS_BOUND,
    parse_approved_value_constraints,
    validate_inputs as legacy_validate_inputs,
)

SCHEMA_VERSION = 1
MAX_INPUT_CHARS = 1_000_000
MAX_ITEMS = 20
MAX_VARIABLES = 20
MAX_ERRORS = 20
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_COUNT = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)(?:\s*-\s*1)?")
_INT = re.compile(r"[+-]?\d+")
CHARSETS = {
    "lower": "abcdefghijklmnopqrstuvwxyz",
    "upper": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "digits": "0123456789",
    "binary": "01",
}
CHARSETS["alpha"] = CHARSETS["lower"] + CHARSETS["upper"]
CHARSETS["alnum"] = CHARSETS["alpha"] + CHARSETS["digits"]
CHARSETS["printable"] = "".join(chr(c) for c in range(32, 127))
RELATION_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
                ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}

_ITEM_KEYS = {
    "ints": {"type", "vars"},
    "array": {"type", "name", "length", "min", "max", "distinct", "order"},
    "string": {"type", "name", "min_length", "max_length", "length", "charset", "chars"},
    "grid": {"type", "name", "rows", "cols", "cell", "charset", "chars", "min", "max"},
    "edges": {"type", "count", "vertices", "index_base", "directed", "self_loops",
              "multi_edges", "connected", "tree", "weight"},
    "repeat": {"type", "count", "items"},
}
GRAPH_FLAGS = ("directed", "self_loops", "multi_edges", "connected")


class SchemaError(ValueError):
    """The schema itself is invalid (code INVALID_INPUT_SCHEMA) or describes a
    format this validator does not support (code UNSUPPORTED_INPUT_FORMAT)."""

    def __init__(self, message, code="INVALID_INPUT_SCHEMA"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Schema checking
# --------------------------------------------------------------------------

def _int_value(value, what):
    if type(value) is not int:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise SchemaError(f"{what} must be an integer")
    if abs(value) > MAX_ABS_BOUND:
        raise SchemaError(f"{what} exceeds the supported range")
    return value


def _range(spec, what):
    if not isinstance(spec, dict) or set(spec) - {"min", "max"} or {"min", "max"} - set(spec):
        raise SchemaError(f"{what} needs exactly min and max")
    lo, hi = _int_value(spec["min"], f"{what}.min"), _int_value(spec["max"], f"{what}.max")
    if lo > hi:
        raise SchemaError(f"{what} has an empty range")
    return {"min": lo, "max": hi}


def _count_ref(value, bound, what):
    if type(value) is int:
        if value < 0:
            raise SchemaError(f"{what} must not be negative")
        return value
    if not isinstance(value, str) or not _COUNT.fullmatch(value.strip()):
        raise SchemaError(f"{what} must be a variable, 'VAR-1' or an integer")
    name = _COUNT.fullmatch(value.strip()).group(1)
    if name not in bound:
        raise SchemaError(f"{what} uses {name} before an 'ints' item reads it")
    return " ".join(value.split()).replace(" ", "")


def _charset(item, what):
    chars = item.get("chars")
    charset = item.get("charset")
    if chars is not None:
        if charset is not None or not isinstance(chars, str) or not chars \
                or any(not 32 <= ord(c) <= 126 for c in chars):
            raise SchemaError(f"{what}.chars must be printable ASCII (and not combined with charset)")
        return "".join(sorted(set(chars)))
    if charset not in CHARSETS:
        raise SchemaError(f"{what}.charset must be one of {sorted(CHARSETS)}",
                          "UNSUPPORTED_INPUT_FORMAT")
    return CHARSETS[charset]


def _check_items(items, variables, bound, depth, path):
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise SchemaError(f"{path} must be a list of 1-{MAX_ITEMS} items")
    checked = []
    for index, item in enumerate(items):
        where = f"{path}[{index}]"
        if not isinstance(item, dict) or item.get("type") not in _ITEM_KEYS:
            raise SchemaError(f"{where}: unsupported item type {item.get('type') if isinstance(item, dict) else item!r}",
                              "UNSUPPORTED_INPUT_FORMAT")
        kind = item["type"]
        unknown = set(item) - _ITEM_KEYS[kind]
        if unknown:
            raise SchemaError(f"{where}: unknown keys {sorted(unknown)}")
        out = {"type": kind}
        if kind == "ints":
            names = item.get("vars")
            if not isinstance(names, list) or not names or len(names) > MAX_VARIABLES:
                raise SchemaError(f"{where}.vars must list declared variables")
            for name in names:
                if name not in variables:
                    raise SchemaError(f"{where}: variable {name!r} is not declared")
                bound.add(name)
            out["vars"] = list(names)
        elif kind == "array":
            out["name"] = _name(item.get("name"), where)
            out["length"] = _count_ref(item.get("length"), bound, f"{where}.length")
            out.update(_range({"min": item.get("min"), "max": item.get("max")}, where))
            out["distinct"] = _flag(item.get("distinct", False), f"{where}.distinct")
            if item.get("order") not in (None, "asc", "desc"):
                raise SchemaError(f"{where}.order must be null, 'asc' or 'desc'")
            out["order"] = item.get("order")
        elif kind == "string":
            out["name"] = _name(item.get("name"), where)
            if item.get("length") is not None:
                out["length"] = _count_ref(item["length"], bound, f"{where}.length")
                out["min_length"], out["max_length"] = 0, MAX_INPUT_CHARS
            else:
                rng = _range({"min": item.get("min_length"), "max": item.get("max_length")},
                             f"{where} length")
                if rng["min"] < 0:
                    raise SchemaError(f"{where}.min_length must not be negative")
                out["length"] = None
                out["min_length"], out["max_length"] = rng["min"], rng["max"]
            out["chars"] = _charset(item, where)
        elif kind == "grid":
            out["name"] = _name(item.get("name"), where)
            out["rows"] = _count_ref(item.get("rows"), bound, f"{where}.rows")
            out["cols"] = _count_ref(item.get("cols"), bound, f"{where}.cols")
            if item.get("cell") == "char":
                out["cell"], out["chars"] = "char", _charset(item, where)
            elif item.get("cell") == "int":
                out["cell"] = "int"
                out.update(_range({"min": item.get("min"), "max": item.get("max")}, where))
            else:
                raise SchemaError(f"{where}.cell must be 'char' or 'int'")
        elif kind == "edges":
            out["count"] = _count_ref(item.get("count"), bound, f"{where}.count")
            vertices = item.get("vertices")
            if vertices not in bound:
                raise SchemaError(f"{where}.vertices must be a variable read earlier")
            out["vertices"] = vertices
            base = item.get("index_base", 1)
            if base not in (0, 1):
                raise SchemaError(f"{where}.index_base must be 0 or 1")
            out["index_base"] = base
            for flag in GRAPH_FLAGS:
                if flag not in item:
                    raise SchemaError(
                        f"{where}: graph semantics must be explicit ({flag} is missing); "
                        "unverifiable graph formats are not supported",
                        "UNSUPPORTED_INPUT_FORMAT")
                out[flag] = _flag(item[flag], f"{where}.{flag}")
            out["tree"] = _flag(item.get("tree", False), f"{where}.tree")
            if out["tree"] and (out["directed"] or out["self_loops"] or out["multi_edges"]
                                or not out["connected"]):
                raise SchemaError(f"{where}: a tree must be undirected, connected and simple")
            out["weight"] = None if item.get("weight") is None else _range(item["weight"], f"{where}.weight")
        elif kind == "repeat":
            if depth > 0:
                raise SchemaError(f"{where}: nested repeat is not supported", "UNSUPPORTED_INPUT_FORMAT")
            out["count"] = _count_ref(item.get("count"), bound, f"{where}.count")
            out["items"] = _check_items(item.get("items"), variables, set(bound), depth + 1,
                                        f"{where}.items")
        checked.append(out)
    return checked


def _name(value, where):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise SchemaError(f"{where}.name must be an identifier")
    return value


def _flag(value, what):
    if type(value) is not bool:
        raise SchemaError(f"{what} must be true or false")
    return value


def check_schema(schema):
    """Validated, normalised copy of a schema. Raises SchemaError."""
    if not isinstance(schema, dict):
        raise SchemaError("input_schema must be a JSON object")
    unknown = set(schema) - {"version", "variables", "items", "relations", "legacy"}
    if unknown:
        raise SchemaError(f"input_schema has unknown keys {sorted(unknown)}")
    if schema.get("version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise SchemaError(f"input_schema version must be {SCHEMA_VERSION}")
    raw_vars = schema.get("variables", {})
    if not isinstance(raw_vars, dict) or len(raw_vars) > MAX_VARIABLES:
        raise SchemaError(f"variables must be an object with at most {MAX_VARIABLES} entries")
    variables = {}
    for name, spec in raw_vars.items():
        variables[_name(name, f"variables[{name!r}]")] = _range(spec, f"variables[{name!r}]")
    bound = set()
    items = _check_items(schema.get("items"), variables, bound, 0, "items")
    relations = []
    for index, rel in enumerate(schema.get("relations") or []):
        if (not isinstance(rel, dict) or set(rel) != {"left", "op", "right"}
                or rel["op"] not in RELATION_OPS
                or rel["left"] not in variables or rel["right"] not in variables):
            raise SchemaError(f"relations[{index}] must be {{left, op, right}} over declared variables")
        relations.append(dict(rel))
    legacy = schema.get("legacy", False) is True
    return {"version": SCHEMA_VERSION, "variables": variables, "items": items,
            "relations": relations, "legacy": legacy}


def schema_from_legacy_constraints(constraints):
    """Schema equivalent of the legacy approved integer constraints (1-3 vars)."""
    variables = parse_approved_value_constraints(constraints)
    schema = {"variables": {v["variable"]: {"min": v["minimum"], "max": v["maximum"]}
                            for v in variables},
              "items": [{"type": "ints", "vars": [v["variable"] for v in variables]}],
              "legacy": True}
    checked = check_schema(schema)
    checked["legacy_constraints"] = constraints
    return checked


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

class _Stop(Exception):
    pass


class _Checker:
    def __init__(self, schema, lines):
        self.schema = schema
        self.lines = lines
        self.pos = 0
        self.errors = []
        self.values = {}

    def error(self, layer, code, message, line=None):
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({"layer": layer, "code": code, "message": message,
                                "line": line})
        if layer == "syntax":
            raise _Stop()

    def next_line(self, what):
        if self.pos >= len(self.lines):
            self.error("syntax", "MISSING_LINE", f"expected a line for {what}", self.pos + 1)
        self.pos += 1
        return self.pos, self.lines[self.pos - 1]

    def count(self, ref, scope, what, line):
        if type(ref) is int:
            return ref
        name = _COUNT.fullmatch(ref).group(1)
        value = scope[name] - (1 if ref.endswith("-1") else 0)
        if value < 0:
            self.error("syntax", "NEGATIVE_COUNT", f"{what} would be {value}", line)
        if value > MAX_INPUT_CHARS:
            self.error("syntax", "COUNT_TOO_LARGE", f"{what} is too large to read", line)
        return value

    def ints(self, text, line, expected, what):
        tokens = text.split()
        if len(tokens) != expected:
            self.error("syntax", "TOKEN_COUNT",
                       f"{what}: expected {expected} value(s), found {len(tokens)}", line)
        values = []
        for token in tokens:
            if not _INT.fullmatch(token):
                self.error("syntax", "NOT_INTEGER", f"{what}: {token[:20]!r} is not an integer", line)
            values.append(int(token))
        return values

    def in_range(self, value, lo, hi, what, line):
        if not lo <= value <= hi:
            self.error("constraints", "OUT_OF_RANGE",
                       f"{what}={value} outside [{lo}, {hi}]", line)

    def run(self, items, scope):
        for item in items:
            getattr(self, "_" + item["type"])(item, scope)

    def _ints(self, item, scope):
        line, text = self.next_line("ints")
        values = self.ints(text, line, len(item["vars"]), " ".join(item["vars"]))
        for name, value in zip(item["vars"], values):
            spec = self.schema["variables"][name]
            self.in_range(value, spec["min"], spec["max"], name, line)
            scope[name] = value
            self.values.setdefault(name, value)

    def _array(self, item, scope):
        line, text = self.next_line(item["name"])
        length = self.count(item["length"], scope, f"length of {item['name']}", line)
        values = self.ints(text, line, length, item["name"])
        for value in values:
            self.in_range(value, item["min"], item["max"], f"{item['name']} element", line)
        if item["distinct"] and len(set(values)) != len(values):
            self.error("semantic", "NOT_DISTINCT", f"{item['name']} has repeated values", line)
        if item["order"] == "asc" and values != sorted(values):
            self.error("semantic", "NOT_SORTED", f"{item['name']} is not non-decreasing", line)
        if item["order"] == "desc" and values != sorted(values, reverse=True):
            self.error("semantic", "NOT_SORTED", f"{item['name']} is not non-increasing", line)
        self.values.setdefault(item["name"], values)

    def _string(self, item, scope):
        line, text = self.next_line(item["name"])
        if item["length"] is not None:
            want = self.count(item["length"], scope, f"length of {item['name']}", line)
            if len(text) != want:
                self.error("constraints", "LENGTH_MISMATCH",
                           f"{item['name']} has length {len(text)}, expected {want}", line)
        elif not item["min_length"] <= len(text) <= item["max_length"]:
            self.error("constraints", "LENGTH_OUT_OF_RANGE",
                       f"{item['name']} length {len(text)} outside "
                       f"[{item['min_length']}, {item['max_length']}]", line)
        bad = sorted(set(text) - set(item["chars"]))
        if bad:
            self.error("constraints", "CHARSET",
                       f"{item['name']} contains characters outside its character set: "
                       f"{''.join(bad)[:10]!r}", line)
        self.values.setdefault(item["name"], text)

    def _grid(self, item, scope):
        rows = self.count(item["rows"], scope, f"rows of {item['name']}", self.pos + 1)
        cols = self.count(item["cols"], scope, f"columns of {item['name']}", self.pos + 1)
        grid = []
        for _ in range(rows):
            line, text = self.next_line(item["name"])
            if item["cell"] == "char":
                if len(text) != cols:
                    self.error("syntax", "ROW_LENGTH",
                               f"{item['name']} row has {len(text)} cells, expected {cols}", line)
                bad = sorted(set(text) - set(item["chars"]))
                if bad:
                    self.error("constraints", "CHARSET",
                               f"{item['name']} contains {''.join(bad)[:10]!r}", line)
                grid.append(text)
            else:
                values = self.ints(text, line, cols, item["name"])
                for value in values:
                    self.in_range(value, item["min"], item["max"], f"{item['name']} cell", line)
                grid.append(values)
        self.values.setdefault(item["name"], grid)

    def _edges(self, item, scope):
        start = self.pos + 1
        count = self.count(item["count"], scope, "edge count", start)
        n = scope[item["vertices"]]
        base = item["index_base"]
        width = 3 if item["weight"] else 2
        edges = []
        for _ in range(count):
            line, text = self.next_line("edge")
            values = self.ints(text, line, width, "edge")
            u, v = values[0], values[1]
            for endpoint in (u, v):
                if not base <= endpoint <= n - 1 + base:
                    self.error("constraints", "VERTEX_OUT_OF_RANGE",
                               f"vertex {endpoint} outside [{base}, {n - 1 + base}]", line)
            if item["weight"]:
                self.in_range(values[2], item["weight"]["min"], item["weight"]["max"],
                              "edge weight", line)
            edges.append((u, v, line))
        self._graph_semantics(item, edges, n, base, start)

    def _graph_semantics(self, item, edges, n, base, line):
        seen = set()
        for u, v, at in edges:
            if u == v and not item["self_loops"]:
                self.error("semantic", "SELF_LOOP", f"self-loop at vertex {u}", at)
            key = (u, v) if item["directed"] else (min(u, v), max(u, v))
            if key in seen and not item["multi_edges"]:
                self.error("semantic", "DUPLICATE_EDGE", f"repeated edge {u} {v}", at)
            seen.add(key)
        if item["tree"] and len(edges) != n - 1:
            self.error("semantic", "NOT_A_TREE", f"a tree on {n} vertices needs {n - 1} edges", line)
        if item["connected"] and n > 0:
            parent = list(range(n))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            for u, v, _ in edges:
                if base <= u < n + base and base <= v < n + base:
                    parent[find(u - base)] = find(v - base)
            if len({find(x) for x in range(n)}) != 1:
                self.error("semantic", "NOT_CONNECTED", "graph is not connected", line)

    def _repeat(self, item, scope):
        count = self.count(item["count"], scope, "repeat count", self.pos + 1)
        for _ in range(count):
            self.run(item["items"], dict(scope))


def _split_lines(text):
    text = text.replace("\r\n", "\n")
    if "\r" in text:
        return None
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def _result(errors, values, schema_source=None):
    layers = {layer: not any(e["layer"] == layer for e in errors)
              for layer in ("syntax", "constraints", "semantic")}
    syntax_ok = layers["syntax"]
    return {"valid": all(layers.values()),
            "syntax_valid": syntax_ok,
            "constraints_valid": layers["constraints"] if syntax_ok else None,
            "semantic_valid": layers["semantic"] if syntax_ok else None,
            "errors": errors, "values": values if syntax_ok else None}


def validate_input(schema, text):
    """Validate one stdin text against a CHECKED schema (see check_schema)."""
    if not isinstance(text, str) or not text.strip():
        return _result([{"layer": "syntax", "code": "EMPTY_INPUT",
                         "message": "input is empty", "line": None}], None)
    if len(text) > MAX_INPUT_CHARS:
        return _result([{"layer": "syntax", "code": "INPUT_TOO_LARGE",
                         "message": f"input exceeds {MAX_INPUT_CHARS} characters", "line": None}], None)

    if schema.get("legacy"):
        return _validate_legacy(schema, text)

    lines = _split_lines(text)
    if lines is None:
        return _result([{"layer": "syntax", "code": "CARRIAGE_RETURN",
                         "message": "stray carriage return in input", "line": None}], None)
    checker = _Checker(schema, lines)
    try:
        checker.run(schema["items"], {})
        if checker.pos < len(lines):
            checker.error("syntax", "EXTRA_LINES",
                          f"{len(lines) - checker.pos} unexpected line(s) after the input",
                          checker.pos + 1)
    except _Stop:
        pass
    if not any(e["layer"] == "syntax" for e in checker.errors):
        for rel in schema["relations"]:
            left, right = checker.values.get(rel["left"]), checker.values.get(rel["right"])
            if isinstance(left, int) and isinstance(right, int) \
                    and not RELATION_OPS[rel["op"]](left, right):
                checker.error("semantic", "RELATION",
                              f"{rel['left']} {rel['op']} {rel['right']} does not hold "
                              f"({left} vs {right})")
    return _result(checker.errors, checker.values)


def _validate_legacy(schema, text):
    """Exactly the legacy rule (generic_test_inputs.validate_inputs)."""
    variables = [{"variable": n, "minimum": s["min"], "maximum": s["max"]}
                 for n, s in schema["variables"].items()]
    try:
        legacy_validate_inputs(variables, [{"input_data": text}])
    except ValueError as error:
        message = str(error).replace("Test 1: ", "")
        layer = "constraints" if "outside approved range" in message else "syntax"
        code = "OUT_OF_RANGE" if layer == "constraints" else (
            "NOT_INTEGER" if "not an integer" in message else "TOKEN_COUNT")
        return _result([{"layer": layer, "code": code, "message": message, "line": 1}], None)
    values = dict(zip(schema["variables"], (int(t) for t in text.split())))
    return _result([], values)
