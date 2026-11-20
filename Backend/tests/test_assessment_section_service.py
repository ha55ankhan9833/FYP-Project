"""
Assessment-to-section assignment: service transactions + API authorization.
All database access goes to an in-memory fake; nothing reaches SQL Server.
"""

import unittest
import json
from datetime import datetime
from unittest.mock import patch

from tests.fakes import FakeConnection
from tests.test_generate_click_flow import finalized_draft, accepted

from flask_jwt_extended import create_access_token

import app as app_module
import assessment_section_service as svc

DEADLINE = datetime(2026, 12, 1, 23, 59)


class FakeIntegrityError(Exception):
    pass


def responder_factory(owned=True, sections=("SEC-A", "SEC-B"),
                      teaches=("SEC-A", "SEC-B"), assigned=(),
                      submissions=0, delete_rowcount=1,
                      insert_error=None, test_cases=10,
                      rubric=((1, "Logic", "3.00", None), (2, "Quality", "2.00", None))):
    next_id = [100]
    published = set(assigned)
    draft = finalized_draft(schema=None)

    def responder(sql, params):
        if sql.startswith("SELECT assessment_id, course_code, deadline_at") \
                or sql.startswith("SELECT assessment_id, course_code, deadline_at"):
            return ([(7, "CAI-252", DEADLINE, False, "10.00", "50.00", "Publish fixture", "Assignment", "Course fixture")]
                    if owned else []), -1
        if "FROM dbo.TestCase WITH (HOLDLOCK)" in sql:
            return [(i+1, str(i+1), str(i+1), "0.50") for i in range(test_cases)], -1
        if "FROM dbo.ConstraintReviewDraft" in sql:
            return [(json.dumps(draft), 1)], -1
        if sql.startswith("SELECT value_constraints, code_constraints"):
            return [("\n".join(accepted(draft,"value")), "\n".join(accepted(draft,"code")))], -1
        if sql.startswith("SELECT section_id FROM dbo.AssessmentSection"):
            return [(key,) for key in sorted(published)], -1
        if "OBJECT_ID(N'dbo.AssessmentRubricCriterion'" in sql:
            return [(1,)], -1
        if "FROM dbo.AssessmentRubricCriterion WITH (HOLDLOCK)" in sql:
            return list(rubric), -1
        if "FROM dbo.Section_Export WITH (HOLDLOCK) WHERE section_id = ?" in sql:
            return ([(params[0],)] if params[0] in sections else []), -1
        if "FROM dbo.SectionCourseTeacher" in sql and "THEN 1 ELSE 0" in sql:
            section, teacher, course = params
            ok = section in teaches and teacher == "T1" and course == "CAI-252"
            return [(1 if ok else 0,)], -1
        if "FROM dbo.AssessmentSection WITH (UPDLOCK, HOLDLOCK)" in sql:
            return ([(55,)] if params[1] in assigned else []), -1
        if sql.startswith("INSERT INTO dbo.AssessmentSection"):
            if insert_error:
                raise insert_error
            published.add(params[1])
            next_id[0] += 1
            return [(next_id[0],)], 1
        if "FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)" in sql:
            return [(submissions,)], -1
        if sql.startswith("DELETE FROM dbo.AssessmentSection"):
            return [], delete_rowcount
        raise AssertionError(f"unexpected SQL: {sql}")
    return responder


def assign(conn, section_ids, teacher="T1", assessment_id=7):
    try:
        return svc.assign_sections(assessment_id, teacher, section_ids,
                                   connection_factory=lambda: conn)
    except Exception as exc:
        return exc


def remove(conn, section_id="SEC-A", teacher="T1"):
    try:
        return svc.remove_section(7, teacher, section_id,
                                  connection_factory=lambda: conn)
    except Exception as exc:
        return exc


