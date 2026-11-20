"""
Reference-free generation through owner-reviewed problem specs:
  verified_problem_specs      registry integrity + exact matching (and negatives)
  reference_free_generation   preflight + generation without a ReferenceSolution
  secure_test_case_save       spec binding re-checked under lock, outputs recomputed
  readiness                   reference optional with a spec; VERIFICATION_REQUIRED
  POST .../generate-test-cases/verified
In-memory fakes only; nothing is compiled or executed.
"""

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.fakes import FakeConnection, approved_rubric_response
from tests.test_generate_click_flow import (
    SOURCE, SOURCE_HASH, accepted, finalized_draft, flask_app, headers, readiness_responder)

import assessment_readiness_service as readiness
import reference_free_generation as rfg
import secure_test_case_save as save_mod
import verified_problem_specs as vps
from verified_oracles import MAX_MIN_V1, VERIFIED_ORACLES

REGISTRY = json.loads(vps.REGISTRY_PATH.read_text(encoding="utf-8"))
SPEC = REGISTRY["specs"][0]
STATEMENT = SPEC["problem_statements"][0]
INPUT_FORMAT = SPEC["input_formats"][0]
OUTPUT_FORMAT = SPEC["output_formats"][0]
VALUES = "\n".join(SPEC["value_constraints"])
SCHEMA = SPEC["input_schema"]


def match(statement=STATEMENT, input_format=INPUT_FORMAT, output_format=OUTPUT_FORMAT,
          values=VALUES, schema=SCHEMA, specs=None):
    return vps.match_problem_spec(statement, input_format, output_format, values, schema,
                                  specs=specs)


def schema_with(**changes):
    schema = copy.deepcopy(SCHEMA)
    for path, value in changes.items():
        target = schema
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[int(key)] if key.isdigit() else target[key]
        target[leaf] = value
    return schema


def max_min(text):
    values = [int(t) for t in text.split()[1:]]
    hi = lo = values[0]
    for v in values[1:]:
        hi, lo = (v if v > hi else hi), (v if v < lo else lo)
    return f"{hi}\n{lo}\n"


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

class RegistryTests(unittest.TestCase):

    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "specs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return vps.load_specs(path)

    def broken(self, **changes):
        spec = copy.deepcopy(SPEC)
        spec.update(changes)
        return {"version": 1, "specs": [spec]}

    def test_reviewed_registry_loads_only_max_min(self):
        specs = vps.load_specs()
        self.assertEqual([(s["spec_id"], s["oracle_id"]) for s in specs],
                         [("max_min_loops_v1", "max_min_v1"),
                          ("max_min_loops_frontend_v1", "max_min_v1")])
        for spec in specs:
            self.assertIs(spec["oracle"], MAX_MIN_V1)
            self.assertEqual(len(spec["texts"]["problem_statement"]), 1)   # one wording each

    def test_malformed_entries_fail_closed(self):
        bad = {
            "unknown oracle": self.broken(oracle_id="sum_v9"),
            "domain wider than verified": self.broken(
                input_schema=schema_with(variables__N__max=1000),
                value_constraints=["1 <= N <= 1000", "-1000 <= A[i] <= 1000"]),
            "constraints contradict schema": self.broken(
                value_constraints=["1 <= N <= 50", "-1000 <= A[i] <= 1000"]),
            "no statements": self.broken(problem_statements=[]),
            "blank output format": self.broken(output_formats=["  "]),
            "no constraints": self.broken(value_constraints=[]),
            "structured without schema": self.broken(input_schema=None),
            "legacy oracle with schema": self.broken(oracle_id="prime_check_v1"),
            "invalid schema": self.broken(input_schema={"version": 9}),
        }
        for label, data in bad.items():
            with self.subTest(label):
                with self.assertRaises(vps.RegistryError):
                    self.load(data)
        with self.assertRaises(vps.RegistryError):
            self.load({"version": 1, "specs": [SPEC, SPEC]})            # duplicate id
        with self.assertRaises(vps.RegistryError):
            self.load({"version": 2, "specs": []})
        self.assertEqual(self.load({"version": 1, "specs": []}), [])

    def test_broken_registry_means_not_verified(self):
        with patch.object(vps, "load_specs", side_effect=vps.RegistryError("x")):
            spec, details = vps.safe_match(STATEMENT, INPUT_FORMAT, OUTPUT_FORMAT, VALUES, SCHEMA)
        self.assertIsNone(spec)
        self.assertEqual(details["reason"], "REGISTRY_UNAVAILABLE")

    def test_every_registered_oracle_exists(self):
        for spec in REGISTRY["specs"]:
            self.assertIn(spec["oracle_id"], VERIFIED_ORACLES)


