"""
Step 2 -> Step 3 backend flow ("Generate Test Cases" = explicit approval):
  POST /api/ai/drafts/<id>/approve-reviewed
  POST /api/assessments            (repeat click -> 409 with existing id)
  GET  /api/assessments/<id>/generation-readiness
In-memory fakes only; nothing reaches SQL Server, nothing is compiled or run.
"""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests.fakes import FakeConnection

from flask_jwt_extended import create_access_token

import app as app_module
import assessment_readiness_service as readiness
import teacher_approval_routes as routes
import teacher_approval_service as svc
from input_schema import check_schema, validate_input
from test_case_approval_validator import approved_input_schema
from test_case_builder import select_structured_inputs

SCHEMA = {"version": 1, "variables": {"N": {"min": 1, "max": 100}},
          "items": [{"type": "ints", "vars": ["N"]},
                    {"type": "array", "name": "A", "length": "N", "min": -1000, "max": 1000}],
          "relations": []}
VALUES = ["1 <= N <= 100", "-1000 <= A[i] <= 1000"]
CODES = ["for_loop_required", "conditional_required", "function_prohibited:sort",
         "recursion_prohibited"]


def ai_draft():
    """What POST /api/ai/drafts produces for the max/min problem."""
    def item(i, category, text):
        return {"id": f"id-{i}", "category": category, "text": text, "source": "AI",
                "review_status": "PENDING", "evidence_status": "NEEDS_TEACHER_REVIEW"}
    return {"draft_id": "d-1", "finalized": False,
            "approval": {"value": False, "code": False},
            "constraints": [item(1, "value", "1 <= N <= 100"),
                            item(2, "value", "-1000 <= A[i] <= 1000"),
                            item(3, "code", "for_loop_required"),
                            item(4, "code", "conditional_required"),
                            item(5, "code", "function_prohibited:sort"),
                            item(6, "code", "function_prohibited:stable_sort"),
                            item(7, "code", "recursion_prohibited")]}


def accepted(draft, category):
    return [i["text"] for i in draft["constraints"]
            if i["category"] == category and i["review_status"] == "ACCEPTED"]


