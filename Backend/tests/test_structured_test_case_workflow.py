"""
End-to-end structured test-case workflow through the REAL Flask routes,
approval draft service, preflight, orchestrator, builder, validators and the
locked secure save:

    approval draft (+ input schema) -> approve/finalize -> suggestions ->
    additional_inputs -> expected outputs -> secure save -> stored rows

Only two things are simulated:
  * the database: an in-memory store behind the FakeConnection interface
    (no SQL Server writes are authorised);
  * the "verified sandbox": a Python stand-in for the reference program, so
    NO C++ is compiled or executed anywhere.
When no sandbox is available (the real state) the workflow must stop with
EXPECTED_OUTPUT_UNVERIFIABLE and save nothing.
"""

import hashlib
import json
import unittest
import uuid
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

from tests import fakes  # noqa: F401
from tests.fakes import FakeConnection, approved_rubric_response

from flask_jwt_extended import create_access_token

import app as app_module
import constraint_draft_db
import secure_test_case_save
import test_case_generation_orchestrator as orchestrator
import test_case_generation_service
import test_input_suggestion_routes

flask_app = app_module.app
flask_app.config["TESTING"] = True

TEACHER, OTHER = "T-OWNER", "T-OTHER"
REFERENCE = "#include <iostream>\nint main(){/* reviewed reference */}\n"
REF_HASH = hashlib.sha256(REFERENCE.encode("utf-8")).hexdigest()

ARRAY_SCHEMA = {"variables": {"N": {"min": 1, "max": 50}},
                "items": [{"type": "ints", "vars": ["N"]},
                          {"type": "array", "name": "A", "length": "N", "min": -1000, "max": 1000}]}
MULTILINE_SCHEMA = {"variables": {"N": {"min": 1, "max": 20}, "x": {"min": 0, "max": 99}},
                    "items": [{"type": "ints", "vars": ["N"]},
                              {"type": "repeat", "count": "N", "items": [{"type": "ints", "vars": ["x"]}]}]}
STRING_SCHEMA = {"items": [{"type": "string", "name": "S", "min_length": 1, "max_length": 40,
                            "charset": "printable"}]}
GRAPH_SCHEMA = {"variables": {"N": {"min": 2, "max": 30}, "M": {"min": 1, "max": 60}},
                "items": [{"type": "ints", "vars": ["N", "M"]},
                          {"type": "edges", "count": "M", "vertices": "N", "directed": False,
                           "self_loops": False, "multi_edges": False, "connected": False,
                           "weight": {"min": 1, "max": 100}}]}


def headers(identity=TEACHER, role="teacher"):
    with flask_app.app_context():
        token = create_access_token(identity=identity, additional_claims={"role": role})
    return {"Authorization": f"Bearer {token}"}


class Store:
    """In-memory Assessment / ConstraintReviewDraft / ReferenceSolution / TestCase."""

    def __init__(self):
        self.assessments, self.drafts, self.references, self.test_cases = {}, {}, {}, []
        self.connections = []

    def connect(self, *args, **kwargs):
        conn = StoreConnection(self)
        self.connections.append(conn)
        return conn


