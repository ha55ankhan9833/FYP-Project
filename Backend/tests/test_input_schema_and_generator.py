"""
Phases B/C: schema validation (syntax / constraints / semantic layers), legacy
compatibility, conservative inference and the deterministic baseline generator.
Pure Python: nothing is executed, no database is touched.
"""

import random
import unittest

from tests import fakes  # noqa: F401

import generic_test_inputs as legacy
from baseline_input_generator import MAX_COUNT, generate_candidates
from input_schema import SchemaError, check_schema, schema_from_legacy_constraints, validate_input
from input_schema_inference import infer_schema, parse_bounds

ARRAY = {"variables": {"N": {"min": 1, "max": 100000}},
         "items": [{"type": "ints", "vars": ["N"]},
                   {"type": "array", "name": "A", "length": "N", "min": -10 ** 9, "max": 10 ** 9}]}
STRING = {"items": [{"type": "string", "name": "S", "min_length": 1, "max_length": 50,
                     "charset": "lower"}]}
TREE = {"variables": {"N": {"min": 1, "max": 200000}},
        "items": [{"type": "ints", "vars": ["N"]},
                  {"type": "edges", "count": "N-1", "vertices": "N", "directed": False,
                   "self_loops": False, "multi_edges": False, "connected": True, "tree": True}]}
GRAPH = {"variables": {"N": {"min": 2, "max": 1000}, "M": {"min": 1, "max": 5000}},
         "items": [{"type": "ints", "vars": ["N", "M"]},
                   {"type": "edges", "count": "M", "vertices": "N", "directed": False,
                    "self_loops": False, "multi_edges": False, "connected": False,
                    "weight": {"min": 1, "max": 1000}}]}
MULTI = {"variables": {"T": {"min": 1, "max": 100}, "a": {"min": 0, "max": 10 ** 9},
                       "b": {"min": 0, "max": 10 ** 9}},
         "items": [{"type": "ints", "vars": ["T"]},
                   {"type": "repeat", "count": "T", "items": [{"type": "ints", "vars": ["a", "b"]}]}]}
GRID = {"variables": {"R": {"min": 1, "max": 50}, "C": {"min": 1, "max": 50}},
        "items": [{"type": "ints", "vars": ["R", "C"]},
                  {"type": "grid", "name": "G", "rows": "R", "cols": "C", "cell": "char",
                   "chars": ".#"}]}
RELATION = {"variables": {"N": {"min": 1, "max": 100}, "K": {"min": 1, "max": 100}},
            "items": [{"type": "ints", "vars": ["N", "K"]}],
            "relations": [{"left": "K", "op": "<=", "right": "N"}]}


def codes(result):
    return [e["code"] for e in result["errors"]]


class SchemaCheckTests(unittest.TestCase):
    def test_valid_schemas_accepted(self):
        for schema in (ARRAY, STRING, TREE, GRAPH, MULTI, GRID, RELATION):
            self.assertEqual(check_schema(schema)["version"], 1)

    def test_unknown_item_type_unsupported(self):
        with self.assertRaises(SchemaError) as ctx:
            check_schema({"items": [{"type": "polygon"}]})
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_INPUT_FORMAT")

    def test_unknown_keys_and_bad_ranges_invalid(self):
        bad = [{"items": [{"type": "ints", "vars": ["N"]}]},                     # undeclared
               {"variables": {"N": {"min": 5, "max": 1}}, "items": [{"type": "ints", "vars": ["N"]}]},
               {"items": [{"type": "array", "name": "A", "length": "N", "min": 0, "max": 1}]},  # unbound
               {"items": [{"type": "string", "name": "S", "min_length": 1, "max_length": 2,
                           "charset": "lower", "extra": 1}]},
               {"foo": 1, "items": [{"type": "string", "name": "S", "min_length": 1,
                                     "max_length": 2, "charset": "lower"}]}]
        for schema in bad:
            with self.assertRaises(SchemaError, msg=schema) as ctx:
                check_schema(schema)
            self.assertEqual(ctx.exception.code, "INVALID_INPUT_SCHEMA")

    def test_graph_semantics_must_be_explicit(self):
        edges = dict(GRAPH["items"][1])
        del edges["directed"]
        with self.assertRaises(SchemaError) as ctx:
            check_schema({"variables": GRAPH["variables"], "items": [GRAPH["items"][0], edges]})
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_INPUT_FORMAT")

    def test_unknown_charset_unsupported(self):
        with self.assertRaises(SchemaError) as ctx:
            check_schema({"items": [{"type": "string", "name": "S", "min_length": 1,
                                     "max_length": 2, "charset": "emoji"}]})
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_INPUT_FORMAT")

    def test_nested_repeat_unsupported(self):
        inner = {"type": "repeat", "count": "T", "items": [{"type": "ints", "vars": ["a"]}]}
        schema = {"variables": {"T": {"min": 1, "max": 3}, "a": {"min": 1, "max": 3}},
                  "items": [{"type": "ints", "vars": ["T"]},
                            {"type": "repeat", "count": "T", "items": [inner]}]}
        with self.assertRaises(SchemaError):
            check_schema(schema)