# ----------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------

class MatchingTests(unittest.TestCase):

    def test_exact_specification_matches(self):
        spec, details = match()
        self.assertEqual(spec["spec_id"], "max_min_loops_v1")
        self.assertIsNone(details)

    def test_only_whitespace_case_and_unicode_are_normalised(self):
        spec, _ = match(statement="  " + STATEMENT.upper().replace("\n", "\r\n   ") + "\n",
                        output_format=OUTPUT_FORMAT.lower() + "  ",
                        values="1 ≤ N ≤ 100\n−1000 <= A[i] <= 1000")
        self.assertIsNotNone(spec)
        spec, _ = match(values="-1000 <= A[i] <= 1000\n1 <= N <= 100")   # order of lines
        self.assertIsNotNone(spec)

    def assertNotVerified(self, field, **kwargs):
        spec, details = match(**kwargs)
        self.assertIsNone(spec, kwargs)
        self.assertEqual(details["reason"], "SPEC_MISMATCH")
        self.assertIn(field, details["differs_in"])

    def test_subtly_different_outputs_cannot_use_max_min(self):
        for text in ("Print the minimum element on the first line and the maximum element "
                     "on the second line.",
                     "Print the maximum element and the minimum element on the same line.",
                     "Print the maximum element on the first line.",
                     "Print the sum of the elements.",
                     "Print the maximum element on the first line and the minimum element "
                     "on the second line"):                              # missing period
            with self.subTest(text=text):
                self.assertNotVerified("output_format", output_format=text)

    def test_different_or_similar_statements_cannot_use_max_min(self):
        variants = {
            "title only": "Find Maximum and Minimum Using Loops",
            "second largest": STATEMENT.replace("prints the maximum and the minimum value",
                                                "prints the second largest value"),
            "extra requirement": STATEMENT + "\nPrint the minimum first.",
            "keywords only": "maximum minimum loops array for loop if else cin cout",
            "sum problem": STATEMENT.replace("maximum and the minimum", "sum and the average"),
            "wider bounds in text": STATEMENT.replace("1 <= N <= 100", "1 <= N <= 1000"),
            "empty": "",
        }
        for label, text in variants.items():
            with self.subTest(label):
                self.assertNotVerified("problem_statement", statement=text)

    def test_different_input_contract_cannot_use_max_min(self):
        self.assertNotVerified("input_format", input_format=(
            "The first line contains N.\nEach of the next N lines contains one integer A[i]."))
        self.assertNotVerified("input_format", input_format="The input contains N integers.")

    def test_different_bounds_cannot_use_max_min(self):
        for values in ("1 <= N <= 1000\n-1000 <= A[i] <= 1000",
                       "1 <= N <= 100\n-1000000 <= A[i] <= 1000000",
                       "1 <= N <= 100",
                       VALUES + "\nA[i] != 0",
                       "1 <= N <= 100\n-999 <= A[i] <= 1000",
                       ""):
            with self.subTest(values=values):
                self.assertNotVerified("value_constraints", values=values)

    def test_different_schema_cannot_use_max_min(self):
        variants = {
            "distinct": schema_with(items__1__distinct=True),
            "sorted": schema_with(items__1__order="asc"),
            "narrower values": schema_with(items__1__min=-999),
            "wider count": schema_with(variables__N__max=101),
            "relation": schema_with(relations=[{"left": "N", "op": "<=", "right": 50}]),
            "no schema": None,
            "invalid": {"version": 1, "items": "x"},
        }
        for label, schema in variants.items():
            with self.subTest(label):
                self.assertNotVerified("input_schema", schema=schema)

    def test_ambiguous_registry_is_refused(self):
        specs = vps.load_specs() * 2
        spec, details = match(specs=specs)
        self.assertIsNone(spec)
        self.assertEqual(details["reason"], "AMBIGUOUS_SPEC")

    def test_no_specs(self):
        self.assertEqual(match(specs=[])[1]["reason"], "NO_REVIEWED_SPECS")