class ApproveReviewedServiceTests(unittest.TestCase):

    def test_teacher_view_becomes_exactly_the_approved_draft(self):
        original = ai_draft()
        snapshot = copy.deepcopy(original)
        result, replay = svc.approve_reviewed_constraints(original, VALUES, CODES, SCHEMA)
        draft = result["draft"]
        self.assertFalse(replay)
        self.assertEqual(original, snapshot)                       # input not mutated
        self.assertTrue(draft["finalized"])
        self.assertEqual(draft["approval"], {"value": True, "code": True})
        self.assertEqual(accepted(draft, "value"), VALUES)
        self.assertEqual(accepted(draft, "code"), CODES)           # stable_sort removed
        rejected = [i["text"] for i in draft["constraints"] if i["review_status"] == "REJECTED"]
        self.assertEqual(rejected, ["function_prohibited:stable_sort"])
        self.assertFalse(any(i["review_status"] == "PENDING" for i in draft["constraints"]))
        self.assertEqual(draft["input_schema"]["items"][1]["min"], -1000)

    def test_teacher_edits_and_additions_are_recorded(self):
        result, _ = svc.approve_reviewed_constraints(
            ai_draft(), ["1 <= N <= 100", "-1000 <= A[i] <= 1000"],
            ["for_loop_required", "function_required"], SCHEMA)
        items = {i["text"]: i for i in result["draft"]["constraints"]}
        self.assertEqual(items["function_required"]["source"], "TEACHER")
        self.assertEqual(items["for_loop_required"]["source"], "AI")

    def test_empty_code_list_is_an_explicit_approval(self):
        result, _ = svc.approve_reviewed_constraints(ai_draft(), VALUES, [], SCHEMA)
        self.assertTrue(result["draft"]["approval"]["code"])
        self.assertEqual(accepted(result["draft"], "code"), [])

    def test_bad_lists_rejected(self):
        for values, codes in (([], CODES), (["1 <= N <= 100", " 1 <= N <=  100 "], CODES),
                              (VALUES, ["x", "X"]), (VALUES, "recursion_prohibited"),
                              (VALUES, [""]), (["a" * 501], [])):
            with self.subTest(values=values, codes=codes), self.assertRaises(ValueError):
                svc.approve_reviewed_constraints(ai_draft(), values, codes, SCHEMA)

    def test_schema_must_agree_with_visible_bounds(self):
        with self.assertRaises(svc.ReviewRejected) as ctx:
            svc.approve_reviewed_constraints(
                ai_draft(), ["1 <= N <= 100", "-500 <= A[i] <= 500"], CODES, SCHEMA)
        self.assertEqual(ctx.exception.code, "SCHEMA_CONSTRAINT_MISMATCH")
        self.assertEqual(ctx.exception.details[0]["schema_name"], "A")

    def test_unseen_schema_is_never_assumed(self):
        with self.assertRaises(svc.ReviewRejected) as ctx:
            svc.approve_reviewed_constraints(ai_draft(), VALUES, CODES, None)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA_REQUIRED")

    def test_legacy_integer_problem_needs_no_schema(self):
        draft = ai_draft()
        draft["constraints"] = draft["constraints"][:1]
        result, _ = svc.approve_reviewed_constraints(draft, ["1 <= N <= 100"], [], None)
        self.assertNotIn("input_schema", result["draft"])

    def test_invalid_schema_raises_schema_error(self):
        from input_schema import SchemaError
        with self.assertRaises(SchemaError):
            svc.approve_reviewed_constraints(ai_draft(), VALUES, CODES, {"version": 9})

    def test_retry_with_same_content_is_a_replay(self):
        result, _ = svc.approve_reviewed_constraints(ai_draft(), VALUES, CODES, SCHEMA)
        again, replay = svc.approve_reviewed_constraints(
            result["draft"], list(reversed(VALUES)), CODES, SCHEMA)
        self.assertTrue(replay)
        self.assertIs(again["draft"], result["draft"])

    def test_finalized_with_different_content_conflicts(self):
        result, _ = svc.approve_reviewed_constraints(ai_draft(), VALUES, CODES, SCHEMA)
        with self.assertRaises(svc.ReviewConflict):
            svc.approve_reviewed_constraints(result["draft"], VALUES, CODES[:1], SCHEMA)
        changed = copy.deepcopy(SCHEMA)
        changed["variables"]["N"]["max"] = 50
        with self.assertRaises(svc.ReviewConflict):
            svc.approve_reviewed_constraints(result["draft"], ["1 <= N <= 50"], CODES, changed)

    def test_previously_approved_category_must_match(self):
        draft = ai_draft()
        for item in draft["constraints"]:
            if item["category"] == "code":
                item["review_status"] = "ACCEPTED"
        draft["approval"]["code"] = True
        all_codes = accepted(draft, "code")
        result, _ = svc.approve_reviewed_constraints(draft, VALUES, all_codes, SCHEMA)
        self.assertTrue(result["draft"]["finalized"])
        with self.assertRaises(svc.ReviewConflict):
            svc.approve_reviewed_constraints(draft, VALUES, CODES, SCHEMA)

    def test_approved_draft_drives_exactly_ten_valid_unique_inputs(self):
        result, _ = svc.approve_reviewed_constraints(ai_draft(), VALUES, CODES, SCHEMA)
        schema = approved_input_schema(result["draft"])
        teacher_cases = ["3\n5 5 5\n", "4\n-1 -7 -3 -2\n"]
        inputs = select_structured_inputs(schema, teacher_cases)
        texts = [i["input_data"] for i in inputs]
        self.assertEqual(len(texts), 10)
        self.assertEqual(len(set(texts)), 10)
        self.assertEqual(texts[:2], teacher_cases)
        for text in texts:
            self.assertTrue(validate_input(check_schema(SCHEMA), text)["valid"])
            n, values = text.split("\n")[0], text.split("\n")[1].split()
            self.assertEqual(int(n), len(values))
            self.assertTrue(all(-1000 <= int(v) <= 1000 for v in values))