class ValidationTests(unittest.TestCase):
    def test_array(self):
        s = check_schema(ARRAY)
        self.assertTrue(validate_input(s, "3\n1 -2 3\n")["valid"])
        self.assertTrue(validate_input(s, "3\n1 -2 3")["valid"])            # missing final newline
        self.assertTrue(validate_input(s, "3\r\n1 -2 3\r\n")["valid"])      # CRLF
        self.assertEqual(codes(validate_input(s, "3\n1 2\n")), ["TOKEN_COUNT"])
        self.assertEqual(codes(validate_input(s, "3\n1 x 3\n")), ["NOT_INTEGER"])
        self.assertEqual(codes(validate_input(s, "3\n")), ["MISSING_LINE"])
        self.assertEqual(codes(validate_input(s, "1\n5\n6\n")), ["EXTRA_LINES"])
        r = validate_input(s, "2\n1 2000000000\n")
        self.assertTrue(r["syntax_valid"])
        self.assertFalse(r["constraints_valid"])
        self.assertEqual(codes(r), ["OUT_OF_RANGE"])
        self.assertEqual(codes(validate_input(s, "")), ["EMPTY_INPUT"])
        self.assertEqual(codes(validate_input(s, "1\n5\r6\n")), ["CARRIAGE_RETURN"])

    def test_array_semantics(self):
        schema = {"variables": {"N": {"min": 1, "max": 10}},
                  "items": [{"type": "ints", "vars": ["N"]},
                            {"type": "array", "name": "A", "length": "N", "min": 1, "max": 9,
                             "distinct": True, "order": "asc"}]}
        s = check_schema(schema)
        self.assertTrue(validate_input(s, "3\n1 4 9\n")["valid"])
        r = validate_input(s, "3\n4 1 1\n")
        self.assertTrue(r["syntax_valid"] and r["constraints_valid"])
        self.assertFalse(r["semantic_valid"])
        self.assertEqual(set(codes(r)), {"NOT_DISTINCT", "NOT_SORTED"})

    def test_string(self):
        s = check_schema(STRING)
        self.assertTrue(validate_input(s, "hello\n")["valid"])
        self.assertEqual(codes(validate_input(s, "Hello\n")), ["CHARSET"])
        self.assertEqual(codes(validate_input(s, "a" * 51 + "\n")), ["LENGTH_OUT_OF_RANGE"])
        self.assertEqual(codes(validate_input(s, "ab\ncd\n")), ["EXTRA_LINES"])
        printable = check_schema({"items": [{"type": "string", "name": "L", "min_length": 1,
                                             "max_length": 100, "charset": "printable"}]})
        self.assertTrue(validate_input(printable, "Red fish, blue FISH; red bird.\n")["valid"])

    def test_multi_line_repeat_and_grid(self):
        m = check_schema(MULTI)
        self.assertTrue(validate_input(m, "2\n1 2\n3 4\n")["valid"])
        self.assertEqual(codes(validate_input(m, "3\n1 2\n3 4\n")), ["MISSING_LINE"])
        g = check_schema(GRID)
        self.assertTrue(validate_input(g, "2 3\n#.#\n...\n")["valid"])
        self.assertEqual(codes(validate_input(g, "2 3\n#.#\n..\n")), ["ROW_LENGTH"])
        self.assertEqual(codes(validate_input(g, "1 3\n#x#\n")), ["CHARSET"])

    def test_tree_and_graph_semantics(self):
        t = check_schema(TREE)
        self.assertTrue(validate_input(t, "1\n")["valid"])
        self.assertTrue(validate_input(t, "4\n1 2\n2 3\n2 4\n")["valid"])
        r = validate_input(t, "4\n1 2\n2 1\n3 4\n")
        self.assertEqual(set(codes(r)), {"DUPLICATE_EDGE", "NOT_CONNECTED"})
        r = validate_input(t, "3\n1 2\n2 9\n")      # 9 is out of range, so vertex 3 is isolated
        self.assertEqual(codes(r), ["VERTEX_OUT_OF_RANGE", "NOT_CONNECTED"])
        self.assertFalse(r["constraints_valid"])
        g = check_schema(GRAPH)
        self.assertTrue(validate_input(g, "3 2\n1 2 5\n2 3 7\n")["valid"])
        self.assertEqual(codes(validate_input(g, "3 1\n2 2 5\n")), ["SELF_LOOP"])
        self.assertEqual(codes(validate_input(g, "3 1\n1 2 0\n")), ["OUT_OF_RANGE"])
        self.assertEqual(codes(validate_input(g, "3 1\n1 2\n")), ["TOKEN_COUNT"])

    def test_relations_are_semantic(self):
        s = check_schema(RELATION)
        self.assertTrue(validate_input(s, "5 3\n")["valid"])
        r = validate_input(s, "3 5\n")
        self.assertTrue(r["constraints_valid"])
        self.assertEqual(codes(r), ["RELATION"])