FRONTEND = next(s for s in REGISTRY["specs"] if s["spec_id"] == "max_min_loops_frontend_v1")
FE_STATEMENT = FRONTEND["problem_statements"][0]
FE_INPUT = FRONTEND["input_formats"][0]
FE_OUTPUT = FRONTEND["output_formats"][0]


def fe_match(statement=FE_STATEMENT, input_format=FE_INPUT, output_format=FE_OUTPUT,
             values=VALUES, schema=SCHEMA):
    return vps.match_problem_spec(statement, input_format, output_format, values, schema)


class FrontendWordingTests(unittest.TestCase):
    """The separately reviewed frontend wording (max_min_loops_frontend_v1)."""

    def test_frontend_wording_matches_its_own_entry(self):
        spec, details = fe_match()
        self.assertEqual((spec["spec_id"], spec["oracle_id"]),
                         ("max_min_loops_frontend_v1", "max_min_v1"))
        self.assertIsNone(details)
        self.assertEqual(FRONTEND["value_constraints"], SPEC["value_constraints"])
        self.assertEqual(FRONTEND["input_schema"], SPEC["input_schema"])

    def test_reviewed_semantics_pinned(self):
        lines = [l for l in FE_OUTPUT.splitlines() if l.strip()]
        self.assertEqual(lines[1:], ["The first line must contain the maximum value.",
                                     "The second line must contain the minimum value."])
        self.assertIn("N space-separated integers", FE_INPUT)

    def test_entries_cannot_be_mixed(self):
        # each field must come from the SAME reviewed entry
        for kwargs in ({"output_format": OUTPUT_FORMAT}, {"input_format": INPUT_FORMAT},
                       {"statement": STATEMENT}):
            with self.subTest(kwargs=list(kwargs)):
                self.assertIsNone(fe_match(**kwargs)[0])

    def assertRejected(self, field, **kwargs):
        spec, details = fe_match(**kwargs)
        self.assertIsNone(spec, kwargs)
        self.assertIn(field, details["differs_in"])

    def test_modified_output_wording_rejected(self):
        variants = {
            "swapped lines": FE_OUTPUT.replace("first line must contain the maximum",
                                               "first line must contain the minimum").replace(
                "second line must contain the minimum", "second line must contain the maximum"),
            "minimum only": FE_OUTPUT.split("\n\nThe first line")[0],
            "same line": "Print the maximum and minimum values on the same line.",
            "extra sum line": FE_OUTPUT + "\n\nThe third line must contain the sum.",
            "markdown added": FE_OUTPUT.replace("Print", "**Print**"),
        }
        for label, text in variants.items():
            with self.subTest(label):
                self.assertRejected("output_format", output_format=text)

    def test_modified_statement_rejected(self):
        variants = {
            "output-changing requirement": FE_STATEMENT + "\nPrint the minimum before the maximum.",
            "different task": FE_STATEMENT.replace("maximum and minimum values among them",
                                                   "sum of all values"),
            "wider bounds": FE_STATEMENT.replace("- 1 <= N <= 100", "- 1 <= N <= 1000"),
            "markdown stripped": FE_STATEMENT.replace("**", ""),
            "one requirement dropped": FE_STATEMENT.replace("4. You must not use recursion.\n", ""),
            "title only": "Find Maximum and Minimum Using Loops",
        }
        for label, text in variants.items():
            with self.subTest(label):
                self.assertRejected("problem_statement", statement=text)

    def test_modified_input_or_bounds_rejected(self):
        self.assertRejected("input_format", input_format=FE_INPUT.replace(
            "space-separated integers", "integers, one per line,"))
        self.assertRejected("input_format", input_format=FE_INPUT.replace("1 <= N <= 100",
                                                                          "1 <= N <= 1000"))
        self.assertRejected("value_constraints", values="1 <= N <= 1000\n-1000 <= A[i] <= 1000")
        self.assertRejected("input_schema", schema=schema_with(items__1__distinct=True))

    def test_whitespace_and_case_only_still_match(self):
        spec, _ = fe_match(statement=FE_STATEMENT.replace("\n\n", "\n").upper(),
                           output_format=" " + FE_OUTPUT.replace("\n\n", " ") + " ")
        self.assertEqual(spec["spec_id"], "max_min_loops_frontend_v1")


