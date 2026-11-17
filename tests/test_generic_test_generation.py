"""
Generic test-case generation: parser, generator, pipeline, trust policy,
orchestrator wiring, secure save, and routes. All mocked - no database,
no compiler, no executable is run.
"""

import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.fakes import FakeConnection, BACKEND

from flask_jwt_extended import create_access_token

import app as app_module
import generic_test_inputs as gti
import secure_test_case_save as save_mod
import test_case_generation_orchestrator as orch
import test_case_generation_service as gen_service
from test_case_pipeline import build_test_cases, merge_additional_inputs
from test_case_validator import validate_generated_test_cases
from test_input_generator import generate_integer_test_inputs

FACTORIAL_HASH = "53b7d425901f831aae4dae9bfaeab839de9ec51415f319ce4943f43b07893993"
UNTRUSTED_HASH = "f" * 64


class ConstraintParserTests(unittest.TestCase):
    def test_single_and_exclusive_bounds(self):
        self.assertEqual(gti.parse_approved_value_constraints("0 < N < 50"),
                         [{"variable": "N", "minimum": 1, "maximum": 49}])

    def test_multi_line_and_comma_lists_keep_order(self):
        v = gti.parse_approved_value_constraints(
            "1 <= a, b <= 1000\n-5 <= K <= 5")
        self.assertEqual([x["variable"] for x in v], ["a", "b", "K"])
        self.assertEqual(v[2]["minimum"], -5)

    def test_fails_closed_on_unsupported_text(self):
        for bad in ["1 <= N <= 100 and N is even", "recursion_required",
                    "N is a string", "1 <= N <= 100\nUse arrays", "",
                    "1 <= N <= 1e5"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gti.parse_approved_value_constraints(bad)

    def test_rejects_contradictions_and_limits(self):
        for bad in ["5 <= N <= 1", "1 <= N <= 5\n1 <= n <= 9",
                    "1 <= a, b, c, d, e <= 9",
                    "1 <= N <= 10000000000000000000"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gti.parse_approved_value_constraints(bad)


class InputGeneratorTests(unittest.TestCase):
    def test_single_variable_identical_to_legacy_generator(self):
        legacy = generate_integer_test_inputs(1, 100, random_count=5)
        new = gti.generate_inputs(
            gti.parse_approved_value_constraints("1 <= N <= 100"), 5)
        self.assertEqual(new, legacy)   # Assessment 4 behaviour preserved

    def test_multi_variable_boundaries_unique_and_valid(self):
        v = gti.parse_approved_value_constraints("1 <= a, b <= 1000")
        cases = gti.generate_inputs(v, random_count=5)
        inputs = [c["input_data"] for c in cases]
        self.assertIn("1 1", inputs)
        self.assertIn("1000 1000", inputs)
        self.assertIn("1 500", inputs)       # a at minimum, b midpoint
        self.assertEqual(len(inputs), len(set(inputs)))
        gti.validate_inputs(v, cases)

    def test_tiny_domain_does_not_loop(self):
        v = gti.parse_approved_value_constraints("0 <= a, b <= 1")
        cases = gti.generate_inputs(v, random_count=20)
        self.assertLessEqual(len(cases), 4)

    def test_validator_backwards_compatible_keys(self):
        r = validate_generated_test_cases(
            "1 <= N <= 100", [{"input_data": "1"}, {"input_data": "100"}])
        self.assertEqual((r["minimum"], r["maximum"]), (1, 100))

    def test_validator_rejections(self):
        for cases in ([{"input_data": "101"}], [{"input_data": "1 2"}],
                      [{"input_data": "x"}],
                      [{"input_data": "5"}, {"input_data": " 5 "}]):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                validate_generated_test_cases("1 <= N <= 100", cases)


def fake_batch(fn, calls=None):
    def run(stdins):
        if calls is not None:
            calls.append(list(stdins))
        return [fn(s.strip()) for s in stdins]
    return run


class PipelineTests(unittest.TestCase):
    def test_outputs_come_from_reference_and_run_twice(self):
        calls = []
        cases = build_test_cases("1 <= N <= 10",
                                 fake_batch(lambda s: f"{int(s) * 2}\n", calls))
        self.assertEqual(len(cases), 10)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c["expected_output"] == f"{int(c['input_data']) * 2}\n"
                            for c in cases))
        self.assertEqual([c["test_number"] for c in cases],
                         list(range(1, len(cases) + 1)))

    def test_non_deterministic_reference_refused(self):
        counter = iter(range(1000))
        with self.assertRaisesRegex(RuntimeError, "non-deterministic"):
            build_test_cases("1 <= N <= 10",
                             fake_batch(lambda s: f"{next(counter)}\n"))

    def test_empty_output_refused(self):
        with self.assertRaisesRegex(RuntimeError, "empty output"):
            build_test_cases("1 <= N <= 10", fake_batch(lambda s: "  \n"))

    def test_wrong_result_count_refused(self):
        with self.assertRaises(RuntimeError):
            build_test_cases("1 <= N <= 10", lambda stdins: ["1\n"])

    def test_additional_inputs_validated_and_deduplicated(self):
        v = gti.parse_approved_value_constraints("1 <= N <= 100")
        generated = gti.generate_inputs(v, random_count=0)
        merged = merge_additional_inputs(v, generated, ["97", " 1 ", "50"])
        extra = [c for c in merged if c["category"] == "teacher_specified"]
        self.assertEqual([c["input_data"] for c in extra], ["97"])  # 1, 50 generated
        for bad in (["101"], ["7", "7"], ["abc"], [""], "97", ["1"] * 21):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                merge_additional_inputs(v, generated, bad)


