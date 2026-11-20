"""
max_min_v1 verified Python oracle and the structured oracle generation path.
No C++ is compiled or executed; no database is used (fake preflight/save).
"""

import copy
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from flask_jwt_extended import create_access_token

import app as app_module
import assessment_readiness_service as readiness
import oracle_test_case_service as ots
import verified_oracles as vo
from input_schema import check_schema, validate_input
from test_case_builder import StructuredInputError
from test_case_policy import REQUIRED_TEST_CASE_COUNT
from test_case_validator import structured_input_key

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "max_min_reference.cpp"
RAW_SCHEMA = {"version": 1, "variables": {"N": {"min": 1, "max": 100}},
              "items": [{"type": "ints", "vars": ["N"]},
                        {"type": "array", "name": "A", "length": "N",
                         "min": -1000, "max": 1000}],
              "relations": []}
SCHEMA = check_schema(RAW_SCHEMA)
ORACLE = vo.MAX_MIN_V1
CONSTRAINTS = "1 <= N <= 100\n-1000 <= A[i] <= 1000"


def schema_with(**changes):
    raw = copy.deepcopy(RAW_SCHEMA)
    for path, value in changes.items():
        target = raw
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[int(key)] if key.isdigit() else target[key]
        target[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = value
    return check_schema(raw)


def preflight(source_hash=ORACLE.reference_sha256, schema=SCHEMA, constraints=CONSTRAINTS):
    def fn(assessment_id, teacher_id):
        if teacher_id != "T1":
            raise ValueError("Assessment not found or access denied")
        return {"assessment_id": assessment_id, "reference_solution_id": 3,
                "source_code": "(never used)", "source_hash": source_hash,
                "constraints": constraints, "code_constraints": "",
                "input_schema": schema, "ready": True}
    return fn


def generate(oracle_id="max_min_v1", additional=None, dry_run=True, teacher="T1", **pre):
    saved = []

    def save(**kwargs):
        saved.append(kwargs)
        return {"saved_count": len(kwargs["test_cases"]), "marks_per_test": "0.50",
                "test_marks_allocated": "5.00"}
    result = ots.generate_from_verified_oracle(
        7, teacher, oracle_id, additional_inputs=additional, dry_run=dry_run,
        preflight_fn=preflight(**pre), save_fn=save)
    return result, saved


class OracleUnitTests(unittest.TestCase):

    def test_registered_and_bound_to_reviewed_reference(self):
        self.assertIs(vo.get_verified_oracle("max_min_v1"), ORACLE)
        self.assertEqual(hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
                         ORACLE.reference_sha256)
        self.assertNotIn(b"\r", REFERENCE.read_bytes())            # LF bytes = stable hash

    def test_output_is_max_then_min(self):
        self.assertEqual(ORACLE.expected_output(SCHEMA, "5\n3 -2 7 0 4\n"), "7\n-2\n")
        self.assertEqual(ORACLE.expected_output(SCHEMA, "1\n-1000\n"), "-1000\n-1000\n")
        self.assertEqual(ORACLE.expected_output(SCHEMA, "3\n1000 1000 1000\n"),
                         "1000\n1000\n")

    def test_algorithms_are_independent_and_agree(self):
        self.assertIsNot(ORACLE.primary, ORACLE.secondary)
        for values in ([5], [2, 9], [9, 2], [-3, -3, -7], list(range(-50, 51))):
            self.assertEqual(ORACLE.primary(values), ORACLE.secondary(values))

    def test_disagreement_aborts(self):
        broken = vo.CountPrefixedArrayOracle(
            **{**{f: getattr(ORACLE, f) for f in ORACLE.__dataclass_fields__},
               "secondary": lambda values: "0\n0\n"})
        with self.assertRaises(vo.OracleError):
            broken.expected_output(SCHEMA, "2\n4 5\n")

    def test_input_is_schema_validated_before_any_algorithm_runs(self):
        calls = []
        spy = vo.CountPrefixedArrayOracle(
            **{**{f: getattr(ORACLE, f) for f in ORACLE.__dataclass_fields__},
               "primary": lambda v: calls.append(v) or "x",
               "secondary": lambda v: calls.append(v) or "x"})
        for bad in ("3\n1 2\n", "2\n1001 0\n", "0\n\n", "101\n" + " ".join(["1"] * 101) + "\n",
                    "2\n1 x\n", "", "2\n1 2\n3\n"):
            with self.subTest(bad=bad), self.assertRaises(vo.OracleError):
                spy.expected_output(SCHEMA, bad)
        self.assertEqual(calls, [])

    def test_schema_shape_and_domain_enforced(self):
        ORACLE.check_schema(SCHEMA)
        ORACLE.check_schema(schema_with(**{"variables.N.max": 50, "items.1.min": -10}))
        for bad in (schema_with(**{"variables.N.max": 200}),
                    schema_with(**{"items.1.max": 5000}),
                    schema_with(**{"items.1.min": -1001}),
                    schema_with(**{"items.1.distinct": True}),
                    schema_with(**{"items.1.order": "asc"}),
                    check_schema({"version": 1, "variables": {"N": {"min": 1, "max": 10}},
                                  "items": [{"type": "ints", "vars": ["N"]}],
                                  "relations": []}),
                    None):
            with self.subTest(bad=bad), self.assertRaises(vo.OracleError):
                ORACLE.check_schema(bad)

    def test_unknown_oracle(self):
        for name in ("max_min_v2", "", None, "MAX_MIN_V1"):
            with self.subTest(name=name), self.assertRaises(vo.OracleError):
                vo.get_verified_oracle(name)


class StructuredGenerationTests(unittest.TestCase):

    def test_dry_run_returns_ten_unique_verified_cases(self):
        result, saved = generate()
        cases = result["test_cases"]
        self.assertEqual(saved, [])
        self.assertEqual(result["generated_count"], REQUIRED_TEST_CASE_COUNT)
        self.assertEqual(len(cases), REQUIRED_TEST_CASE_COUNT)
        self.assertEqual(len({structured_input_key(c["input_data"]) for c in cases}), 10)
        self.assertEqual(result["execution_mode"], "verified_python_oracle")
        self.assertEqual(result["input_format"], "approved_input_schema")
        for case in cases:
            self.assertTrue(validate_input(SCHEMA, case["input_data"])["valid"])
            values = [int(t) for t in case["input_data"].split()[1:]]
            self.assertEqual(case["expected_output"], f"{max(values)}\n{min(values)}\n")

    def test_teacher_cases_first_and_exact_text(self):
        teacher = ["3\n5 5 5\n", "4\n-1 -7 -3 -2\n"]
        result, _ = generate(additional=teacher)
        cases = result["test_cases"]
        self.assertEqual([c["input_data"] for c in cases[:2]], teacher)
        self.assertEqual([c["category"] for c in cases[:2]], ["teacher_specified"] * 2)
        self.assertEqual(cases[1]["expected_output"], "-1\n-7\n")

    def test_invalid_teacher_case_is_rejected_with_details(self):
        with self.assertRaises(StructuredInputError) as ctx:
            generate(additional=["3\n1 2\n", "2\n5 5000\n"])
        self.assertEqual([d["index"] for d in ctx.exception.details], [0, 1])

    def test_save_is_structured_and_gets_the_same_cases(self):
        result, saved = generate(dry_run=False)
        self.assertEqual(len(saved), 1)
        self.assertIs(saved[0]["structured"], True)
        self.assertEqual(saved[0]["expected_source_hash"], ORACLE.reference_sha256)
        self.assertEqual(len(saved[0]["test_cases"]), 10)
        self.assertEqual(result["saved_count"], 10)
        self.assertNotIn("test_cases", result)

    def test_deterministic(self):
        first, _ = generate()
        second, _ = generate()
        self.assertEqual(first["test_cases"], second["test_cases"])

    def test_unapproved_reference_hash_refused(self):
        with self.assertRaises(vo.OracleError):
            generate(source_hash="0" * 64)
        other = hashlib.sha256(b"int main(){}").hexdigest()
        with self.assertRaises(vo.OracleError):
            generate(source_hash=other)

    def test_unrelated_problems_cannot_use_it(self):
        with self.assertRaises(vo.OracleError):                      # legacy assessment
            generate(schema=None, constraints="1 <= N <= 100")
        with self.assertRaises(vo.OracleError):                      # wider domain
            generate(schema=schema_with(**{"items.1.max": 100000}))
        string_schema = check_schema({"version": 1, "variables": {"N": {"min": 1, "max": 5}},
                                      "items": [{"type": "ints", "vars": ["N"]},
                                                {"type": "string", "name": "S", "length": "N",
                                                 "charset": "lower"}], "relations": []})
        with self.assertRaises(vo.OracleError):
            generate(schema=string_schema)

    def test_prime_oracle_still_refuses_structured(self):
        with self.assertRaises(vo.OracleError):
            generate(oracle_id="prime_check_v1",
                     source_hash=vo.PRIME_CHECK_V1.reference_sha256)

    def test_ownership_enforced_by_preflight(self):
        with self.assertRaises(ValueError):
            generate(teacher="T2")

    def test_bad_arguments(self):
        for kwargs in ({"dry_run": "yes"}, {"oracle_id": "nope"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate(**kwargs)


class ReadinessOracleTests(unittest.TestCase):

    def test_structured_assessment_resolves_to_max_min(self):
        source = readiness._output_source(ORACLE.reference_sha256, SCHEMA, False,
                                          frozenset(), vo.VERIFIED_ORACLES)
        self.assertEqual(source, {"source": "VERIFIED_ORACLE",
                                  "endpoint": "generate-test-cases/oracle",
                                  "oracle_id": "max_min_v1"})

    def test_wrong_shape_or_hash_is_unavailable(self):
        self.assertIsNone(readiness._output_source(
            ORACLE.reference_sha256, schema_with(**{"items.1.max": 5000}), False,
            frozenset(), vo.VERIFIED_ORACLES))
        self.assertIsNone(readiness._output_source(
            ORACLE.reference_sha256, None, False, frozenset(), vo.VERIFIED_ORACLES))
        self.assertIsNone(readiness._output_source(
            "1" * 64, SCHEMA, False, frozenset(), vo.VERIFIED_ORACLES))


flask_app = app_module.app
flask_app.config["TESTING"] = True
URL = "/api/assessments/7/generate-test-cases/oracle"


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity, additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class OracleRouteTests(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()

    def post(self, body, **kw):
        return self.client.post(URL, json=body, **kw)

    def test_auth(self):
        self.assertEqual(self.post({"oracle_id": "max_min_v1"}).status_code, 401)
        self.assertEqual(self.post({"oracle_id": "max_min_v1"},
                                   headers=headers(role="student")).status_code, 403)

    def test_client_cannot_send_outputs_or_identity(self):
        for extra in ({"expected_outputs": ["1\n1\n"]}, {"teacher_id": "T2"},
                      {"test_cases": []}, {"random_count": 3}):
            with self.subTest(extra=extra):
                r = self.post({"oracle_id": "max_min_v1", **extra}, headers=headers())
                self.assertEqual(r.status_code, 400)

    def test_preview_and_save_use_token_identity(self):
        def no_save(**_kwargs):
            raise AssertionError("dry run must not save")
        real = ots.generate_from_verified_oracle
        with patch.object(real, "__defaults__", (None, True, preflight(), no_save)), \
                patch.object(ots, "generate_from_verified_oracle", wraps=real) as spy:
            r = self.post({"oracle_id": "max_min_v1"}, headers=headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["generated_count"], 10)
        self.assertEqual(spy.call_args.kwargs["teacher_id"], "T1")

    def test_invalid_custom_case_gets_details(self):
        err = StructuredInputError("1 additional input(s) are not valid",
                                   [{"index": 0, "errors": [{"code": "TOKEN_COUNT"}]}])
        with patch.object(ots, "generate_from_verified_oracle", side_effect=err):
            r = self.post({"oracle_id": "max_min_v1", "additional_inputs": ["3\n1 2\n"]},
                          headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "INVALID_TEST_INPUT"))
        self.assertEqual(r.get_json()["errors"][0]["index"], 0)

    def test_retry_after_save_is_409_not_a_second_set(self):
        with patch.object(ots, "generate_from_verified_oracle",
                          side_effect=ValueError("Assessment already has test cases")):
            r = self.post({"oracle_id": "max_min_v1", "dry_run": False}, headers=headers())
        self.assertEqual(r.status_code, 409)

    def test_wrong_reference_is_400(self):
        with patch.object(ots, "generate_from_verified_oracle",
                          side_effect=vo.OracleError("not verified")):
            r = self.post({"oracle_id": "max_min_v1"}, headers=headers())
        self.assertEqual(r.status_code, 400)

    def test_not_owner_is_404(self):
        with patch.object(ots, "generate_from_verified_oracle",
                          side_effect=ValueError("Assessment not found or access denied")):
            r = self.post({"oracle_id": "max_min_v1"}, headers=headers(identity="T2"))
        self.assertEqual(r.status_code, 404)


class NoExecutionTests(unittest.TestCase):

    def test_oracle_modules_import_no_compiler_or_runner(self):
        for name in ("verified_oracles.py", "oracle_test_case_service.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            for forbidden in ("cpp_compiler", "cpp_runner", "compile_cpp", "host_runner",
                              "subprocess", "sandbox_runner", "get_secure_sandbox"):
                with self.subTest(module=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_execution_gate_still_disabled(self):
        import secure_execution_gate
        self.assertIsNone(secure_execution_gate.get_secure_sandbox())

    def test_reference_not_on_host_allowlist(self):
        from test_case_generation_service import load_trusted_reference_hashes
        self.assertNotIn(ORACLE.reference_sha256, load_trusted_reference_hashes())


if __name__ == "__main__":
    unittest.main()