class StoreConnection(FakeConnection):
    def __init__(self, store):
        super().__init__(self.respond)
        self.store = store
        self.pending = []

    def commit(self):
        super().commit()
        self.store.test_cases.extend(self.pending)
        self.pending = []

    def rollback(self):
        super().rollback()
        self.pending = []

    def close(self):
        self.closed = True

    def respond(self, sql, p):
        s = self.store
        if sql.startswith("SELECT draft_json, row_version FROM dbo.ConstraintReviewDraft"):
            d = s.drafts.get(p[0])
            return ([(d["json"], d["version"])] if d and d["teacher"] == p[1] else []), -1
        if sql.startswith("UPDATE dbo.ConstraintReviewDraft"):
            d = s.drafts.get(p[2])
            if not d or d["teacher"] != p[3] or d["final"] or d["version"] != p[4]:
                return [], 0
            d.update(json=p[0], final=bool(p[1]), version=uuid.uuid4().bytes[:8])
            return [], 1
        if sql.startswith("SELECT draft_json, is_finalized FROM dbo.ConstraintReviewDraft"):
            return [(d["json"], d["final"]) for d in s.drafts.values() if d["assessment"] == p[0]], -1
        if "FROM dbo.Assessment" in sql:
            a = s.assessments.get(p[0])
            if a is None or (len(p) > 1 and a["teacher"] != p[1]):
                return [], -1
            if sql.startswith("SELECT assessment_id, value_constraints, code_constraints"):
                return [(p[0], a["value"], a["code"])], -1
            if sql.startswith("SELECT value_constraints, code_constraints"):
                return [(a["value"], a["code"])], -1
            if sql.startswith("SELECT total_marks"):
                return [(a["total"], a["value"], a["code"], a["validation"])], -1
            if sql.startswith("SELECT problem_statement"):
                return [(a["statement"], a["input_format"], a["output_format"], a["value"])], -1
        if "FROM dbo.ReferenceSolution" in sql:
            r = s.references.get(p[0])
            if r is None:
                return [], -1
            if sql.startswith("SELECT reference_solution_id"):
                return [(r["id"], r["source"], r["hash"], "cpp", "APPROVED")], -1
            return [(r["source"], r["hash"], "cpp", "APPROVED")], -1
        if "AssessmentRubricCriterion" in sql:
            a = s.assessments[p[0]] if p else None
            return approved_rubric_response(sql, a and a["total"], a and a["validation"])
        if sql.startswith("SELECT COUNT(*) FROM dbo.TestCase"):
            return [(sum(t["assessment_id"] == p[0] for t in s.test_cases),)], -1
        if sql.startswith("INSERT INTO dbo.TestCase"):
            self.pending.append(dict(zip(("assessment_id", "test_number", "test_name", "category",
                                          "visibility", "input_data", "expected_output", "marks"), p)))
            return [], 1
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeSandbox:
    """Stand-in for the verified sandbox: a PYTHON model of the reference."""

    def __init__(self, program):
        self.program = program
        self.stdin_seen = []

    def compile(self, source_code):
        assert source_code == REFERENCE
        return {"status": "COMPILED", "artifact": "artifact-1"}

    def run_tests(self, artifact, cases):
        out = []
        for case in cases:
            self.stdin_seen.append(case["input_data"])
            out.append({"test_number": case["test_number"], "status": "completed",
                        "stdout": self.program(case["input_data"])})
        return out

    def cleanup(self, artifact):
        pass


def total_of_numbers(stdin):
    return str(sum(int(t) for t in stdin.split())) + "\n"


def length_and_repr(stdin):
    return f"{len(stdin)} {stdin.count(' ')}\n"