class TrustPolicyTests(unittest.TestCase):
    def test_sandbox_allows_any_approved_reference(self):
        self.assertEqual(gen_service.choose_execution_mode(
            UNTRUSTED_HASH, True, frozenset()), "sandbox")

    def test_host_only_for_reviewed_hash(self):
        self.assertEqual(gen_service.choose_execution_mode(
            FACTORIAL_HASH, False), "trusted_host")
        with self.assertRaisesRegex(ValueError, "not been reviewed"):
            gen_service.choose_execution_mode(UNTRUSTED_HASH, False)

    def test_allowlist_file_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.json"
            path.write_text(json.dumps({"references": [
                {"sha256": "a" * 64}, {"sha256": "NOT-A-HASH"}, "junk"]}))
            hashes = gen_service.load_trusted_reference_hashes(path)
            self.assertIn("a" * 64, hashes)
            self.assertIn(FACTORIAL_HASH, hashes)       # built-in kept
            self.assertNotIn("NOT-A-HASH", hashes)
            self.assertEqual(gen_service.load_trusted_reference_hashes(
                Path(d) / "missing.json"), gen_service.TRUSTED_REFERENCE_HASHES)

    def test_allowlist_contains_only_owner_approved_factorial(self):
        self.assertEqual(gen_service.load_trusted_reference_hashes(),
                         frozenset({FACTORIAL_HASH}))
        source = (Path(BACKEND) / "fixtures/reference_solutions/"
                  "prime_check_reference.cpp").read_bytes()
        with self.assertRaisesRegex(ValueError, "not been reviewed"):
            gen_service.choose_execution_mode(
                hashlib.sha256(source).hexdigest(), sandbox_available=False)


class NoHardCodedIdsTests(unittest.TestCase):
    def test_generation_sources_have_no_fixed_assessment_ids(self):
        for name in ("test_case_generation_orchestrator.py",
                     "secure_test_case_save.py",
                     "test_case_generation_service.py",
                     "test_case_pipeline.py"):
            text = (Path(BACKEND) / name).read_text(encoding="utf-8")
            for forbidden in ("assessment_id != 4", "REVIEWED_ASSESSMENT_ID",
                              "reference_solution_id != 1",
                              '"1 <= N <= 100"'):
                self.assertNotIn(forbidden, text, f"{forbidden} in {name}")