class AssignSectionsTests(unittest.TestCase):
    def test_successful_assignment_atomic(self):
        conn = FakeConnection(responder_factory())
        r = assign(conn, ["SEC-A", " SEC-B "])
        self.assertEqual([a["section_id"] for a in r["assigned"]],
                         ["SEC-A", "SEC-B"])
        self.assertEqual(len(conn.statements("INSERT INTO dbo.AssessmentSection")), 2)
        self.assertEqual(conn.commits, 1)
        self.assertTrue(conn.closed)
        # assessment row locked before anything else
        self.assertIn("UPDLOCK, HOLDLOCK", conn.executed[0][0])
        self.assertIn("Assessment", conn.executed[0][0])

    def test_inserts_only_never_touch_submissions(self):
        conn = FakeConnection(responder_factory())
        assign(conn, ["SEC-A"])
        writes = [s for s, _ in conn.executed
                  if s.split()[0] in ("INSERT", "UPDATE", "DELETE")]
        self.assertTrue(all("dbo.AssessmentSection" in s for s in writes))

    def test_repeated_assignment_is_idempotent(self):
        conn = FakeConnection(responder_factory(assigned=("SEC-A",)))
        result = assign(conn, ["SEC-A"])
        self.assertTrue(result["replayed"])
        self.assertEqual(result["published_section_ids"], ["SEC-A"])
        self.assertFalse(conn.statements("INSERT"))
        self.assertEqual(conn.commits, 1)

    def test_retry_mixed_with_new_section_adds_only_new(self):
        conn = FakeConnection(responder_factory(assigned=("SEC-A",)))
        result = assign(conn, ["SEC-A", "SEC-B"])
        self.assertEqual(len(conn.statements("INSERT")), 1)
        self.assertEqual(result["published_section_ids"], ["SEC-A", "SEC-B"])

    def test_unauthorized_teacher_is_not_found(self):
        conn = FakeConnection(responder_factory(owned=False))
        exc = assign(conn, ["SEC-A"], teacher="T2")
        self.assertIsInstance(exc, svc.AssessmentNotFoundError)
        self.assertFalse(conn.statements("INSERT"))

    def test_nonexistent_assessment(self):
        conn = FakeConnection(responder_factory(owned=False))
        exc = assign(conn, ["SEC-A"], assessment_id=999)
        self.assertIsInstance(exc, svc.AssessmentNotFoundError)

    def test_nonexistent_section_404(self):
        conn = FakeConnection(responder_factory())
        exc = assign(conn, ["SEC-A", "NOPE"])
        self.assertEqual(exc.status_code, 404)
        self.assertEqual(exc.errors[0]["reason"], "SECTION_NOT_FOUND")
        self.assertFalse(conn.statements("INSERT"))

    def test_teacher_course_section_mismatch_403(self):
        conn = FakeConnection(responder_factory(sections=("SEC-A", "SEC-C"),
                                                teaches=("SEC-A",)))
        exc = assign(conn, ["SEC-A", "SEC-C"])
        self.assertEqual(exc.status_code, 403)
        self.assertEqual(exc.errors, [{
            "section_id": "SEC-C",
            "reason": "TEACHER_NOT_ASSIGNED_TO_SECTION"}])
        self.assertFalse(conn.statements("INSERT"))

    def test_authorization_uses_assessment_course_and_teacher(self):
        conn = FakeConnection(responder_factory())
        assign(conn, ["SEC-A"])
        params = [p for s, p in conn.executed
                  if "FROM dbo.SectionCourseTeacher" in s]
        self.assertEqual(params, [("SEC-A", "T1", "CAI-252")])

    def test_unique_constraint_race_maps_to_conflict(self):
        err = FakeIntegrityError(
            "23000", "[2627] Violation of UNIQUE KEY constraint "
                     "'UQ_AssessmentSection'.")
        conn = FakeConnection(responder_factory(insert_error=err))
        exc = assign(conn, ["SEC-A"])
        self.assertIsInstance(exc, svc.AssignmentConflictError)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)

    def test_unexpected_db_error_rolls_back(self):
        conn = FakeConnection(responder_factory(
            insert_error=RuntimeError("connection lost")))
        exc = assign(conn, ["SEC-A", "SEC-B"])
        self.assertIsInstance(exc, RuntimeError)
        self.assertEqual(conn.commits, 0)
        self.assertGreaterEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)

    def test_publish_refused_without_ten_test_cases(self):
        for count in (0, 9, 11):
            with self.subTest(count=count):
                conn = FakeConnection(responder_factory(test_cases=count))
                exc = assign(conn, ["SEC-A"])
                self.assertIsInstance(exc, svc.AssessmentNotReadyError)
                self.assertEqual([m["code"] for m in exc.missing],
                                 ["TEST_CASES_NOT_GENERATED"])
                self.assertFalse(conn.statements("INSERT"))
                self.assertEqual(conn.commits, 0)

    def test_publish_refused_with_incomplete_rubric(self):
        conn = FakeConnection(responder_factory(rubric=((1, "Logic", "3.00", None),)))
        exc = assign(conn, ["SEC-A"])
        self.assertEqual([m["code"] for m in exc.missing], ["RUBRIC_INCOMPLETE"])
        self.assertFalse(conn.statements("INSERT"))
        both = assign(FakeConnection(responder_factory(test_cases=0, rubric=())), ["SEC-A"])
        self.assertEqual([m["code"] for m in both.missing],
                         ["TEST_CASES_NOT_GENERATED", "RUBRIC_INCOMPLETE"])

    def test_publish_guard_runs_before_any_section_check(self):
        conn = FakeConnection(responder_factory(test_cases=0))
        assign(conn, ["SEC-A"])
        self.assertFalse(conn.statements("FROM dbo.Section_Export"))
        self.assertFalse(conn.statements("FROM dbo.SectionCourseTeacher"))

    def test_input_validation_before_db(self):
        for bad in ([], "SEC-A", ["SEC-A", "sec-a"], [""], ["x" * 21],
                    [5], ["A;DROP"], ["SEC"] * 51):
            with self.subTest(bad=bad):
                conn = FakeConnection(responder_factory())
                exc = assign(conn, bad)
                self.assertIsInstance(exc, ValueError)
                self.assertFalse(conn.executed)