# ---------------------------------------------------------------- routes

flask_app = app_module.app
flask_app.config["TESTING"] = True
VERSION = b"\x00" * 7 + b"\x01"
BODY = {"value_constraints": VALUES, "code_constraints": CODES, "input_schema": SCHEMA}


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity, additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class ApproveReviewedRouteTests(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()
        self.store = {("d-1", "T1"): ai_draft()}
        self.updates = []

        def get(draft_id, teacher_id):
            draft = self.store.get((draft_id, teacher_id))
            return (copy.deepcopy(draft), VERSION) if draft else (None, None)

        def update(teacher_id, draft, version):
            self.updates.append((teacher_id, version))
            self.store[(draft["draft_id"], teacher_id)] = draft
            return draft

        for name, fn in (("get_draft_with_version", get), ("update_draft", update)):
            p = patch.object(routes, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def post(self, body=BODY, **kw):
        return self.client.post("/api/ai/drafts/d-1/approve-reviewed", json=body, **kw)

    def test_auth(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post(headers=headers(role="student")).status_code, 403)

    def test_other_teachers_draft_is_not_found(self):
        r = self.post(headers=headers(identity="T2"))
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "DRAFT_NOT_FOUND"))
        self.assertEqual(self.updates, [])

    def test_body_must_be_exact(self):
        for body in ({"value_constraints": VALUES, "code_constraints": CODES},
                     {**BODY, "teacher_id": "T2"}, {**BODY, "approve": True}, []):
            with self.subTest(body=body):
                r = self.post(body, headers=headers())
                self.assertEqual((r.status_code, r.get_json()["code"]), (400, "INVALID_REQUEST"))

    def test_success_writes_once_then_replays(self):
        r = self.post(headers=headers())
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(body["replay"])
        self.assertTrue(body["draft"]["finalized"])
        self.assertEqual(self.updates, [("T1", VERSION)])
        self.assertEqual(body["notes"], [])
        again = self.post(headers=headers())
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()["replay"])
        self.assertEqual(len(self.updates), 1)                       # nothing rewritten

    def test_conflicts(self):
        self.post(headers=headers())
        r = self.post({**BODY, "code_constraints": []}, headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "DRAFT_REVIEW_CONFLICT"))

    def test_concurrent_change_is_409_draft_changed(self):
        with patch.object(routes, "update_draft",
                          side_effect=ValueError(routes.CONFLICT_MESSAGE)):
            r = self.post(headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "DRAFT_CHANGED"))

    def test_mismatch_and_missing_schema_are_422(self):
        r = self.post({**BODY, "value_constraints": ["1 <= N <= 100", "-5 <= A[i] <= 5"]},
                      headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (422, "SCHEMA_CONSTRAINT_MISMATCH"))
        self.assertTrue(r.get_json()["details"])
        r = self.post({**BODY, "input_schema": None}, headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (422, "INPUT_SCHEMA_REQUIRED"))
        r = self.post({**BODY, "input_schema": {"version": 9}}, headers=headers())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.updates, [])

    def test_unchecked_code_text_is_saved_with_a_note(self):
        r = self.post({**BODY, "code_constraints": ["Use cin and cout"]}, headers=headers())
        notes = r.get_json()["notes"]
        self.assertEqual([n["code"] for n in notes], ["CODE_CONSTRAINT_NOT_AUTO_CHECKED"])
        self.assertEqual(notes[0]["constraint"], "Use cin and cout")