class LegacyCompatibilityTests(unittest.TestCase):
    """The schema path must give exactly the legacy generic_test_inputs verdicts."""

    def test_verdicts_match_legacy_rule(self):
        rng = random.Random(7)
        for constraints in ("1 <= N <= 1000000", "-100 < X < 100", "1 <= a, b <= 1000",
                            "1 <= n <= 10\n0 <= k <= 5\n2 <= m <= 3"):
            schema = schema_from_legacy_constraints(constraints)
            variables = legacy.parse_approved_value_constraints(constraints)
            samples = [c["input_data"] for c in legacy.generate_inputs(variables, random_count=10)]
            samples += ["", "x", "1 2 3 4", "1\n2", "+5", "-0", "99999999999", "1.5",
                        " ".join(str(rng.randint(-5, 2000)) for _ in variables)]
            for text in samples:
                try:
                    legacy.validate_inputs(variables, [{"input_data": text}])
                    expected = True
                except ValueError:
                    expected = False
                self.assertEqual(validate_input(schema, text)["valid"], expected, (constraints, text))

    def test_legacy_schema_rejects_unsupported_constraints(self):
        with self.assertRaises(ValueError):
            schema_from_legacy_constraints("1 <= |S| <= 100")


class InferenceTests(unittest.TestCase):
    def test_bound_parsing(self):
        b = parse_bounds("1 <= N <= 10^5. -10^9 <= A[i] <= 10^9; 1 ≤ |S| ≤ 2*10^5; "
                         "0 <= k <= 2n; 1 <= a, b <= 1e3")
        self.assertEqual(b["scalars"]["N"], (1, 100000))
        self.assertEqual(b["elements"]["A"], (-10 ** 9, 10 ** 9))
        self.assertEqual(b["lengths"]["S"], (1, 200000))
        self.assertEqual(b["scalars"]["a"], (1, 1000))
        self.assertNotIn("k", b["scalars"])                 # "2n" is not a numeric bound

    def test_array_inferred(self):
        r = infer_schema({"input_format": "The first line contains N. The second line contains A.",
                          "value_constraints": "1 <= N <= 100000; -10^9 <= A_i <= 10^9",
                          "sample_input": "3\n1 2 3\n"})
        self.assertTrue(r["supported"])
        self.assertEqual(r["shape"], "array")
        self.assertTrue(validate_input(r["schema"], "1\n-1000000000\n")["valid"])

    def test_string_charset_from_input_format(self):
        r = infer_schema({"problem_statement": "Uppercase letters are vowels...",
                          "input_format": "One nonempty alphanumeric string.",
                          "value_constraints": "1<=|S|<=200000", "sample_input": "A1B2\n"})
        self.assertTrue(r["supported"])
        self.assertTrue(validate_input(r["schema"], "123\n")["valid"])

    def test_degenerate_sample_not_guessed(self):
        r = infer_schema({"input_format": "The first line contains N. The next N-1 lines contain "
                                          "edges of a tree.",
                          "value_constraints": "1 <= N <= 200000", "sample_input": "1\n"})
        # either recognised as the tree it is, or refused -- never "one scalar line"
        self.assertTrue(not r["supported"] or r["shape"] == "graph")

    def test_unsupported_reports_reasons(self):
        r = infer_schema({"input_format": "A polygon.", "value_constraints": "",
                          "sample_input": "4\n0 0\n1 0\n1 1\n0 1\n"})
        self.assertFalse(r["supported"])
        self.assertTrue(r["reasons"])
        self.assertFalse(infer_schema({"sample_input": ""})["supported"])

    def test_undirected_graph_requires_stated_direction(self):
        problem = {"input_format": "First line n m, then m lines u v.",
                   "value_constraints": "2 <= n <= 100; 1 <= m <= 1000",
                   "sample_input": "3 2\n1 2\n2 3\n"}
        self.assertFalse(infer_schema(problem)["supported"])
        problem["problem_statement"] = "Given an undirected graph"
        r = infer_schema(problem)
        self.assertTrue(r["supported"])
        self.assertEqual(r["shape"], "graph")
        self.assertFalse(r["schema"]["items"][1]["self_loops"])