class OrchestratorTests(unittest.TestCase):
    def preflight(self, digest, constraints="1 <= N <= 10"):
        return {"assessment_id": 57, "reference_solution_id": 9,
                "source_code": "SRC", "source_hash": digest,
                "constraints": constraints, "code_constraints": "",
                "ready": True}

    def run_orch(self, digest, sandbox=None, constraints="1 <= N <= 10",
                 reviewed=True):
        seen = {}

        def fake_save(**kwargs):
            seen["save"] = kwargs
            return {"saved_count": len(kwargs["test_cases"]),
                    "marks_per_test": "0.50", "test_marks_allocated": "5.00"}

        def fake_host(source, cons, extra=None):
            seen["host"] = True
            return build_test_cases(cons, fake_batch(lambda s: "ok\n"), extra)

        digest_src = hashlib.sha256(b"SRC").hexdigest() if digest is None else digest
        with patch.object(orch, "validate_generation_preflight",
                          side_effect=lambda a, t: (seen.setdefault("pre", (a, t)),
                                                    self.preflight(digest_src, constraints))[1]), \
             patch.object(orch, "get_secure_sandbox", return_value=sandbox), \
             patch.object(orch, "_generate_on_host", side_effect=fake_host), \
             patch.object(orch, "save_generated_test_cases_secure", side_effect=fake_save), \
             patch.object(gen_service, "load_trusted_reference_hashes",
                          return_value=(frozenset({hashlib.sha256(b"SRC").hexdigest()})
                                        if reviewed else frozenset())):
            try:
                return orch.generate_and_save_test_cases(57, "T9"), seen
            except Exception as exc:
                return exc, seen

    def test_any_owned_assessment_with_reviewed_reference(self):
        result, seen = self.run_orch(None)
        self.assertEqual(result["execution_mode"], "trusted_host")
        self.assertEqual(seen["pre"], (57, "T9"))
        self.assertEqual(seen["save"]["assessment_id"], 57)
        self.assertEqual(seen["save"]["teacher_id"], "T9")
        self.assertEqual(seen["save"]["reference_solution_id"], 9)
        self.assertEqual(len(seen["save"]["test_cases"]), 10)

    def test_too_many_additional_inputs_refused_before_execution(self):
        seen = {}
        with patch.object(orch, "validate_generation_preflight",
                          return_value=self.preflight(
                              hashlib.sha256(b"SRC").hexdigest(),
                              "1 <= N <= 100")), \
             patch.object(orch, "_generate_on_host",
                          side_effect=lambda *a: seen.setdefault("host", True)):
            with self.assertRaisesRegex(ValueError, "exceed the fixed 10"):
                orch.generate_and_save_test_cases(
                    57, "T9", additional_inputs=["3", "4", "5", "6", "7", "8"])
        self.assertNotIn("host", seen)

    def test_unreviewed_reference_without_sandbox_never_runs(self):
        result, seen = self.run_orch(None, reviewed=False)
        self.assertIsInstance(result, ValueError)
        self.assertIn("not been reviewed", str(result))
        self.assertNotIn("host", seen)
        self.assertNotIn("save", seen)

    def test_reference_changed_after_preflight_refused(self):
        result, seen = self.run_orch(UNTRUSTED_HASH)
        self.assertIn("changed after preflight", str(result))
        self.assertNotIn("host", seen)

    def test_unsupported_constraints_fail_before_execution(self):
        result, seen = self.run_orch(None, constraints="N is a string")
        self.assertIsInstance(result, ValueError)
        self.assertNotIn("host", seen)

    def test_sandbox_path_used_when_available(self):
        class Sandbox:
            cleaned = []
            def compile(self, src):
                return {"status": "COMPILED", "artifact": "A", "diagnostics": ""}
            def run_tests(self, artifact, cases):
                return [{"test_number": c["test_number"], "status": "completed",
                         "stdout": "ok\n"} for c in cases]
            def cleanup(self, artifact):
                self.cleaned.append(artifact)

        sb = Sandbox()
        result, seen = self.run_orch(None, sandbox=sb, reviewed=False)
        self.assertEqual(result["execution_mode"], "sandbox")
        self.assertNotIn("host", seen)
        self.assertEqual(sb.cleaned, ["A"])