class AssessmentRepeatClickTests(unittest.TestCase):

    PAYLOAD = {"course_code": "CSC-101", "assessment_type": "Assignment",
               "title": "Find Maximum and Minimum Using Loops", "problem_statement": "P",
               "input_format": "I", "output_format": "O", "total_marks": 10,
               "deadline_at": "2099-12-31T23:59:00", "validation_percentage": 50,
               "draft_id": "d-1"}

    def run_create(self, linked_lookup):
        def responder(sql, params):
            if "FROM dbo.ConstraintReviewDraft WITH (UPDLOCK, HOLDLOCK)" in sql:
                return [(json.dumps({"finalized": True}), 1, 42)], -1     # already linked
            if "INNER JOIN dbo.Assessment AS a" in sql:
                return linked_lookup, -1
            raise AssertionError(sql)
        conns = []

        def factory():
            conns.append(FakeConnection(responder))
            return conns[-1]
        with patch.object(app_module, "get_connection", side_effect=factory):
            r = flask_app.test_client().post("/api/assessments", json=self.PAYLOAD,
                                             headers=headers())
        inserts = [s for c in conns for s, _ in c.executed if "INSERT" in s]
        return r, inserts

    def test_repeat_click_returns_existing_assessment(self):
        r, inserts = self.run_create([(42,)])
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "DRAFT_ALREADY_USED"))
        self.assertEqual(r.get_json()["assessment_id"], 42)
        self.assertEqual(inserts, [])

    def test_linked_elsewhere_stays_400(self):
        r, inserts = self.run_create([])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(inserts, [])


# ---------------------------------------------------------------- readiness

SOURCE = "int main(){}\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode()).hexdigest()


def finalized_draft(schema=SCHEMA):
    result, _ = svc.approve_reviewed_constraints(
        ai_draft(), VALUES if schema else ["1 <= N <= 100"],
        CODES, schema)
    return result["draft"]


def readiness_responder(owned=True, draft=None, reference=("APPROVED", SOURCE, SOURCE_HASH),
                        tests=0, sections=0, problem=("p", "i", "o"), marks=(10, 50)):
    draft = draft if draft is not None else finalized_draft()

    def responder(sql, params):
        if sql.startswith("SELECT assessment_id, course_code, total_marks"):
            return ([(7, "CSC-101", *marks, *problem)] if owned else []), -1
        if "FROM dbo.ConstraintReviewDraft WITH (UPDLOCK, HOLDLOCK) WHERE assessment_id" in sql:
            return [(json.dumps(draft), 1)], -1
        if "SELECT value_constraints, code_constraints FROM dbo.Assessment" in sql:
            return [("\n".join(accepted(draft, "value")), "\n".join(accepted(draft, "code")))], -1
        if "FROM dbo.ReferenceSolution" in sql:
            if reference is None:
                return [], -1
            status, source, digest = reference
            return [(3, source, digest, "cpp", status)], -1
        if "FROM dbo.TestCase" in sql:
            return [(tests,)], -1
        if "FROM dbo.AssessmentSection" in sql:
            return [(sections,)], -1
        raise AssertionError(sql)
    return responder


class FakeOracle:
    oracle_id = "fake_v1"
    reference_sha256 = SOURCE_HASH


