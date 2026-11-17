"""
Verified-oracle test-case import (no C++ execution). All database access
is faked; nothing reaches SQL Server.
"""

import hashlib
import json
import unittest
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch

from tests.fakes import BACKEND, FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module
import oracle_test_case_service as svc
import secure_test_case_save as save_mod
import verified_oracles as vo
from equal_weight_test_scoring import calculate_equal_weight_score

PRIME_HASH = vo.PRIME_CHECK_V1.reference_sha256
FACTORIAL_HASH = "53b7d425901f831aae4dae9bfaeab839de9ec51415f319ce4943f43b07893993"
DATA = Path(BACKEND) / "fixtures" / "new_assessment_csc101"
GENERATION = json.loads((DATA / "07_generate_test_cases.json").read_text())["body"]


def preflight(hash_=PRIME_HASH, constraints="1 <= N <= 1000000"):
    return lambda a, t: {"source_hash": hash_, "reference_solution_id": 2,
                         "constraints": constraints, "assessment_id": a}


def run(**kw):
    saved = {}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return {"saved_count": len(kwargs["test_cases"]),
                "marks_per_test": "0.50", "test_marks_allocated": "5.00"}

    args = dict(assessment_id=5, teacher_id="BIIT235",
                oracle_id="prime_check_v1",
                additional_inputs=GENERATION["additional_inputs"],
                dry_run=True, preflight_fn=preflight(), save_fn=fake_save)
    args.update(kw)
    try:
        return svc.generate_from_verified_oracle(**args), saved
    except Exception as exc:
        return exc, saved


def transcription(n):
    prime = n >= 2
    d = 2
    while prime and d * d <= n:
        if n % d == 0:
            prime = False
        d += 1
    return "YES\n" if prime else "NO\n"


class OracleRegistryTests(unittest.TestCase):
    def test_known_values(self):
        expected = {1: "NO", 2: "YES", 3: "YES", 4: "NO", 25: "NO", 97: "YES",
                    7919: "YES", 994009: "NO", 999983: "YES", 1000000: "NO"}
        for n, out in expected.items():
            self.assertEqual(vo.PRIME_CHECK_V1.expected_output(str(n)), out + "\n")

    def test_two_algorithms_agree_on_prefix(self):
        for n in range(1, 20001):
            self.assertEqual(vo._prime_by_sieve((n,)),
                             vo._prime_by_miller_rabin((n,)), n)

    def test_unknown_oracle_rejected(self):
        for bad in ("prime_check_v2", "", None, 5, "__import__"):
            with self.assertRaises(vo.OracleError):
                vo.get_verified_oracle(bad)

    def test_domain_and_arity_enforced(self):
        o = vo.PRIME_CHECK_V1
        for bad in ("0", "1000001", "-7", "2 3"):
            with self.subTest(bad=bad), self.assertRaises(vo.OracleError):
                o.expected_output(bad)
        with self.assertRaises(vo.OracleError):
            o.check_domain([{"variable": "N", "minimum": 1, "maximum": 2_000_000}])
        with self.assertRaises(vo.OracleError):
            o.check_domain([{"variable": "a", "minimum": 1, "maximum": 9},
                            {"variable": "b", "minimum": 1, "maximum": 9}])

    def test_disagreement_aborts(self):
        broken = vo.VerifiedOracle(
            oracle_id="x", description="", reference_sha256=PRIME_HASH,
            variable_count=1, domain_minimum=1, domain_maximum=100,
            primary=vo._prime_by_sieve, secondary=lambda v: "YES\n")
        with self.assertRaisesRegex(vo.OracleError, "disagree"):
            broken.expected_output("4")