# ----------------------------------------------------------------------
# Fake database for preflight + secure save
# ----------------------------------------------------------------------

class World:
    def __init__(self, statement=STATEMENT, output_format=OUTPUT_FORMAT, schema=SCHEMA,
                 tests=0, rubric_ok=True, input_format=INPUT_FORMAT):
        self.draft = finalized_draft(schema=schema)
        self.fields = [statement, input_format, output_format]
        self.tests, self.rubric_ok = tests, rubric_ok
        self.value = "\n".join(accepted(self.draft, "value"))
        self.code = "\n".join(accepted(self.draft, "code"))

    def responder(self, sql, params):
        if "AssessmentRubricCriterion" in sql:
            if not self.rubric_ok and sql.startswith("SELECT rubric_criterion_id"):
                return [], -1
            return approved_rubric_response(sql, "10.00", "50.00")
        if sql.startswith("SELECT value_constraints, code_constraints, problem_statement"):
            return ([(self.value, self.code, *self.fields)] if params == (7, "T1") else []), -1
        if "FROM dbo.ConstraintReviewDraft" in sql:
            return [(json.dumps(self.draft), 1)], -1
        if "SELECT value_constraints, code_constraints FROM dbo.Assessment" in sql:
            return [(self.value, self.code)], -1
        if sql.startswith("SELECT total_marks, value_constraints"):
            return ([("10.00", self.value, self.code, "50.00")] if params == (7, "T1")
                    else []), -1
        if sql.startswith("SELECT problem_statement, input_format, output_format"):
            return [tuple(self.fields)], -1
        if "FROM dbo.TestCase" in sql:
            return [(self.tests,)], -1
        if sql.startswith("INSERT INTO dbo.TestCase"):
            return [], 1
        raise AssertionError(sql)


def run_generation(world, dry_run=True, teacher="T1", extra=None):
    preflight_conn = FakeConnection(world.responder)
    save_conn = FakeConnection(world.responder)
    with patch.object(save_mod, "get_connection", return_value=save_conn):
        try:
            result = rfg.generate_reference_free(
                7, teacher, extra, dry_run,
                preflight_fn=lambda a, t: rfg.reference_free_preflight(
                    a, t, connection_factory=lambda: preflight_conn))
        except Exception as exc:
            result = exc
    return result, preflight_conn, save_conn