class ReadinessTests(unittest.TestCase):

    def check(self, responder=None, sandbox=False, trusted=frozenset(), oracles=None,
              rubric_ok=True):
        conn = FakeConnection(responder or readiness_responder())

        def rubric(*_args):
            if not rubric_ok:
                raise ValueError("Assessment is not open for submissions yet")
            return True
        with patch("assessment_rubric_service.check_rubric_ready", side_effect=rubric):
            result = readiness.get_generation_readiness(
                7, "T1", connection_factory=lambda: conn, sandbox_available=sandbox,
                trusted_hashes=trusted, oracles=oracles or {})
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)
        for sql, _ in conn.executed:
            self.assertFalse(sql.upper().startswith(("INSERT", "UPDATE", "DELETE")))
        return result

    def codes(self, result, key="missing_for_generation"):
        return [m["code"] for m in result[key]]

    def test_blocked_without_output_source(self):
        result = self.check()
        self.assertFalse(result["ready_to_generate"])
        self.assertEqual(self.codes(result), ["EXPECTED_OUTPUT_SOURCE_UNAVAILABLE"])
        self.assertEqual(result["checks"]["input_format"], "APPROVED_INPUT_SCHEMA")
        self.assertEqual(self.codes(result, "missing_for_publish"), ["TEST_CASES_NOT_GENERATED"])

    def test_allowlisted_reference_is_ready(self):
        result = self.check(trusted=frozenset({SOURCE_HASH}))
        self.assertTrue(result["ready_to_generate"])
        self.assertEqual(result["checks"]["expected_output_source"],
                         {"source": "TRUSTED_HOST_ALLOWLIST", "endpoint": "generate-test-cases",
                          "reference_required": True})

    def test_sandbox_is_ready(self):
        self.assertEqual(self.check(sandbox=True)["checks"]["expected_output_source"]["source"],
                         "SECURE_SANDBOX")

    def test_oracle_only_for_legacy_format(self):
        structured = self.check(oracles={"fake_v1": FakeOracle()})
        self.assertIn("EXPECTED_OUTPUT_SOURCE_UNAVAILABLE", self.codes(structured))
        legacy = self.check(readiness_responder(draft=finalized_draft(schema=None)),
                            oracles={"fake_v1": FakeOracle()})
        self.assertEqual(legacy["checks"]["expected_output_source"]["endpoint"],
                         "generate-test-cases/oracle")
        self.assertTrue(legacy["ready_to_generate"])

    def test_reference_problems(self):
        no_ref = self.check(readiness_responder(reference=None))
        self.assertEqual(self.codes(no_ref), ["VERIFICATION_REQUIRED"])
        self.assertEqual(no_ref["verification"]["status"], "VERIFICATION_REQUIRED")
        self.assertIn("REFERENCE_SOLUTION_NOT_APPROVED",
                      self.codes(self.check(readiness_responder(
                          reference=("PENDING", SOURCE, SOURCE_HASH)))))
        self.assertIn("REFERENCE_SOLUTION_INVALID",
                      self.codes(self.check(readiness_responder(
                          reference=("APPROVED", SOURCE + "x", SOURCE_HASH)))))

    def test_unfinalized_draft(self):
        draft = ai_draft()
        self.assertIn("CONSTRAINTS_NOT_APPROVED",
                      self.codes(self.check(readiness_responder(draft=draft))))

    def test_publish_readiness(self):
        done = self.check(readiness_responder(tests=10), trusted=frozenset({SOURCE_HASH}))
        self.assertEqual(self.codes(done), ["TEST_CASES_ALREADY_EXIST"])
        self.assertTrue(done["ready_to_publish"])
        no_rubric = self.check(readiness_responder(tests=10), rubric_ok=False)
        self.assertEqual(self.codes(no_rubric, "missing_for_publish"), ["RUBRIC_INCOMPLETE"])

    def test_not_owner(self):
        with self.assertRaises(readiness.AssessmentNotFound):
            self.check(readiness_responder(owned=False))

    def test_route(self):
        client = flask_app.test_client()
        url = "/api/assessments/7/generation-readiness"
        self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual(client.get(url, headers=headers(role="student")).status_code, 403)
        with patch.object(readiness, "get_generation_readiness",
                          side_effect=readiness.AssessmentNotFound("x")):
            r = client.get(url, headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "ASSESSMENT_NOT_FOUND"))
        with patch.object(readiness, "get_generation_readiness",
                          return_value={"ready_to_generate": False}) as m:
            r = client.get(url, headers=headers(identity="T9"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m.call_args[0], (7, "T9"))
        with patch.object(readiness, "get_generation_readiness", side_effect=RuntimeError("db")):
            r = client.get(url, headers=headers())
        self.assertEqual((r.status_code, r.get_json()["code"]), (500, "READINESS_UNAVAILABLE"))


if __name__ == "__main__":
    unittest.main()