class WorkflowBase(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.client = flask_app.test_client()
        self.stack = ExitStack()
        for module in (constraint_draft_db, test_case_generation_service, secure_test_case_save,
                       test_input_suggestion_routes):
            self.stack.enter_context(patch.object(module, "get_connection", self.store.connect))
        self.stack.enter_context(patch.object(orchestrator, "load_trusted_reference_hashes",
                                              return_value=frozenset()))
        self.sandbox = None
        self.stack.enter_context(patch.object(orchestrator, "get_secure_sandbox",
                                              lambda: self.sandbox))

    def tearDown(self):
        self.stack.close()

    # -- set-up through the real approval routes ---------------------------
    def make_assessment(self, value_text, schema=None, assessment_id=7):
        draft_id = str(uuid.uuid4())
        draft = {"draft_id": draft_id,
                 "constraints": [{"id": "c1", "category": "value", "text": value_text,
                                  "source": "TEACHER", "review_status": "ACCEPTED",
                                  "evidence_status": "TEACHER_PROVIDED"}],
                 "approval": {"value": False, "code": False}, "finalized": False}
        self.store.drafts[draft_id] = {"teacher": TEACHER, "assessment": assessment_id,
                                       "json": json.dumps(draft), "final": False,
                                       "version": b"\x00" * 8}
        if schema is not None:
            r = self.client.put(f"/api/ai/drafts/{draft_id}/input-schema",
                                json={"input_schema": schema}, headers=headers())
            self.assertEqual(r.status_code, 200, r.get_json())
        for category in ("value", "code"):
            r = self.client.post(f"/api/ai/drafts/{draft_id}/approve",
                                 json={"category": category}, headers=headers())
            self.assertEqual(r.status_code, 200, r.get_json())
        r = self.client.post(f"/api/ai/drafts/{draft_id}/finalize", json={}, headers=headers())
        self.assertEqual(r.status_code, 200, r.get_json())
        self.store.assessments[assessment_id] = {
            "teacher": TEACHER, "value": value_text, "code": "", "total": Decimal("10.00"),
            "validation": Decimal("0"), "statement": "Sum the numbers.",
            "input_format": "See schema.", "output_format": "One integer."}
        self.store.references[assessment_id] = {"id": 3, "source": REFERENCE, "hash": REF_HASH}
        return draft_id

    def generate(self, additional=None, assessment_id=7, identity=TEACHER, role="teacher"):
        body = {} if additional is None else {"additional_inputs": additional}
        return self.client.post(f"/api/assessments/{assessment_id}/generate-test-cases",
                                json=body, headers=headers(identity, role))

    def saved(self, assessment_id=7):
        return sorted((t for t in self.store.test_cases if t["assessment_id"] == assessment_id),
                      key=lambda t: t["test_number"])


class StructuredWorkflowTests(WorkflowBase):
    def test_count_prefixed_array_exact_text_end_to_end(self):
        self.make_assessment("1 <= N <= 50; -1000 <= A_i <= 1000", ARRAY_SCHEMA)
        # suggestions reuse the APPROVED schema without the teacher repeating it
        r = self.client.post("/api/assessments/7/test-inputs/suggestions", json={"count": 12},
                             headers=headers())
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()["data"]
        self.assertEqual(data["schema_source"], "approved_input_schema")
        plan = data["additional_inputs"]
        self.assertTrue(plan["supported"])
        self.assertEqual(plan["format"], "exact_text")
        chosen = plan["suggested"][:3] + ["3\n-1 0 1\n"]
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(chosen)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["data"]["input_format"], "approved_input_schema")
        rows = self.saved()
        self.assertEqual(len(rows), 10)
        self.assertEqual([t["input_data"] for t in rows[:4]], chosen)       # exact, newlines kept
        self.assertEqual(self.sandbox.stdin_seen[:4], chosen)                # reference got same bytes
        self.assertEqual(rows[3]["expected_output"], "3\n")   # model sums all tokens: 3-1+0+1
        self.assertEqual(len({t["input_data"] for t in rows}), 10)
        self.assertTrue(all(t["visibility"] == "hidden" for t in rows))

    def test_multi_line_input(self):
        self.make_assessment("1 <= N <= 20; 0 <= x <= 99", MULTILINE_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        teacher = ["3\n5\n6\n7\n", "1\n0\n"]
        r = self.generate(teacher)
        self.assertEqual(r.status_code, 201, r.get_json())
        rows = self.saved()
        self.assertEqual(rows[0]["input_data"], "3\n5\n6\n7\n")
        self.assertTrue(all(t["input_data"].count("\n") == int(t["input_data"].split("\n")[0]) + 1
                            for t in rows))

    def test_string_with_significant_spaces(self):
        self.make_assessment("1 <= |S| <= 40", STRING_SCHEMA)
        self.sandbox = FakeSandbox(length_and_repr)
        teacher = ["a  b\n", "a b\n", "Hello, World!\n"]
        r = self.generate(teacher)
        self.assertEqual(r.status_code, 201, r.get_json())
        rows = self.saved()
        self.assertEqual([t["input_data"] for t in rows[:3]], teacher)      # "a  b" != "a b"
        self.assertEqual(rows[0]["expected_output"], "5 2\n")

    def test_graph_with_explicit_semantics(self):
        self.make_assessment("2 <= N <= 30; 1 <= M <= 60", GRAPH_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(["3 2\n1 2 5\n2 3 7\n"])
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(len(self.saved()), 10)
        r = self.client.post("/api/assessments/7/test-inputs/suggestions", json={},
                             headers=headers())
        self.assertEqual(r.status_code, 200)

    def test_invalid_inputs_rejected_with_details_and_nothing_saved(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(["3\n1 2\n", "2\n1 5000\n", "2\n1 2\n"])
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertEqual(body["code"], "INVALID_TEST_INPUT")
        self.assertEqual([e["index"] for e in body["errors"]], [0, 1])
        self.assertEqual(body["errors"][0]["errors"][0]["code"], "TOKEN_COUNT")
        self.assertEqual(body["errors"][1]["errors"][0]["code"], "OUT_OF_RANGE")
        self.assertEqual(self.sandbox.stdin_seen, [])                       # nothing ran
        self.assertEqual(self.saved(), [])

    def test_duplicates_rejected(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(["2\n1 2\n", "2\n1 2"])              # same input, final newline optional
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["errors"][0]["errors"][0]["code"], "DUPLICATE_INPUT")
        self.assertEqual(self.saved(), [])

    def test_exactly_ten_boundary(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        ten = [f"1\n{v}\n" for v in range(10)]
        self.assertEqual(self.generate(ten + ["1\n99\n"]).status_code, 400)  # 11 refused
        r = self.generate(ten)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual([t["input_data"] for t in self.saved()], ten)        # no generated fill
        self.assertEqual(self.generate(ten).status_code, 409)                 # already has tests

    def test_schema_too_small_for_ten_cases(self):
        tiny = {"variables": {"N": {"min": 1, "max": 3}}, "items": [{"type": "ints", "vars": ["N"]}]}
        self.make_assessment("1 <= N <= 3 (tiny)", tiny)
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate()
        self.assertEqual(r.status_code, 400)
        self.assertIn("allows only 3 distinct inputs", r.get_json()["message"])

    def test_unverified_expected_outputs_not_finalized(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        self.sandbox = None                                     # the real state: gate disabled
        r = self.generate(["2\n1 2\n"])
        self.assertEqual(r.status_code, 409)
        body = r.get_json()
        self.assertEqual(body["code"], "EXPECTED_OUTPUT_UNVERIFIABLE")
        self.assertEqual(body["finalization_status"], "NOT_FINALIZED")
        self.assertEqual(len(body["validated_inputs"]), 10)
        self.assertEqual(body["validated_inputs"][0]["input_data"], "2\n1 2\n")
        self.assertNotIn("expected_output", json.dumps(body))
        self.assertEqual(self.saved(), [])

    def test_oracle_path_refuses_structured_assessment(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        r = self.client.post("/api/assessments/7/generate-test-cases/oracle",
                             json={"oracle_id": "prime_check_v1"}, headers=headers())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.saved(), [])


class SchemaApprovalTests(WorkflowBase):
    def test_unknown_grammar_cannot_be_stored(self):
        draft_id = str(uuid.uuid4())
        draft = {"draft_id": draft_id, "constraints": [], "approval": {"value": False, "code": False},
                 "finalized": False}
        self.store.drafts[draft_id] = {"teacher": TEACHER, "assessment": None,
                                       "json": json.dumps(draft), "final": False, "version": b"\0" * 8}
        r = self.client.put(f"/api/ai/drafts/{draft_id}/input-schema",
                            json={"input_schema": {"items": [{"type": "polygon"}]}}, headers=headers())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["code"], "UNSUPPORTED_INPUT_FORMAT")
        r = self.client.put(f"/api/ai/drafts/{draft_id}/input-schema",
                            json={"input_schema": {"items": [{"type": "ints", "vars": ["Q"]}]}},
                            headers=headers())
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("input_schema", json.loads(self.store.drafts[draft_id]["json"]))

    def test_schema_locked_after_value_approval(self):
        draft_id = self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        r = self.client.put(f"/api/ai/drafts/{draft_id}/input-schema",
                            json={"input_schema": None}, headers=headers())
        self.assertIn(r.status_code, (400, 409))
        self.assertIn("input_schema", json.loads(self.store.drafts[draft_id]["json"]))

    def test_other_teacher_and_student_refused(self):
        draft_id = self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        self.sandbox = FakeSandbox(total_of_numbers)
        self.assertEqual(self.client.put(f"/api/ai/drafts/{draft_id}/input-schema",
                                         json={"input_schema": None},
                                         headers=headers(OTHER)).status_code, 404)
        self.assertEqual(self.generate(identity=OTHER).status_code, 404)
        self.assertEqual(self.generate(role="student", identity="S1").status_code, 403)
        self.assertEqual(self.client.post("/api/assessments/7/test-inputs/suggestions", json={},
                                          headers=headers(OTHER)).status_code, 404)
        self.assertEqual(self.client.post("/api/assessments/7/generate-test-cases",
                                          json={}).status_code, 401)
        self.assertEqual(self.saved(), [])

    def test_save_refuses_schema_mismatch(self):
        self.make_assessment("1 <= N <= 50", ARRAY_SCHEMA)
        cases = [{"test_number": i, "test_name": f"T{i}", "category": "c",
                  "input_data": f"1\n{i}\n", "expected_output": "x\n"} for i in range(1, 11)]
        with self.assertRaises(ValueError) as ctx:
            secure_test_case_save.save_generated_test_cases_secure(
                7, TEACHER, 3, REF_HASH, cases, structured=False)
        self.assertIn("Approved input schema changed", str(ctx.exception))
        self.assertEqual(self.saved(), [])


class LegacyCompatibilityWorkflowTests(WorkflowBase):
    """No schema in the draft -> exactly the old legacy behaviour."""

    def test_single_integer_legacy(self):
        self.make_assessment("2 <= N <= 1000000")
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(["97"])
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertNotIn("input_format", r.get_json()["data"])
        rows = self.saved()
        self.assertEqual(len(rows), 10)
        self.assertIn("97", [t["input_data"] for t in rows])
        self.assertTrue(all(s.endswith("\n") for s in self.sandbox.stdin_seen))   # legacy stdin

    def test_two_and_three_integers_legacy(self):
        self.make_assessment("1 <= a, b, c <= 1000")
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate()
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertTrue(all(len(t["input_data"].split()) == 3 for t in self.saved()))

    def test_legacy_still_collapses_whitespace_and_refuses_structured_text(self):
        self.make_assessment("1 <= a, b <= 1000")
        self.sandbox = FakeSandbox(total_of_numbers)
        r = self.generate(["5\n6"])                     # legacy rule: normalised to "5 6"
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertIn("5 6", [t["input_data"] for t in self.saved()])

    def test_legacy_without_sandbox_unchanged_error(self):
        self.make_assessment("2 <= N <= 1000000")
        r = self.generate()
        self.assertEqual(r.status_code, 400)
        self.assertIn("has not been reviewed", r.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