class ReferenceFreeGenerationTests(unittest.TestCase):

    def no_reference_sql(self, *conns):
        for conn in conns:
            self.assertFalse(conn.statements("ReferenceSolution"))
            self.assertFalse([s for s, _ in conn.executed
                              if s.upper().startswith(("UPDATE", "DELETE"))])

    def test_preview_without_reference(self):
        result, pre, save = run_generation(World())
        self.assertEqual(result["output_source"], "VERIFIED_PROBLEM_SPEC")
        self.assertFalse(result["reference_required"])
        self.assertIsNone(result["reference_solution_id"])
        self.assertEqual((result["oracle_id"], result["spec_id"]), ("max_min_v1", "max_min_loops_v1"))
        cases = result["test_cases"]
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({c["input_data"] for c in cases}), 10)
        for c in cases:
            self.assertEqual(c["expected_output"], max_min(c["input_data"]))
        self.assertFalse(save.executed)                                 # preview writes nothing
        self.no_reference_sql(pre)

    def test_save_without_reference_equal_marks_and_rechecked(self):
        result, pre, save = run_generation(World(), dry_run=False,
                                           extra=["3\n5 5 5\n", "4\n-1 -7 -3 -2\n"])
        self.assertEqual(result["saved_count"], 10)
        self.assertEqual(result["marks_per_test"], "0.50")
        inserts = [p for s, p in save.executed if s.startswith("INSERT INTO dbo.TestCase")]
        self.assertEqual(len(inserts), 10)
        self.assertEqual({p[7] for p in inserts}, {Decimal("0.50")})
        for p in inserts:
            self.assertEqual(p[6], max_min(p[5]))
        self.assertEqual(save.commits, 1)
        self.assertTrue(save.statements("SELECT problem_statement, input_format, output_format"))
        self.no_reference_sql(pre, save)

    def test_frontend_wording_generates_and_saves_without_reference(self):
        world = World(statement=FE_STATEMENT, input_format=FE_INPUT, output_format=FE_OUTPUT)
        result, pre, save = run_generation(world, dry_run=False)
        self.assertEqual(result["spec_id"], "max_min_loops_frontend_v1")
        self.assertEqual(result["saved_count"], 10)
        inserts = [p for s, p in save.executed if s.startswith("INSERT INTO dbo.TestCase")]
        self.assertEqual(len({p[5] for p in inserts}), 10)
        for p in inserts:
            self.assertEqual(p[6], max_min(p[5]))
        self.no_reference_sql(pre, save)

    def test_unverified_problem_generates_nothing(self):
        for world in (World(output_format="Print the minimum then the maximum."),
                      World(statement="Find Maximum and Minimum Using Loops"),
                      World(schema=None)):
            with self.subTest():
                result, _, save = run_generation(world, dry_run=False)
                self.assertIsInstance(result, (vps.VerificationRequired, ValueError))
                self.assertFalse(save.executed)

    def test_verification_required_details(self):
        result, _, _ = run_generation(World(output_format="Print the minimum."))
        self.assertIsInstance(result, vps.VerificationRequired)
        self.assertEqual(result.details["differs_in"], ["output_format"])
        self.assertIn("cannot be verified automatically", result.message)

    def test_other_teacher_and_existing_tests(self):
        result, _, _ = run_generation(World(), teacher="T2")
        self.assertIn("access denied", str(result))
        result, _, _ = run_generation(World(tests=10))
        self.assertIn("already has test cases", str(result))

    def test_rubric_still_required_before_save(self):
        from assessment_rubric_service import RubricNotApprovedError
        result, _, save = run_generation(World(rubric_ok=False), dry_run=False)
        self.assertIsInstance(result, RubricNotApprovedError)
        self.assertFalse(save.statements("INSERT"))


class SecureSaveSpecBindingTests(unittest.TestCase):

    def cases(self):
        return run_generation(World())[0]["test_cases"]

    def save(self, world, cases, **kw):
        conn = FakeConnection(world.responder)
        args = {"reference_solution_id": None, "expected_source_hash": None,
                "structured": True, "verified_spec_id": "max_min_loops_v1", **kw}
        with patch.object(save_mod, "get_connection", return_value=conn):
            try:
                return conn, save_mod.save_generated_test_cases_secure(7, "T1", test_cases=cases,
                                                                       **args)
            except Exception as exc:
                return conn, exc

    def test_spec_drift_under_lock_refused(self):
        cases = self.cases()
        conn, r = self.save(World(output_format="Print the minimum first."), cases)
        self.assertIn("no longer matches", str(r))
        self.assertFalse(conn.statements("INSERT"))

    def test_tampered_output_refused(self):
        cases = self.cases()
        cases[4] = {**cases[4], "expected_output": "0\n0\n"}
        conn, r = self.save(World(), cases)
        self.assertIn("does not match the verified oracle", str(r))
        self.assertFalse(conn.statements("INSERT"))

    def test_wrong_or_mixed_binding_refused(self):
        cases = self.cases()
        _, r = self.save(World(), cases, verified_spec_id="other_spec")
        self.assertIn("no longer matches", str(r))
        _, r = self.save(World(), cases, reference_solution_id=3,
                         expected_source_hash=SOURCE_HASH)
        self.assertIn("not both", str(r))
        _, r = self.save(World(), cases, verified_spec_id="")
        self.assertIn("Invalid verified spec ID", str(r))
        _, r = self.save(World(), cases, verified_spec_id=None)          # no binding at all
        self.assertIn("Invalid reference solution ID", str(r))