class GeneratorTests(unittest.TestCase):
    def all_schemas(self):
        return [check_schema(s) for s in (ARRAY, STRING, TREE, GRAPH, MULTI, GRID, RELATION)] + \
            [schema_from_legacy_constraints("1 <= a, b <= 1000")]

    def test_every_candidate_valid_unique_and_marked(self):
        for schema in self.all_schemas():
            out = generate_candidates(schema, count=20)
            self.assertTrue(out["candidates"], schema)
            texts = [c["input"] for c in out["candidates"]]
            self.assertEqual(len(texts), len(set(texts)))
            for c in out["candidates"]:
                self.assertTrue(validate_input(schema, c["input"])["valid"], c)
                self.assertEqual(c["status"], "CANDIDATE")
                self.assertEqual(c["generation_method"], "deterministic_baseline_v1")
                self.assertNotIn("expected_output", c)

    def test_deterministic(self):
        s = check_schema(GRAPH)
        self.assertEqual(generate_candidates(s, seed=3), generate_candidates(s, seed=3))
        self.assertNotEqual(generate_candidates(s, seed=3)["candidates"],
                            generate_candidates(s, seed=4)["candidates"])

    def test_boundaries_and_sample_exclusion(self):
        s = check_schema({"variables": {"N": {"min": 1, "max": 14}},
                          "items": [{"type": "ints", "vars": ["N"]}]})
        out = generate_candidates(s, sample_input="1\n", count=20)
        values = {int(c["input"]) for c in out["candidates"]}
        self.assertEqual(values, set(range(2, 15)))         # every other value; sample excluded
        self.assertIn("DUPLICATE_OF_SAMPLE",
                      [e["code"] for r in out["rejected"] for e in r["errors"]])

    def test_large_sizes_are_capped_and_labelled(self):
        out = generate_candidates(check_schema(ARRAY), sample_input="3\n1 2 3\n")
        self.assertTrue(any(c["category"].endswith("_size_capped") for c in out["candidates"]))
        self.assertTrue(all(len(c["input"]) <= 2000 for c in out["candidates"]))
        self.assertTrue(any(c["input"].startswith("1\n") for c in out["candidates"]))  # N minimum

    def test_sample_mutation_keeps_structure(self):
        out = generate_candidates(check_schema(ARRAY), sample_input="4\n5 6 7 8\n", count=50)
        mutations = [c for c in out["candidates"] if c["category"] == "sample_mutation"]
        self.assertTrue(mutations)
        self.assertTrue(all(c["input"].startswith("4\n") for c in mutations))

    def test_oversized_candidates_rejected_with_reason(self):
        schema = check_schema({"items": [{"type": "string", "name": "S", "min_length": 500,
                                          "max_length": 900, "charset": "lower"}]})
        out = generate_candidates(schema, count=5, max_chars=100)
        self.assertFalse(out["candidates"])
        self.assertIn("INPUT_TOO_LARGE", [r["errors"][0]["code"] for r in out["rejected"]])

    def test_tree_candidates_are_trees(self):
        out = generate_candidates(check_schema(TREE), count=20)
        for c in out["candidates"]:
            n = int(c["input"].split("\n")[0])
            self.assertEqual(c["input"].count("\n"), n)     # header + n-1 edges

    def test_count_bounds(self):
        with self.assertRaises(ValueError):
            generate_candidates(check_schema(STRING), count=MAX_COUNT + 1)


if __name__ == "__main__":
    unittest.main()
