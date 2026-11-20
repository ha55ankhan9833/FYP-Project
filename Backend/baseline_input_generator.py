"""
Deterministic baseline test-INPUT generator (pure Python, seeded, no ML,
no code execution, no expected outputs).

Given a CHECKED input schema (input_schema.check_schema) it produces candidate
stdin inputs from boundary heuristics, structural patterns and mutations of
the sample input. Every candidate is re-validated by input_schema.validate_input;
invalid, duplicate, sample-identical and oversized candidates are returned as
structured rejections, never silently kept or dropped.

Generated inputs are CANDIDATES for teacher review, not verified test cases.
"""

import random

from input_schema import validate_input
from test_input_codec import canonical_form

METHOD = "deterministic_baseline_v1"
DEFAULT_COUNT = 20
MAX_COUNT = 50
MAX_CANDIDATE_CHARS = 2000
_PATTERNS = ("random", "all_min", "all_max", "all_equal", "ascending",
             "descending", "alternating")


class _TooLarge(Exception):
    pass


class _Infeasible(Exception):
    pass


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


class _Builder:
    """Renders one input for one recipe; counts are shrunk to fit the budget."""

    def __init__(self, schema, recipe, rng, budget):
        self.schema = schema
        self.recipe = recipe
        self.rng = rng
        self.budget = budget
        self.size = 0
        self.lines = []
        self.capped = False
        self.count_vars = _count_vars(schema["items"])

    def emit(self, line):
        self.size += len(line) + 1
        if self.size > self.budget:
            raise _TooLarge()
        self.lines.append(line)

    # ---- scalars -------------------------------------------------------
    def scalar(self, name, scope):
        spec = self.schema["variables"][name]
        lo, hi = spec["min"], spec["max"]
        strategy = self.recipe.get("overrides", {}).get(name, self.recipe["scalar"])
        if name in self.count_vars:
            hi = min(hi, max(lo, self.count_cap(name)))
            if hi < spec["max"] and strategy in ("max", "mixed"):
                self.capped = True
        mid = lo + (hi - lo) // 2
        if strategy == "min":
            return lo
        if strategy == "max":
            return hi
        if strategy == "mid":
            return mid
        if isinstance(strategy, int):
            return _clip(strategy, lo, hi)
        if strategy == "mixed":
            return self.rng.choice([lo, hi, mid, self.rng.randint(lo, hi)])
        return self.rng.randint(lo, hi)

    def count_cap(self, name):
        cap = self.recipe.get("count_cap", 60)
        return cap if self.count_vars[name] == "linear" else int(cap ** 0.5) or 1

    def count(self, ref, scope):
        if type(ref) is int:
            return ref
        name = ref[:-2] if ref.endswith("-1") else ref
        return scope[name] - (1 if ref.endswith("-1") else 0)

    # ---- items ---------------------------------------------------------
    def run(self, items, scope):
        for item in items:
            getattr(self, "_" + item["type"])(item, scope)

    def _ints(self, item, scope):
        values = []
        for name in item["vars"]:
            scope[name] = self.scalar(name, scope)
            values.append(scope[name])
        self._apply_relations(scope)
        self.emit(" ".join(str(v) for v in [scope[n] for n in item["vars"]]))

    def _apply_relations(self, scope):
        for rel in self.schema["relations"]:
            left, right = rel["left"], rel["right"]
            if left in scope and right in scope:
                a, b = scope[left], scope[right]
                op = rel["op"]
                if (op in ("<=", "<") and a > b) or (op in (">=", ">") and a < b):
                    scope[left] = b if op in ("<=", ">=") else (b - 1 if op == "<" else b + 1)
                elif op == "==":
                    scope[left] = b
                elif op == "!=" and a == b:
                    scope[left] = a + 1

    def _values(self, n, lo, hi, pattern, distinct):
        rng = self.rng
        if distinct:
            if hi - lo + 1 < n:
                raise _Infeasible("not enough distinct values")
            if pattern in ("ascending", "descending", "all_min"):
                values = list(range(lo, lo + n))
            elif pattern == "all_max":
                values = list(range(hi - n + 1, hi + 1))
            else:
                values = rng.sample(range(lo, hi + 1), n) if hi - lo < 10 ** 6 else \
                    list({rng.randint(lo, hi) for _ in range(n * 2)})[:n]
                if len(values) < n:
                    raise _Infeasible("could not draw distinct values")
            if pattern == "descending":
                values.reverse()
            return values
        if pattern == "all_min":
            return [lo] * n
        if pattern == "all_max":
            return [hi] * n
        if pattern == "all_equal":
            return [rng.randint(lo, hi)] * n
        if pattern == "alternating":
            return [lo if i % 2 == 0 else hi for i in range(n)]
        values = [rng.randint(lo, hi) for _ in range(n)]
        if pattern == "ascending":
            values.sort()
        elif pattern == "descending":
            values.sort(reverse=True)
        return values

    def _array(self, item, scope):
        n = self.count(item["length"], scope)
        if self.recipe.get("sample") and item["name"] in self.recipe["sample"] \
                and len(self.recipe["sample"][item["name"]]) == n:
            base = self.recipe["sample"][item["name"]]
            values = [v if self.rng.random() < 0.5 else self.rng.randint(item["min"], item["max"])
                      for v in base]
            if item["distinct"] and len(set(values)) != n:
                values = self._values(n, item["min"], item["max"], "random", True)
        else:
            values = self._values(n, item["min"], item["max"], self.recipe["pattern"],
                                  item["distinct"])
        if item["order"] == "asc":
            values.sort()
        elif item["order"] == "desc":
            values.sort(reverse=True)
        self.emit(" ".join(str(v) for v in values))

    def _string_text(self, length, chars, pattern):
        if not length:
            return ""
        rng = self.rng
        pool = chars.replace(" ", "") or chars
        if pattern in ("all_min", "all_equal"):
            return pool[0] * length if pattern == "all_min" else rng.choice(pool) * length
        if pattern == "all_max":
            return pool[-1] * length
        if pattern == "alternating" and len(pool) > 1:
            return "".join(pool[0] if i % 2 == 0 else pool[-1] for i in range(length))
        if pattern in ("ascending", "descending"):
            text = "".join(sorted(rng.choice(pool) for _ in range(length)))
            return text if pattern == "ascending" else text[::-1]
        text = "".join(rng.choice(pool) for _ in range(length))
        if " " in chars and length > 2:      # printable: allow inner spaces, never at the ends
            text = text[0] + "".join(c if rng.random() > 0.15 else " " for c in text[1:-1]) + text[-1]
        return text

    def _string(self, item, scope):
        if item["length"] is not None:
            length = self.count(item["length"], scope)
        else:
            lo, hi = item["min_length"], item["max_length"]
            cap = min(hi, max(lo, self.recipe.get("count_cap", 60)))
            if cap < hi and self.recipe["scalar"] == "max":
                self.capped = True
            strategy = self.recipe["scalar"]
            length = {"min": lo, "max": cap, "mid": lo + (cap - lo) // 2}.get(
                strategy, self.rng.randint(lo, cap))
        sample = (self.recipe.get("sample") or {}).get(item["name"])
        if isinstance(sample, str) and len(sample) == length and length:
            chars = item["chars"].replace(" ", "") or item["chars"]
            text = "".join(c if self.rng.random() < 0.6 else self.rng.choice(chars) for c in sample)
        else:
            text = self._string_text(length, item["chars"], self.recipe["pattern"])
        self.emit(text)

    def _grid(self, item, scope):
        rows, cols = self.count(item["rows"], scope), self.count(item["cols"], scope)
        for _ in range(rows):
            if item["cell"] == "char":
                self.emit(self._string_text(cols, item["chars"], self.recipe["pattern"])
                          .replace(" ", item["chars"].strip()[:1] or "."))
            else:
                self.emit(" ".join(str(v) for v in self._values(
                    cols, item["min"], item["max"], self.recipe["pattern"], False)))

    def _edges(self, item, scope):
        n = scope[item["vertices"]]
        m = self.count(item["count"], scope)
        base = item["index_base"]
        rng = self.rng
        if n <= 0 and m > 0:
            raise _Infeasible("edges without vertices")
        if item["tree"] or (item["connected"] and m == n - 1):
            edges = self._tree(n, self.recipe["pattern"])
        else:
            edges = []
            if item["connected"]:
                if m < n - 1:
                    raise _Infeasible("too few edges for a connected graph")
                edges = self._tree(n, "random")
            seen = {self._key(u, v, item) for u, v in edges}
            limit = (n * (n - 1) if item["directed"] else n * (n - 1) // 2) \
                + (n if item["self_loops"] else 0)
            if not item["multi_edges"] and m > limit:
                raise _Infeasible("more edges than the graph rules allow")
            if item["multi_edges"] and n == 1 and not item["self_loops"] and m > 0:
                raise _Infeasible("a single vertex cannot have edges without self-loops")
            attempts = 0
            while len(edges) < m and attempts < 50 * (m + 1):
                attempts += 1
                u, v = rng.randrange(n), rng.randrange(n)
                if u == v and not item["self_loops"]:
                    continue
                key = self._key(u, v, item)
                if key in seen and not item["multi_edges"]:
                    continue
                seen.add(key)
                edges.append((u, v))
            if len(edges) < m:
                raise _Infeasible("could not place all edges")
        rng.shuffle(edges)
        for u, v in edges[:m]:
            line = f"{u + base} {v + base}"
            if item["weight"]:
                w = item["weight"]
                line += " " + str({"all_min": w["min"], "all_max": w["max"]}.get(
                    self.recipe["pattern"], rng.randint(w["min"], w["max"])))
            self.emit(line)

    @staticmethod
    def _key(u, v, item):
        return (u, v) if item["directed"] else (min(u, v), max(u, v))

    def _tree(self, n, pattern):
        if n <= 1:
            return []
        order = list(range(n))
        self.rng.shuffle(order)
        if pattern in ("ascending", "descending", "all_min"):     # path
            return [(order[i], order[i + 1]) for i in range(n - 1)]
        if pattern in ("all_max", "all_equal"):                   # star
            return [(order[0], order[i]) for i in range(1, n)]
        return [(order[i], order[self.rng.randrange(i)]) for i in range(1, n)]

    def _repeat(self, item, scope):
        count = self.count(item["count"], scope)
        for _ in range(count):
            inner = dict(scope)
            saved = self.recipe
            self.recipe = dict(saved, scalar="mixed")
            try:
                self.run(item["items"], inner)
            finally:
                self.recipe = saved


def _count_vars(items, found=None):
    """Variables used as sizes: 'linear' (array/string/edges/repeat) or 'square' (grid)."""
    found = {} if found is None else found
    for item in items:
        refs = []
        if item["type"] in ("array", "string"):
            refs = [(item.get("length"), "linear")]
        elif item["type"] == "edges":
            refs = [(item["count"], "linear"), (item["vertices"], "linear")]
        elif item["type"] == "grid":
            refs = [(item["rows"], "square"), (item["cols"], "square")]
        elif item["type"] == "repeat":
            refs = [(item["count"], "linear")]
            _count_vars(item["items"], found)
        for ref, kind in refs:
            if isinstance(ref, str):
                name = ref[:-2] if ref.endswith("-1") else ref
                if found.get(name) != "square":
                    found[name] = kind
    return found


def _recipes(schema, sample_values):
    scalars = list(schema["variables"])
    recipes = [
        {"category": "boundary_min", "scalar": "min", "pattern": "all_min"},
        {"category": "boundary_max", "scalar": "max", "pattern": "all_max"},
        {"category": "midpoint", "scalar": "mid", "pattern": "random"},
    ]
    if len(scalars) > 1:
        for name in scalars:
            recipes.append({"category": f"{name}_min", "scalar": "mid", "pattern": "random",
                            "overrides": {name: "min"}})
            recipes.append({"category": f"{name}_max", "scalar": "mid", "pattern": "random",
                            "overrides": {name: "max"}})
    for special in (0, 1, -1, 2):
        overrides = {n: special for n in scalars
                     if schema["variables"][n]["min"] < special < schema["variables"][n]["max"]}
        if overrides:
            recipes.append({"category": f"special_value_{special}", "scalar": "mid",
                            "pattern": "random", "overrides": overrides})
    for pattern in ("ascending", "descending", "all_equal", "alternating"):
        recipes.append({"category": f"pattern_{pattern}", "scalar": "mid", "pattern": pattern,
                        "count_cap": 12})
    recipes.append({"category": "small_size", "scalar": "min", "pattern": "random",
                    "overrides": {}, "count_cap": 3})
    if sample_values:
        # Keep the sample's sizes (so its arrays/strings line up) and re-draw everything else.
        sizes = _count_vars(schema["items"])
        for i in range(3):
            recipes.append({"category": "sample_mutation", "scalar": "random", "pattern": "random",
                            "overrides": {n: sample_values[n] for n in scalars
                                          if n in sizes and isinstance(sample_values.get(n), int)},
                            "sample": sample_values, "seed_offset": 100 + i})
    return recipes


def _preview(text, limit=120):
    return text if len(text) <= limit else text[:limit] + "..."


def generate_candidates(schema, sample_input=None, count=DEFAULT_COUNT, seed=42,
                        max_chars=MAX_CANDIDATE_CHARS):
    """{'method', 'candidates': [...], 'rejected': [...], 'stats': {...}} for a checked schema."""
    if type(count) is not int or not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count must be an integer between 1 and {MAX_COUNT}")

    sample_key = None
    sample_values = None
    if sample_input:
        sample_key = canonical_form(sample_input)
        check = validate_input(schema, sample_input)
        if check["valid"]:
            sample_values = check["values"]

    recipes = _recipes(schema, sample_values)
    rng_fill = random.Random(seed + 999)
    fill = 0
    while len(recipes) < count * 3 and fill < count * 3:
        fill += 1
        recipes.append({"category": "random", "scalar": "random",
                        "pattern": rng_fill.choice(_PATTERNS), "seed_offset": 1000 + fill,
                        "count_cap": rng_fill.choice([5, 20, 60])})

    candidates, rejected, seen = [], [], set()
    stats = {"recipes_tried": 0, "duplicates": 0, "infeasible": 0, "too_large": 0,
             "invalid": 0, "size_capped": 0}

    for index, recipe in enumerate(recipes):
        if len(candidates) >= count:
            break
        stats["recipes_tried"] += 1
        rng = random.Random(seed * 7919 + recipe.get("seed_offset", index))
        builder = _Builder(schema, recipe, rng, max_chars)
        try:
            builder.run(schema["items"], {})
        except _TooLarge:
            stats["too_large"] += 1
            rejected.append({"category": recipe["category"], "stage": "size",
                             "errors": [{"code": "INPUT_TOO_LARGE",
                                         "message": f"candidate exceeds {max_chars} characters"}]})
            continue
        except _Infeasible as error:
            stats["infeasible"] += 1
            rejected.append({"category": recipe["category"], "stage": "generation",
                             "errors": [{"code": "INFEASIBLE", "message": str(error)}]})
            continue
        text = "\n".join(builder.lines) + "\n"
        key = canonical_form(text)
        if key == sample_key:
            stats["duplicates"] += 1
            rejected.append({"category": recipe["category"], "stage": "dedupe",
                             "input_preview": _preview(text),
                             "errors": [{"code": "DUPLICATE_OF_SAMPLE",
                                         "message": "identical to the sample input"}]})
            continue
        if key in seen:
            stats["duplicates"] += 1
            continue
        check = validate_input(schema, text)
        if not check["valid"]:
            stats["invalid"] += 1
            rejected.append({"category": recipe["category"], "stage": "validation",
                             "input_preview": _preview(text), "errors": check["errors"]})
            continue
        seen.add(key)
        if builder.capped:
            stats["size_capped"] += 1
        candidates.append({
            "input": text,
            "category": recipe["category"] + ("_size_capped" if builder.capped else ""),
            "generation_method": METHOD,
            "status": "CANDIDATE",
            "validation": {"valid": True, "syntax_valid": True, "constraints_valid": True,
                           "semantic_valid": True},
        })

    return {"method": METHOD, "seed": seed, "candidates": candidates,
            "rejected": rejected, "stats": stats}