# ----------------------------------------------------------------------
# Readiness
# ----------------------------------------------------------------------

class ReadinessReferenceFreeTests(unittest.TestCase):

    def check(self, reference=None, problem=(STATEMENT, INPUT_FORMAT, OUTPUT_FORMAT)):
        conn = FakeConnection(readiness_responder(reference=reference, problem=problem))
        with patch("assessment_rubric_service.check_rubric_ready", return_value=True):
            return readiness.get_generation_readiness(
                7, "T1", connection_factory=lambda: conn, sandbox_available=False,
                trusted_hashes=frozenset(), oracles={})

    def test_matching_spec_ready_without_reference(self):
        r = self.check()
        self.assertTrue(r["ready_to_generate"], r["missing_for_generation"])
        self.assertEqual(r["checks"]["expected_output_source"],
                         {"source": "VERIFIED_PROBLEM_SPEC",
                          "endpoint": "generate-test-cases/verified",
                          "oracle_id": "max_min_v1", "spec_id": "max_min_loops_v1",
                          "reference_required": False})
        self.assertEqual(r["verification"]["status"], "VERIFIED")
        self.assertFalse(r["verification"]["reference_required"])
        self.assertEqual(r["checks"]["reference_solution"], {"status": "MISSING"})

    def test_optional_pending_reference_does_not_block(self):
        r = self.check(reference=("PENDING", SOURCE, SOURCE_HASH))
        self.assertTrue(r["ready_to_generate"])

    def test_unverified_problem_reports_verification_required(self):
        r = self.check(problem=(STATEMENT, INPUT_FORMAT, "Print the minimum."))
        self.assertFalse(r["ready_to_generate"])
        self.assertEqual([m["code"] for m in r["missing_for_generation"]],
                         ["VERIFICATION_REQUIRED"])
        self.assertEqual(r["missing_for_generation"][0]["spec_match"]["differs_in"],
                         ["output_format"])
        self.assertEqual(r["verification"]["status"], "VERIFICATION_REQUIRED")
        self.assertIsNone(r["checks"]["expected_output_source"])


# ----------------------------------------------------------------------
# Route
# ----------------------------------------------------------------------

def ready(source):
    return {"checks": {"expected_output_source": source},
            "missing_for_generation": [] if source else [{"code": "VERIFICATION_REQUIRED"}],
            "verification": {"status": "VERIFIED" if source else "VERIFICATION_REQUIRED"}}


SPEC_SOURCE = {"source": "VERIFIED_PROBLEM_SPEC", "endpoint": "generate-test-cases/verified",
               "oracle_id": "max_min_v1", "spec_id": "max_min_loops_v1",
               "reference_required": False}