class Assessment5CasesTests(unittest.TestCase):
    def setUp(self):
        result, _ = run()
        self.assertNotIsInstance(result, Exception, result)
        self.cases = result["test_cases"]

    def test_exactly_10_unique_in_range(self):
        self.assertEqual(len(self.cases), 10)
        inputs = [int(c["input_data"]) for c in self.cases]
        self.assertEqual(len(set(inputs)), 10)
        self.assertTrue(all(1 <= n <= 1_000_000 for n in inputs))
        self.assertEqual([c["test_number"] for c in self.cases],
                         list(range(1, 11)))

    def test_matches_reviewed_migration_plan(self):
        plan = json.loads((DATA / "migration_to_10_tests.json").read_text())
        self.assertEqual([(c["input_data"], c["expected_output"])
                          for c in self.cases],
                         [(p["input_data"], p["expected_output"])
                          for p in plan["proposed_10"]])
        self.assertEqual(plan["generation_request_after_migration"]["body"]
                         ["additional_inputs"], GENERATION["additional_inputs"])

    def test_required_edge_cases_present(self):
        inputs = {int(c["input_data"]) for c in self.cases}
        for n in (1, 2, 4, 997 * 997, 999983):
            self.assertIn(n, inputs)

    def test_outputs_match_reference_transcription_and_plan(self):
        planned = json.loads((DATA / "planned_test_inputs.json").read_text())["cases"]
        for case, plan in zip(self.cases, planned):
            n = int(case["input_data"])
            self.assertEqual(case["expected_output"], transcription(n))
            self.assertEqual(case["input_data"], plan["input_data"])
            self.assertEqual(case["expected_output"],
                             plan["expected_output_python_oracle"])

    def test_balanced_outputs(self):
        outs = [c["expected_output"] for c in self.cases]
        self.assertEqual((outs.count("YES\n"), outs.count("NO\n")), (4, 6))

    def test_score_formula_uses_passed_over_10(self):
        for passed in range(11):
            expected = (Decimal(5) * passed / Decimal(10)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            self.assertEqual(Decimal(calculate_equal_weight_score(
                passed, 10, "5.00")["test_score"]), expected)


class OracleServiceSafetyTests(unittest.TestCase):
    def test_dry_run_never_saves(self):
        result, saved = run(dry_run=True)
        self.assertEqual(result["saved_count"], 0)
        self.assertEqual(saved, {})
        self.assertEqual(result["execution_mode"], "verified_python_oracle")

    def test_save_passes_server_computed_cases_and_ids(self):
        result, saved = run(dry_run=False)
        self.assertEqual(result["saved_count"], 10)
        self.assertEqual(saved["assessment_id"], 5)
        self.assertEqual(saved["teacher_id"], "BIIT235")
        self.assertEqual(saved["reference_solution_id"], 2)
        self.assertEqual(saved["expected_source_hash"], PRIME_HASH)
        self.assertEqual(len(saved["test_cases"]), 10)

    def test_old_15_case_request_now_refused(self):
        result, saved = run(additional_inputs=["3", "4", "25", "97", "7919",
                                               "994009", "999983"],
                            dry_run=False)
        self.assertIn("exceed the fixed 10", str(result))
        self.assertEqual(saved, {})

    def test_oracle_refused_for_other_reference_such_as_assessment_4(self):
        result, saved = run(preflight_fn=preflight(hash_=FACTORIAL_HASH),
                            dry_run=False)
        self.assertIsInstance(result, vo.OracleError)
        self.assertEqual(saved, {})

    def test_constraints_outside_verified_domain_refused(self):
        for constraints in ("1 <= N <= 2000000", "1 <= a, b <= 100",
                            "N is a string"):
            with self.subTest(constraints=constraints):
                result, saved = run(preflight_fn=preflight(
                    constraints=constraints), dry_run=False)
                self.assertIsInstance(result, ValueError)
                self.assertEqual(saved, {})

    def test_preflight_failures_propagate(self):
        for message in ("Assessment not found or access denied",
                        "Assessment already has test cases",
                        "Reference solution must be APPROVED",
                        "Constraints not approved"):
            def failing(a, t, m=message):
                raise ValueError(m)
            with self.subTest(message=message):
                result, saved = run(preflight_fn=failing, dry_run=False)
                self.assertEqual(str(result), message)
                self.assertEqual(saved, {})

    def test_client_cannot_inject_outputs_via_inputs(self):
        for bad in (["5 YES"], ["5\nYES"], ["1000001"], ["0"]):
            with self.subTest(bad=bad):
                result, saved = run(additional_inputs=bad, dry_run=False)
                self.assertIsInstance(result, ValueError)
                self.assertEqual(saved, {})

    def test_argument_validation(self):
        for kw in ({"additional_inputs": [str(n) for n in range(10, 21)]},
                   {"dry_run": "no"}, {"teacher_id": ""}, {"assessment_id": 0}):
            with self.subTest(kw=kw):
                result, saved = run(**kw)
                self.assertIsInstance(result, ValueError)


class SecureSaveWithOracleCasesTests(unittest.TestCase):
    """Run the REAL secure save on the 10 oracle cases with a fake DB."""

    def responder(self, existing=0, ref_hash=PRIME_HASH):
        source = (Path(BACKEND) / "fixtures/reference_solutions/"
                  "prime_check_reference.cpp").read_bytes().decode()
        draft = json.dumps({"finalized": True,
                            "approval": {"value": True, "code": True},
                            "constraints": [{"category": "value",
                                             "review_status": "ACCEPTED",
                                             "text": "1 <= N <= 1000000"}]})

        def respond(sql, params):
            if sql.startswith("SELECT total_marks, value_constraints"):
                return [("10.00", "1 <= N <= 1000000", "", "50.00")], -1
            if "FROM dbo.ConstraintReviewDraft" in sql:
                return [(draft, True)], -1
            if sql.startswith("SELECT value_constraints, code_constraints"):
                return [("1 <= N <= 1000000", "")], -1
            if "FROM dbo.ReferenceSolution" in sql:
                return [(source, ref_hash, "cpp", "APPROVED")], -1
            if "FROM dbo.TestCase" in sql:
                return [(existing,)], -1
            if sql.startswith("INSERT INTO dbo.TestCase"):
                return [], 1
            raise AssertionError(sql)
        return respond

    def save(self, conn, cases):
        with patch.object(save_mod, "get_connection", return_value=conn):
            try:
                return save_mod.save_generated_test_cases_secure(
                    5, "BIIT235", 2, PRIME_HASH, cases)
            except Exception as exc:
                return exc

    def cases(self):
        result, _ = run()
        return result["test_cases"]

    def test_atomic_insert_of_10_hidden_equal_rows(self):
        conn = FakeConnection(self.responder())
        r = self.save(conn, self.cases())
        inserts = [p for s, p in conn.executed
                   if s.startswith("INSERT INTO dbo.TestCase")]
        self.assertEqual(r["saved_count"], 10)
        self.assertEqual(len(inserts), 10)
        self.assertTrue(all(p[0] == 5 for p in inserts))       # assessment 5 only
        self.assertTrue(all(p[4] == "hidden" for p in inserts))
        self.assertEqual({p[7] for p in inserts}, {Decimal("0.50")})
        self.assertEqual(conn.commits, 1)

    def test_save_refused_while_old_rows_exist(self):
        # Assessment 5 currently holds 15 rows: a 10-case save is refused
        # until the approved migration removes them.
        conn = FakeConnection(self.responder(existing=15))
        self.assertIn("already has test cases", str(self.save(conn, self.cases())))
        self.assertFalse(conn.statements("INSERT"))

    def test_size_limits(self):
        big = self.cases()
        big[0] = dict(big[0], expected_output="Y" * (64 * 1024 + 1))
        conn = FakeConnection(self.responder())
        self.assertIsInstance(self.save(conn, big), ValueError)
        self.assertFalse(conn.executed)
        many = [dict(c, test_number=i + 1, input_data=str(i + 1))
                for i, c in enumerate(self.cases() * 4)]
        self.assertIsInstance(self.save(FakeConnection(self.responder()), many),
                              ValueError)


class OraclePathIsolationTests(unittest.TestCase):
    EXECUTION_MODULES = {"cpp_runner", "cpp_compiler", "cpp_compilation_service",
                         "test_case_pipeline", "test_case_generation_orchestrator",
                         "secure_execution_gate", "subprocess",
                         "submission_compilation_orchestrator", "os"}

    def imports_of(self, module):
        import ast
        tree = ast.parse((Path(BACKEND) / f"{module}.py").read_text("utf-8-sig"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_transitive_oracle_chain_has_no_execution_imports(self):
        seen, todo = set(), ["oracle_test_case_service"]
        local = {p.stem for p in Path(BACKEND).glob("*.py")}
        while todo:
            module = todo.pop()
            if module in seen:
                continue
            seen.add(module)
            imports = self.imports_of(module)
            self.assertFalse(imports & self.EXECUTION_MODULES,
                             f"{module} imports {imports & self.EXECUTION_MODULES}")
            todo += [m for m in imports if m in local]
        self.assertIn("verified_oracles", seen)
        self.assertIn("test_case_builder", seen)
        self.assertIn("secure_test_case_save", seen)


flask_app = app_module.app
flask_app.config["TESTING"] = True
URL = "/api/assessments/5/generate-test-cases/oracle"


def headers(role="teacher", identity="BIIT235"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity,
                                  additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class OracleRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_requires_teacher(self):
        self.assertEqual(self.client.post(URL, json={"oracle_id": "x"})
                         .status_code, 401)
        self.assertEqual(self.client.post(URL, json={"oracle_id": "x"},
                                          headers=headers("student")).status_code, 403)

    def test_body_cannot_carry_outputs_or_ids(self):
        for body in ({}, {"oracle_id": "prime_check_v1", "test_cases": []},
                     {"oracle_id": "prime_check_v1", "expected_outputs": ["YES"]},
                     {"oracle_id": "prime_check_v1", "assessment_id": 4},
                     {"oracle_id": "prime_check_v1", "additional_inputs": "97"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(URL, json=body,
                                                  headers=headers()).status_code, 400)

    def test_dry_run_is_default_and_ids_from_url_and_jwt(self):
        with patch.object(svc, "generate_from_verified_oracle",
                          return_value={"dry_run": True}) as m:
            r = self.client.post(URL, json={"oracle_id": "prime_check_v1"},
                                 headers=headers(identity="BIIT235"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m.call_args.kwargs["dry_run"], True)
        self.assertEqual(m.call_args.kwargs["assessment_id"], 5)
        self.assertEqual(m.call_args.kwargs["teacher_id"], "BIIT235")

    def test_error_mapping(self):
        for exc, code in ((ValueError("Assessment not found or access denied"), 404),
                          (ValueError("Assessment already has test cases"), 409),
                          (vo.OracleError("Unknown or unverified oracle"), 400),
                          (RuntimeError("boom"), 500)):
            with self.subTest(exc=exc), patch.object(
                    svc, "generate_from_verified_oracle", side_effect=exc):
                r = self.client.post(URL, json={"oracle_id": "prime_check_v1"},
                                     headers=headers())
                self.assertEqual(r.status_code, code)

    def test_end_to_end_route_with_faked_preflight_and_save(self):
        real = svc.generate_from_verified_oracle
        saved = {}

        def wrapped(**kwargs):
            return real(**kwargs, preflight_fn=preflight(),
                        save_fn=lambda **k: (saved.update(k) or {
                            "saved_count": 10, "marks_per_test": "0.50",
                            "test_marks_allocated": "5.00"}))

        with patch.object(svc, "generate_from_verified_oracle", side_effect=wrapped):
            preview = self.client.post(URL, json={"oracle_id": "prime_check_v1",
                                                  **GENERATION}, headers=headers())
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(len(preview.get_json()["data"]["test_cases"]), 10)
            self.assertEqual(saved, {})
            r = self.client.post(URL, json={"oracle_id": "prime_check_v1",
                                            "dry_run": False, **GENERATION},
                                 headers=headers())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(saved["test_cases"]), 10)

    def test_random_count_rejected(self):
        r = self.client.post(URL, json={"oracle_id": "prime_check_v1",
                                        "random_count": 3}, headers=headers())
        self.assertEqual(r.status_code, 400)
        self.assertIn("exactly 10", r.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