class RemoveSectionTests(unittest.TestCase):
    def test_safe_removal(self):
        conn = FakeConnection(responder_factory(assigned=("SEC-A",)))
        r = remove(conn)
        self.assertTrue(r["removed"])
        self.assertEqual(len(conn.statements("DELETE FROM dbo.AssessmentSection")), 1)
        self.assertEqual(conn.commits, 1)

    def test_removal_blocked_when_submissions_exist(self):
        conn = FakeConnection(responder_factory(assigned=("SEC-A",),
                                                submissions=3))
        exc = remove(conn)
        self.assertIsInstance(exc, svc.AssignmentConflictError)
        self.assertFalse(conn.statements("DELETE"))
        self.assertEqual(conn.commits, 0)

    def test_submission_check_takes_range_lock(self):
        conn = FakeConnection(responder_factory(assigned=("SEC-A",)))
        remove(conn)
        self.assertTrue(conn.statements(
            "FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK)"))

    def test_remove_unassigned_section_404(self):
        conn = FakeConnection(responder_factory())
        self.assertIsInstance(remove(conn), svc.AssessmentNotFoundError)

    def test_remove_by_other_teacher_404(self):
        conn = FakeConnection(responder_factory(owned=False,
                                                assigned=("SEC-A",)))
        self.assertIsInstance(remove(conn, teacher="T2"),
                              svc.AssessmentNotFoundError)
        self.assertFalse(conn.statements("DELETE"))

    def test_fk_violation_on_delete_is_conflict(self):
        def responder(sql, params):
            if sql.startswith("DELETE FROM dbo.AssessmentSection"):
                raise FakeIntegrityError("23000", "[547] DELETE statement "
                                         "conflicted with REFERENCE constraint")
            return responder_factory(assigned=("SEC-A",))(sql, params)
        conn = FakeConnection(responder)
        self.assertIsInstance(remove(conn), svc.AssignmentConflictError)
        self.assertEqual(conn.commits, 0)


# ---------------------------------------------------------------- routes

flask_app = app_module.app
flask_app.config["TESTING"] = True


def headers(role="teacher", identity="T1"):
    with flask_app.app_context():
        tok = create_access_token(identity=identity,
                                  additional_claims={"role": role})
    return {"Authorization": f"Bearer {tok}"}


class SectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_routes_require_jwt(self):
        self.assertEqual(self.client.get("/api/assessments/7/sections")
                         .status_code, 401)

    def test_students_forbidden_everywhere(self):
        h = headers(role="student")
        calls = [
            self.client.get("/api/assessments/7/sections", headers=h),
            self.client.get("/api/assessments/7/assignable-sections", headers=h),
            self.client.post("/api/assessments/7/sections",
                             json={"section_ids": ["SEC-A"]}, headers=h),
            self.client.delete("/api/assessments/7/sections/SEC-A", headers=h),
        ]
        self.assertEqual([r.status_code for r in calls], [403] * 4)

    def test_post_201_with_teacher_from_jwt(self):
        with patch.object(svc, "assign_sections",
                          return_value={"assessment_id": 7, "assigned": []}) as m:
            r = self.client.post("/api/assessments/7/sections",
                                 json={"section_ids": ["SEC-A"]},
                                 headers=headers(identity="T77"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m.call_args[0], (7, "T77", ["SEC-A"]))

    def test_post_rejects_extra_fields(self):
        r = self.client.post("/api/assessments/7/sections",
                             json={"section_ids": ["A"], "teacher_id": "X"},
                             headers=headers())
        self.assertEqual(r.status_code, 400)

    def test_post_error_details_returned(self):
        err = svc.SectionAssignmentError(
            [{"section_id": "SEC-C", "reason": "TEACHER_NOT_ASSIGNED_TO_SECTION"}])
        with patch.object(svc, "assign_sections", side_effect=err):
            r = self.client.post("/api/assessments/7/sections",
                                 json={"section_ids": ["SEC-C"]},
                                 headers=headers())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["errors"][0]["section_id"], "SEC-C")

    def test_not_ready_409_lists_missing(self):
        err = svc.AssessmentNotReadyError(
            [{"code": "TEST_CASES_NOT_GENERATED", "detail": "0 of 10 test cases"}])
        with patch.object(svc, "assign_sections", side_effect=err):
            r = self.client.post("/api/assessments/7/sections",
                                 json={"section_ids": ["SEC-A"]}, headers=headers())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "ASSESSMENT_NOT_READY")
        self.assertEqual(r.get_json()["missing"][0]["code"], "TEST_CASES_NOT_GENERATED")

    def test_delete_conflict_409(self):
        with patch.object(svc, "remove_section",
                          side_effect=svc.AssignmentConflictError("x")):
            r = self.client.delete("/api/assessments/7/sections/SEC-A",
                                   headers=headers())
        self.assertEqual(r.status_code, 409)

    def test_not_found_404(self):
        with patch.object(svc, "list_assigned_sections",
                          side_effect=svc.AssessmentNotFoundError("Assessment not found")):
            r = self.client.get("/api/assessments/7/sections", headers=headers())
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