DRAFT_JSON = json.dumps({
    "finalized": True, "approval": {"value": True, "code": True},
    "constraints": [
        {"category": "value", "review_status": "ACCEPTED", "text": "1 <= N <= 1000000"},
        {"category": "code", "review_status": "ACCEPTED", "text": "loops_only"}]})


def save_responder(owned=True, existing=0, stored_hash=None, source="SRC",
                   status="APPROVED", code_constraints="loops_only"):
    digest = hashlib.sha256(source.encode()).hexdigest()

    def responder(sql, params):
        if sql.startswith("SELECT total_marks, value_constraints"):
            return ([("10.00", "1 <= N <= 1000000", code_constraints, "50.00")]
                    if owned else []), -1
        if "FROM dbo.ConstraintReviewDraft" in sql:
            return [(DRAFT_JSON, True)], -1
        if sql.startswith("SELECT value_constraints, code_constraints"):
            return [("1 <= N <= 1000000", code_constraints)], -1
        if "FROM dbo.ReferenceSolution" in sql:
            return [(source, stored_hash or digest, "cpp", status)], -1
        if "FROM dbo.TestCase" in sql:
            return [(existing,)], -1
        if sql.startswith("INSERT INTO dbo.TestCase"):
            return [], 1
        raise AssertionError(sql)
    return responder, digest


def cases(n):
    return [{"test_number": i, "test_name": f"t{i}", "category": "c",
             "input_data": str(i), "expected_output": "NO\n"}
            for i in range(1, n + 1)]


class SecureSaveTests(unittest.TestCase):
    def save(self, responder, digest, test_cases, teacher="T9"):
        conn = FakeConnection(responder)
        with patch.object(save_mod, "get_connection", return_value=conn):
            try:
                return conn, save_mod.save_generated_test_cases_secure(
                    57, teacher, 9, digest, test_cases)
            except Exception as exc:
                return conn, exc

    def test_any_assessment_saves_10_with_equal_marks(self):
        responder, digest = save_responder()
        conn, r = self.save(responder, digest, cases(10))
        self.assertEqual(r["saved_count"], 10)
        self.assertEqual(r["marks_per_test"], "0.50")
        self.assertEqual(r["test_marks_allocated"], "5.00")
        marks = {p[-1] for s, p in conn.executed
                 if s.startswith("INSERT INTO dbo.TestCase")}
        self.assertEqual(marks, {Decimal("0.50")})            # identical rows
        self.assertTrue(all(p[4] == "hidden" for s, p in conn.executed
                            if s.startswith("INSERT INTO dbo.TestCase")))
        self.assertEqual(conn.commits, 1)

    def test_any_count_other_than_10_rejected_before_db(self):
        responder, digest = save_responder()
        for n in (1, 9, 11, 15):
            with self.subTest(n=n):
                conn, exc = self.save(responder, digest, cases(n))
                self.assertIn("exactly 10", str(exc))
                self.assertFalse(conn.executed)

    def test_rejections_roll_back(self):
        # 10 valid cases, so each scenario fails for its OWN reason.
        scenarios = [
            ("not owner", save_responder(owned=False), "access denied"),
            ("existing tests", save_responder(existing=2), "already has"),
            ("not approved", save_responder(status="PENDING"), "Approved C++"),
            ("tampered", save_responder(stored_hash="0" * 64), "hash mismatch"),
            ("code constraint drift", save_responder(code_constraints="other"),
             "code constraints"),
        ]
        for label, (responder, digest), message in scenarios:
            with self.subTest(label):
                conn, exc = self.save(responder, digest, cases(10))
                self.assertIsInstance(exc, ValueError)
                self.assertIn(message, str(exc))
                self.assertEqual(conn.commits, 0)
                self.assertFalse(conn.statements("INSERT"))

    def test_wrong_expected_hash_rejected(self):
        responder, _ = save_responder()
        _, exc = self.save(responder, "1" * 64, cases(10))
        self.assertIn("hash mismatch", str(exc))

    def test_whitespace_duplicate_inputs_rejected_before_db(self):
        responder, digest = save_responder()
        dup = cases(10)
        dup[1]["input_data"] = " 1 "
        conn, exc = self.save(responder, digest, dup)
        self.assertIsInstance(exc, ValueError)
        self.assertFalse(conn.executed)