class VerifiedRouteTests(unittest.TestCase):
    URL = "/api/assessments/7/generate-test-cases/verified"

    def setUp(self):
        self.client = flask_app.test_client()

    def post(self, body=None, source=SPEC_SOURCE, identity="T1", **patches):
        targets = {"rf": "reference_free_generation.generate_reference_free",
                   "oracle": "oracle_test_case_service.generate_from_verified_oracle",
                   "generic": "test_case_generation_orchestrator.generate_and_save_test_cases"}
        mocks = {}
        with patch("assessment_readiness_service.get_generation_readiness",
                   return_value=ready(source)) as rd:
            stack = [patch(targets[k], **v) for k, v in patches.items()]
            for p, k in zip(stack, patches):
                mocks[k] = p.start()
            try:
                r = self.client.post(self.URL, headers=headers(identity=identity),
                                     json=body if body is not None else {})
            finally:
                for p in stack:
                    p.stop()
        return r, rd, mocks

    def test_auth(self):
        self.assertEqual(self.client.post(self.URL, json={}).status_code, 401)
        r = self.client.post(self.URL, headers=headers(role="student"), json={})
        self.assertEqual((r.status_code, r.get_json()["code"]), (403, "TEACHER_ONLY"))

    def test_reference_free_preview_and_save(self):
        r, rd, m = self.post(rf={"return_value": {"dry_run": True, "test_cases": []}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m["rf"].call_args[0], (7, "T1", None, True))
        self.assertEqual(rd.call_args[0], (7, "T1"))                    # teacher from JWT
        r, _, m = self.post({"dry_run": False, "additional_inputs": ["3\n1 2 3\n"]},
                            rf={"return_value": {"dry_run": False, "saved_count": 10}})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m["rf"].call_args[0], (7, "T1", ["3\n1 2 3\n"], False))

    def test_verification_required_when_no_source(self):
        r, _, m = self.post(source=None, rf={"side_effect": AssertionError("must not run")})
        body = r.get_json()
        self.assertEqual((r.status_code, body["code"]), (409, "VERIFICATION_REQUIRED"))
        self.assertEqual((body["generated_count"], body["saved_count"]), (0, 0))
        self.assertIn("cannot be verified automatically", body["message"])
        self.assertFalse(m["rf"].called)

    def test_reference_bound_oracle_still_supported(self):
        source = {"source": "VERIFIED_ORACLE", "endpoint": "generate-test-cases/oracle",
                  "oracle_id": "max_min_v1", "reference_required": True}
        r, _, m = self.post(source=source, oracle={"return_value": {"dry_run": True}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m["oracle"].call_args[1]["oracle_id"], "max_min_v1")
        self.assertTrue(r.get_json()["data"]["reference_required"])

    def test_sandbox_or_allowlist_source(self):
        source = {"source": "TRUSTED_HOST_ALLOWLIST", "endpoint": "generate-test-cases",
                  "reference_required": True}
        r, _, _ = self.post(source=source)
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "PREVIEW_UNAVAILABLE"))
        r, _, m = self.post({"dry_run": False}, source=source,
                            generic={"return_value": {"saved_count": 10}})
        self.assertEqual(r.status_code, 201)
        self.assertTrue(m["generic"].called)

    def test_errors_are_coded(self):
        from assessment_rubric_service import RubricNotApprovedError
        cases = [
            (vps.VerificationRequired("changed", {"reason": "SPEC_MISMATCH"}), 409,
             "VERIFICATION_REQUIRED"),
            (RubricNotApprovedError("approve"), 409, "RUBRIC_NOT_APPROVED"),
            (ValueError("Assessment already has test cases"), 409, "TEST_CASES_ALREADY_EXIST"),
            (ValueError("Assessment not found or access denied"), 404, "ASSESSMENT_NOT_FOUND"),
            (RuntimeError("db"), 500, "GENERATION_FAILED"),
        ]
        for error, status, code in cases:
            with self.subTest(code=code):
                r, _, _ = self.post({"dry_run": False}, rf={"side_effect": error})
                self.assertEqual((r.status_code, r.get_json()["code"]), (status, code))

    def test_body_validation(self):
        for body in ({"dry_run": "no"}, {"oracle_id": "max_min_v1"},
                     {"expected_outputs": ["1"]}, {"additional_inputs": "3 1 2 3"}):
            with self.subTest(body=body):
                r, _, _ = self.post(body)
                self.assertEqual(r.status_code, 400)


class NoExecutionTests(unittest.TestCase):

    def test_new_modules_never_execute_code(self):
        for module in ("verified_problem_specs.py", "reference_free_generation.py"):
            text = (vps.REGISTRY_PATH.parent / module).read_text(encoding="utf-8")
            for forbidden in ("subprocess", "secure_execution_gate", "os.system", "g++",
                              "compile(", "exec(", "eval(", "trusted_reference_hashes"):
                self.assertNotIn(forbidden, text, f"{module}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
