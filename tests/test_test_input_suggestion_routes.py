"""
Phase D/E: teacher-only suggestion and validation endpoints, additional_inputs
integration, and the (disabled) model inference interface.
All database access is faked; nothing is executed or persisted.
"""

import unittest
from unittest.mock import patch

from tests import fakes  # noqa: F401
from tests.fakes import FakeConnection

from flask_jwt_extended import create_access_token

import ai_test_input_service as ai
import app as app_module
import test_input_suggestion_routes as routes
from test_case_builder import select_inputs
from generic_test_inputs import parse_approved_value_constraints

flask_app = app_module.app
flask_app.config["TESTING"] = True

ARRAY_PROBLEM = {
    "problem_statement": "Find the maximum subarray sum.",
    "input_format": "The first line contains N. The second line contains A.",
    "output_format": "One integer.",
    "value_constraints": "1 <= N <= 100000; -10^9 <= A_i <= 10^9",
    "sample_input": "3\n1 -2 3\n",
}


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        token = create_access_token(identity=identity, additional_claims={"role": role})
    return {"Authorization": f"Bearer {token}"}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_routes_registered(self):
        rules = {(r.rule, m) for r in flask_app.url_map.iter_rules() for m in r.methods}
        for rule in ("/api/test-inputs/suggestions", "/api/test-inputs/validate",
                     "/api/assessments/<int:assessment_id>/test-inputs/suggestions"):
            self.assertIn((rule, "POST"), rules)

    def test_jwt_required(self):
        for url in ("/api/test-inputs/suggestions", "/api/test-inputs/validate",
                    "/api/assessments/1/test-inputs/suggestions"):
            r = self.client.post(url, json={})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.get_json()["code"], "AUTH_REQUIRED")

    def test_students_forbidden(self):
        for url in ("/api/test-inputs/suggestions", "/api/test-inputs/validate",
                    "/api/assessments/1/test-inputs/suggestions"):
            r = self.client.post(url, json={"problem": ARRAY_PROBLEM}, headers=headers("student"))
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json()["status"], "error")

    def test_json_required(self):
        r = self.client.post("/api/test-inputs/suggestions", data="x", headers=headers())
        self.assertEqual(r.status_code, 415)


class ProblemSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def post(self, body):
        return self.client.post("/api/test-inputs/suggestions", json=body, headers=headers())

    def test_inferred_array_candidates(self):
        r = self.post({"problem": ARRAY_PROBLEM, "count": 10})
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()["data"]
        self.assertEqual((data["schema_source"], data["shape"]), ("inferred_from_sample", "array"))
        self.assertEqual(len(data["candidates"]), 10)
        for c in data["candidates"]:
            self.assertEqual(c["status"], "CANDIDATE")
            self.assertTrue(c["validation"]["valid"])
            self.assertFalse(c["usable_as_additional_input"])
            self.assertNotIn("expected_output", c)
        self.assertFalse(data["additional_inputs"]["supported"])
        self.assertIn("not verified", data["note"])

    def test_explicit_schema_graph(self):
        schema = {"variables": {"N": {"min": 2, "max": 50}, "M": {"min": 1, "max": 100}},
                  "items": [{"type": "ints", "vars": ["N", "M"]},
                            {"type": "edges", "count": "M", "vertices": "N", "directed": True,
                             "self_loops": False, "multi_edges": False, "connected": False}]}
        r = self.post({"problem": {"problem_statement": "Shortest paths."}, "input_schema": schema})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["data"]["schema_source"], "explicit")

    def test_invalid_schema_400(self):
        r = self.post({"problem": {"problem_statement": "x"},
                       "input_schema": {"items": [{"type": "ints", "vars": ["N"]}]}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_INPUT_SCHEMA")

    def test_unsupported_format_422_with_reasons(self):
        r = self.post({"problem": {"problem_statement": "Polygon area.", "input_format": "A polygon.",
                                   "sample_input": "4\n0 0\n1 0\n1 1\n0 1\n"}})
        self.assertEqual(r.status_code, 422)
        body = r.get_json()
        self.assertEqual(body["code"], "UNSUPPORTED_INPUT_FORMAT")
        self.assertTrue(body["reasons"])

    def test_unsupported_graph_semantics_422(self):
        schema = {"variables": {"N": {"min": 2, "max": 5}},
                  "items": [{"type": "ints", "vars": ["N"]},
                            {"type": "edges", "count": "N", "vertices": "N"}]}
        r = self.post({"problem": {"problem_statement": "x"}, "input_schema": schema})
        self.assertEqual(r.status_code, 422)

    def test_request_validation(self):
        self.assertEqual(self.post({"problem": ARRAY_PROBLEM, "random_count": 3}).status_code, 400)
        self.assertEqual(self.post({"problem": {"problem_statement": ""}}).status_code, 400)
        self.assertEqual(self.post({"problem": ARRAY_PROBLEM, "count": 0}).status_code, 400)
        self.assertEqual(self.post({"problem": ARRAY_PROBLEM, "method": "magic"}).status_code, 400)
        self.assertEqual(self.post({"problem": dict(ARRAY_PROBLEM, extra=1)}).status_code, 400)

    def test_model_method_unavailable_503(self):
        r = self.post({"problem": ARRAY_PROBLEM, "method": "model"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "AI_MODEL_UNAVAILABLE")

    def test_no_database_access(self):
        with patch.object(routes, "get_connection", side_effect=AssertionError("no DB")):
            self.assertEqual(self.post({"problem": ARRAY_PROBLEM}).status_code, 200)


class AssessmentSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def run_with(self, row, body=None, assessment_id=5, teacher="T1"):
        conn = FakeConnection(lambda sql, params: ([row] if row else [], -1))
        with patch.object(routes, "get_connection", return_value=conn):
            r = self.client.post(f"/api/assessments/{assessment_id}/test-inputs/suggestions",
                                 json=body or {}, headers=headers(identity=teacher))
        return r, conn

    def test_not_owner_404_and_teacher_from_jwt(self):
        r, conn = self.run_with(None)
        self.assertEqual(r.status_code, 404)
        sql, params = conn.executed[0]
        self.assertTrue(sql.startswith("SELECT"))
        self.assertEqual(params, (5, "T1"))

    def test_legacy_assessment_additional_inputs_plan(self):
        constraints = "2 <= N <= 1000000"
        r, conn = self.run_with(("Is N prime?", "One integer N.", "YES or NO.", constraints))
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()["data"]
        self.assertEqual(data["schema_source"], "legacy_constraints")
        plan = data["additional_inputs"]
        self.assertTrue(plan["supported"])
        self.assertEqual(len(plan["suggested"]), plan["slots"])
        self.assertTrue(set(plan["suggested"]).isdisjoint(plan["structural_inputs"]))
        # The real 10-case rule accepts exactly this payload.
        cases = select_inputs(parse_approved_value_constraints(constraints), plan["suggested"])
        self.assertEqual(len(cases), 10)
        usable = [c for c in data["candidates"] if c["usable_as_additional_input"]]
        self.assertTrue(usable)
        # read-only: assessment + approval-draft SELECTs only, no writes, no commit
        self.assertEqual(len(conn.executed), 2)
        self.assertTrue(all(sql.startswith("SELECT") for sql, _ in conn.executed))
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_structured_assessment_needs_sample_and_is_not_additional_input(self):
        row = (ARRAY_PROBLEM["problem_statement"], ARRAY_PROBLEM["input_format"],
               ARRAY_PROBLEM["output_format"], ARRAY_PROBLEM["value_constraints"])
        r, _ = self.run_with(row)
        self.assertEqual(r.status_code, 422)                       # no sample, no schema
        r, _ = self.run_with(row, {"sample_input": ARRAY_PROBLEM["sample_input"]})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()["data"]
        self.assertFalse(data["additional_inputs"]["supported"])
        self.assertEqual(data["assessment_id"], 5)

    def test_unknown_body_fields_rejected(self):
        r, conn = self.run_with(("p", "i", "o", "1 <= N <= 5"), {"additional_inputs": ["1"]})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(conn.executed)


class ValidateEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_validate_inputs(self):
        r = self.client.post("/api/test-inputs/validate", headers=headers(), json={
            "problem": ARRAY_PROBLEM,
            "inputs": ["2\n5 6\n", "2\n5\n", "2\n5 6\n", "1\n9999999999\n"]})
        self.assertEqual(r.status_code, 200, r.get_json())
        results = r.get_json()["data"]["results"]
        self.assertTrue(results[0]["valid"])
        self.assertEqual(results[1]["errors"][0]["code"], "TOKEN_COUNT")
        self.assertEqual(results[2]["duplicate_of"], 0)
        self.assertFalse(results[3]["constraints_valid"])
        self.assertEqual(r.get_json()["data"]["valid_count"], 1)

    def test_validate_limits(self):
        r = self.client.post("/api/test-inputs/validate", headers=headers(),
                             json={"problem": ARRAY_PROBLEM, "inputs": []})
        self.assertEqual(r.status_code, 400)


class ModelInterfaceTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(ai.TEST_INPUT_AI_MODE, "off")
        self.assertFalse(ai.status()["available"])
        with self.assertRaises(ai.ModelUnavailable):
            ai.generate_raw_candidates(ARRAY_PROBLEM, 5)

    def test_on_but_not_installed(self):
        with patch.object(ai, "TEST_INPUT_AI_MODE", "on"), \
                patch.object(ai, "MODEL_DIR", ai.MODEL_DIR.parent / "does_not_exist"):
            self.assertIn("not installed", ai.status()["reason"])
            with self.assertRaises(ai.ModelUnavailable):
                ai.generate_raw_candidates(ARRAY_PROBLEM, 5)

    def test_model_candidates_go_through_the_same_validation(self):
        fake = {"texts": ["2\n7 8\n", "2\n7\n", None, "3\n1 -2 3\n", "2\n7 8\n"],
                "model": {"name": "fake_model", "warnings": []}}
        import test_input_suggestion_service as service
        with patch.object(ai, "generate_raw_candidates", return_value=fake):
            data = service.suggest(ARRAY_PROBLEM, method="model", count=5)
        self.assertEqual([c["input"] for c in data["candidates"]], ["2\n7 8\n"])
        stages = sorted(r["stage"] for r in data["rejected"])
        self.assertEqual(stages, ["decode", "validation"])     # sample copy + duplicate dropped


if __name__ == "__main__":
    unittest.main()