flask_app = app_module.app
flask_app.config["TESTING"] = True


def headers(role="teacher", identity="T9"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity,
                                  additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class GenerationRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_students_forbidden(self):
        r = self.client.post("/api/assessments/57/generate-test-cases",
                             json={}, headers=headers("student"))
        self.assertEqual(r.status_code, 403)

    def test_body_validation(self):
        h = headers()
        for body in ({"assessment_id": 4},
                     {"additional_inputs": "97"},
                     {"additional_inputs": [97]},
                     {"additional_inputs": ["1"] * 11},
                     {"random_count": 21}):
            with self.subTest(body=body):
                r = self.client.post("/api/assessments/57/generate-test-cases",
                                     json=body, headers=h)
                self.assertEqual(r.status_code, 400)

    def test_random_count_rejected_with_explanation(self):
        r = self.client.post("/api/assessments/57/generate-test-cases",
                             json={"random_count": 5}, headers=headers())
        self.assertEqual(r.status_code, 400)
        self.assertIn("exactly 10", r.get_json()["message"])

    def test_ids_come_from_url_and_jwt(self):
        with patch("test_case_generation_orchestrator.generate_and_save_test_cases",
                   return_value={"success": True}) as m:
            r = self.client.post("/api/assessments/57/generate-test-cases",
                                 json={"additional_inputs": ["97"]},
                                 headers=headers(identity="T9"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m.call_args.kwargs, {
            "assessment_id": 57, "teacher_id": "T9",
            "additional_inputs": ["97"]})

    def test_other_teacher_gets_404(self):
        with patch("test_case_generation_orchestrator.generate_and_save_test_cases",
                   side_effect=ValueError("Assessment not found or access denied")):
            r = self.client.post("/api/assessments/57/generate-test-cases",
                                 json={}, headers=headers(identity="T2"))
        self.assertEqual(r.status_code, 404)


class ReferenceSolutionRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_students_forbidden(self):
        h = headers("student")
        self.assertEqual(self.client.get(
            "/api/assessments/57/reference-solution", headers=h).status_code, 403)
        self.assertEqual(self.client.post(
            "/api/assessments/57/reference-solution",
            json={"source_code": "x"}, headers=h).status_code, 403)

    def test_create_uses_jwt_teacher_and_rejects_extra_fields(self):
        h = headers(identity="T9")
        self.assertEqual(self.client.post(
            "/api/assessments/57/reference-solution",
            json={"source_code": "x", "approval_status": "APPROVED"},
            headers=h).status_code, 400)
        with patch("reference_solution_routes.save_reference_solution",
                   return_value={"reference_solution_id": 1}) as m:
            r = self.client.post("/api/assessments/57/reference-solution",
                                 json={"source_code": "int main(){}"}, headers=h)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m.call_args[0], (57, "T9", "int main(){}"))

    def test_error_mapping(self):
        h = headers()
        for message, code in (("Assessment not found or access denied", 404),
                              ("Reference solution already exists; x", 409),
                              ("Only PENDING reference solutions can be approved", 409),
                              ("Reviewed source hash does not match", 400)):
            with self.subTest(message=message), \
                 patch("reference_solution_routes.approve_reference_solution",
                       side_effect=ValueError(message)):
                r = self.client.post(
                    "/api/assessments/57/reference-solution/approve",
                    json={"source_hash": "a" * 64}, headers=h)
                self.assertEqual(r.status_code, code)


if __name__ == "__main__":
    unittest.main()
