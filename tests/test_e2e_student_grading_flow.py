"""
End-to-end flow through the REAL Flask routes, services, grading pipeline,
persistence services and the development fixture grader:

  teacher login -> assign section -> student login -> view assessment ->
  submit approved fixture -> (duplicate refused) -> dev fixture grader
  (recorded evidence) -> teacher review -> teacher finalize -> student
  sees only the finalized result.

The database is tests.e2e_world (stateful, transactional, in memory);
Ollama is a parsed fake; the compiler and subprocess fail if touched.
No SQL Server, no C++ compilation or execution.
"""

import unittest
from decimal import Decimal
from unittest.mock import patch

from tests.e2e_world import World

import cpp_compilation_service
import db
from dev_tools import e2e_api_client
from dev_tools import fixture_submission_grader as fsg
from dev_tools import recorded_evidence_grading as reg
from dev_tools import trusted_execution_harness as teh
from evaluation_review_service import TEACHER_ONLY_MARKER
from grading_job_claim_service import claim_grading_job_by_id
from ollama_grading_service import parse_criterion_only_response
from rubric_ai_grader import RubricGradingUnavailable, grade_rubric_with_ollama

import app as app_module

HAVE_REPORTS = all((reg.REPORTS / r["report"]).exists()
                   for r in reg.REVIEWED_RECORDS.values())
TESTS = [{"test_number": t[0], "input_data": t[3], "expected_output": t[4]}
         for t in World().tests]


def fake_rubric(logic, quality, cited=None,
                text="Trial division loop over d; exits early on a divisor."):
    calls = {"count": 0}

    def grader(**kwargs):
        return parse_criterion_only_response({
            "criteria": [{"criterion_name": "Logic & Algorithm", "mark": logic,
                          "explanation": text, "cited_failed_tests": cited or []},
                         {"criterion_name": "Code Quality", "mark": quality,
                          "explanation": "Readable names and structure."}],
            "overall_feedback": "ok", "issues": [],
            "improvement_suggestions": []}, kwargs["rubric"])

    def grade_rubric(context, rubric, alloc, source, evidence):
        calls["count"] += 1
        calls["evidence"] = evidence
        return grade_rubric_with_ollama(context, rubric, alloc, source, evidence,
                                        grader=grader)
    return grade_rubric, calls


def must_not_call_ollama(*args, **kwargs):
    raise AssertionError("Ollama must not be called")


class Flow:
    """Drives one World through the real Flask app."""

    def __init__(self, test):
        self.test = test
        self.world = World()
        self.client = app_module.app.test_client()
        boom = AssertionError("compilation/execution attempted")
        self.patches = [
            patch.object(db.pyodbc, "connect", side_effect=self.world.connect),
            patch.object(cpp_compilation_service, "compile_cpp_source",
                         side_effect=boom),
            patch("subprocess.run", side_effect=boom),
            patch("subprocess.Popen", side_effect=boom)]
        for p in self.patches:
            p.start()
            test.addCleanup(p.stop)

    # authentication through the real login endpoints
    def login_teacher(self, teacher_id="BIIT235"):
        _, email, password, _ = self.world.teachers[teacher_id]
        r = self.client.post("/api/teacher/login",
                             json={"email": email, "password": password})
        self.test.assertEqual(r.status_code, 200)
        return {"Authorization": "Bearer " + r.get_json()["access_token"]}

    def login_student(self, student_id="S-1"):
        _, email, password = self.world.students[student_id]
        r = self.client.post("/api/student/login",
                             json={"email": email, "password": password})
        self.test.assertEqual(r.status_code, 200)
        return {"Authorization": "Bearer " + r.get_json()["access_token"]}

    def assign(self, teacher, sections=("SEC000001",)):
        return self.client.post("/api/assessments/5/sections", headers=teacher,
                                json={"section_ids": list(sections)})

    def submit(self, student, fixture=None, source=None):
        if source is None:
            source = teh.fixture_path(fixture).read_bytes().decode("utf-8")
        return self.client.post("/api/assessments/5/submissions",
                                headers=student,
                                json={"source_code": source,
                                      "file_name": "solution.cpp"})

    def grade(self, submission_id, grade_rubric):
        plan = fsg.build_plan(submission_id)
        result = fsg.grade(plan, "dev-fixture-grader-test",
                           overrides={"grade_rubric": grade_rubric})
        return plan, result

    def student_result(self, student):
        r = self.client.get("/api/student/assessments/5/submission",
                            headers=student)
        return r.status_code, r.get_json()


@unittest.skipUnless(HAVE_REPORTS, "recorded trusted-fixture reports not present")
class EndToEndFlowTests(unittest.TestCase):

    def setUp(self):
        self.flow = Flow(self)
        self.teacher = self.flow.login_teacher()
        r = self.flow.assign(self.teacher)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.student = self.flow.login_student("S-1")

    def submit_ok(self, fixture, student=None):
        r = self.flow.submit(student or self.student, fixture)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["submission"]

    def test_buggy_fixture_full_flow(self):
        w, client = self.flow.world, self.flow.client

        # student sees the assessment, open, with no hidden test data
        r = client.get("/api/student/assessments", headers=self.student)
        items = r.get_json()["assessments"]
        self.assertEqual([i["assessment_id"] for i in items], [5])
        self.assertTrue(items[0]["open_for_submissions"])
        self.assertEqual(e2e_api_client.scan_student_payload(r.get_json(), TESTS), [])

        sub = self.submit_ok("prime_buggy_square")
        sid = sub["submission_id"]
        self.assertEqual((sub["student_id"], sub["section_id"]), ("S-1", "SEC000001"))
        self.assertEqual(len(w.subs), 1)
        self.assertEqual([(j["submission_id"], j["status"], j["attempts"])
                          for j in w.jobs.values()], [(sid, "PENDING", 0)])

        # duplicate refused, nothing new written
        self.assertEqual(self.flow.submit(self.student, "prime_buggy_square")
                         .status_code, 409)
        self.assertEqual((len(w.subs), len(w.jobs)), (1, 1))

        # before grading: student sees SUBMITTED, no result
        _, body = self.flow.student_result(self.student)
        self.assertEqual(body["submission"]["status"], "SUBMITTED")
        self.assertIsNone(body["submission"]["result"])

        grade_rubric, calls = fake_rubric(2, 2, text=(
            "Loop structure fine; N = 994009 printed YES."))
        plan, result = self.flow.grade(sid, grade_rubric)
        self.assertEqual((plan["fixture"], plan["expected_outcome"]),
                         ("prime_buggy_square", "GRADED"))
        self.assertEqual(result["outcome"], "GRADED")
        self.assertEqual(result["replay"]["real_execution"], False)
        self.assertEqual(calls["evidence"]["provenance"], "trusted_fixture_recorded")

        evaluation = w.evals[sid]
        self.assertEqual(evaluation["suggested"], Decimal("8.00"))  # 4.00 + 4.00
        self.assertEqual((evaluation["passed"], evaluation["run"]), (8, 10))
        self.assertEqual(evaluation["auto_status"], "NEEDS_REVIEW")
        self.assertIsNone(evaluation["final"])
        self.assertTrue(evaluation["feedback"].startswith("[DEVELOPMENT TEST"))
        self.assertIn("NOT independently compiled or executed",
                      evaluation["feedback"])
        self.assertEqual(sorted(c[2] for c in w.crits),
                         ["Code Quality", "Logic & Algorithm"])
        self.assertEqual(w.jobs[1]["status"], "COMPLETED")

        # graded but not finalized: student still sees nothing
        _, body = self.flow.student_result(self.student)
        self.assertEqual(body["submission"]["status"], "UNDER_REVIEW")
        self.assertIsNone(body["submission"]["result"])
        self.assertNotIn("8.00", str(body))

        # teacher review shows evidence, warnings and suggested marks
        r = client.get("/api/assessments/5/submissions", headers=self.teacher)
        row = r.get_json()["submissions"][0]
        self.assertEqual((row["grading_status"], row["suggested_score"]),
                         ("AUTO_GRADED_NEEDS_REVIEW", "8.00"))
        r = client.get(f"/api/submissions/{sid}/evaluation", headers=self.teacher)
        ev = r.get_json()["submission"]["evaluation"]
        self.assertIn(TEACHER_ONLY_MARKER, ev["auto_evaluation_feedback"])
        self.assertIn("FAILURES_NOT_EXPLAINED", ev["auto_evaluation_feedback"])
        self.assertIn("trusted_fixture_recorded", ev["auto_evaluation_feedback"])

        # teacher chooses the final mark
        r = client.post(f"/api/submissions/{sid}/evaluation/finalize",
                        headers=self.teacher,
                        json={"final_score": "7.50",
                              "instructor_feedback": "Check the square case."})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["evaluation"]["mode"],
                         "REVIEWED_AUTOMATIC_GRADE")
        r = client.post(f"/api/submissions/{sid}/evaluation/finalize",
                        headers=self.teacher, json={"final_score": "9"})
        self.assertEqual(r.status_code, 409)

        status, body = self.flow.student_result(self.student)
        result = body["submission"]["result"]
        self.assertEqual(body["submission"]["status"], "RESULT_PUBLISHED")
        self.assertEqual((result["final_score"], result["tests_passed"],
                          result["tests_run"]), ("7.50", 8, 10))
        self.assertNotIn("automated_feedback", result)
        self.assertNotIn(TEACHER_ONLY_MARKER, str(body))
        self.assertNotIn("994009", str(body))
        self.assertEqual(e2e_api_client.scan_student_payload(body, TESTS), [])
        self.assertNotIn("suggested", str(body))

    def test_correct_fixture_completed(self):
        sid = self.submit_ok("prime_correct")["submission_id"]
        grade_rubric, _ = fake_rubric(3, 2)
        _, result = self.flow.grade(sid, grade_rubric)
        e = self.flow.world.evals[sid]
        self.assertEqual((result["outcome"], e["suggested"], e["passed"],
                          e["auto_status"]),
                         ("GRADED", Decimal("10.00"), 10, "COMPLETED"))

    def test_compile_error_fixture_skips_ollama(self):
        sid = self.submit_ok("prime_compile_error")["submission_id"]
        plan, result = self.flow.grade(sid, must_not_call_ollama)
        self.assertFalse(plan["ollama_will_be_called"])
        self.assertEqual(result["outcome"], "COMPILATION_FAILED")
        self.assertEqual(result["replay"]["run_calls"], 0)
        e = self.flow.world.evals[sid]
        self.assertEqual((e["suggested"], e["passed"], e["run"], e["auto_status"]),
                         (Decimal("0"), 0, 0, "COMPILATION_FAILED"))
        self.assertIn("student_solution.cpp:6:19: error:", e["feedback"])
        self.assertNotIn("Users", e["feedback"])
        self.assertFalse(self.flow.world.crits)
        # teacher still decides; student sees nothing until then
        _, body = self.flow.student_result(self.student)
        self.assertIsNone(body["submission"]["result"])
        r = self.flow.client.post(f"/api/submissions/{sid}/evaluation/finalize",
                                  headers=self.teacher, json={"final_score": 0})
        self.assertEqual(r.status_code, 200)

    def test_three_students_three_fixtures_isolated(self):
        subs = {}
        for student, fixture in (("S-1", "prime_correct"),
                                 ("S-2", "prime_buggy_square"),
                                 ("S-3", "prime_compile_error")):
            token = self.flow.login_student(student)
            subs[fixture] = self.submit_ok(fixture, token)["submission_id"]
        grade_rubric, _ = fake_rubric(2, 2, cited=[6, 9],
                                      text="Loop stops before d*d == n.")
        self.flow.grade(subs["prime_buggy_square"], grade_rubric)
        jobs = {j["submission_id"]: j["status"] for j in self.flow.world.jobs.values()}
        self.assertEqual(jobs, {subs["prime_correct"]: "PENDING",
                                subs["prime_buggy_square"]: "COMPLETED",
                                subs["prime_compile_error"]: "PENDING"})
        self.assertEqual(list(self.flow.world.evals), [subs["prime_buggy_square"]])

    # ---- refusals: arbitrary code, tampering, infrastructure, ownership

    def test_arbitrary_source_is_never_graded(self):
        source = teh.fixture_path("prime_correct").read_bytes().decode() + " "
        r = self.flow.submit(self.student, source=source)
        self.assertEqual(r.status_code, 201)       # the API accepts any code
        sid = r.get_json()["submission"]["submission_id"]
        with self.assertRaisesRegex(fsg.SubmissionNotEligible,
                                    "not an approved fixture"):
            fsg.build_plan(sid)
        job = self.flow.world.jobs[1]
        self.assertEqual((job["status"], job["attempts"]), ("PENDING", 0))

    def test_source_tampered_after_plan_released_not_zeroed(self):
        sid = self.submit_ok("prime_correct")["submission_id"]
        plan = fsg.build_plan(sid)
        w = self.flow.world
        w.subs[sid] = {**w.subs[sid], "source": w.subs[sid]["source"] + "//x"}
        result = fsg.grade(plan, "dev-fixture-grader-test",
                           overrides={"grade_rubric": must_not_call_ollama})
        self.assertEqual((result["outcome"], result["error_code"]),
                         ("INFRASTRUCTURE_FAILURE", "SANDBOX_COMPILE_FAILURE"))
        self.assertNotIn(sid, w.evals)
        job = w.jobs[1]
        self.assertEqual((job["status"], job["attempts"]), ("PENDING", 1))
        self.assertIn("does not match the approved fixture", job["last_error"])

    def test_ollama_unavailable_released_not_zeroed(self):
        sid = self.submit_ok("prime_correct")["submission_id"]

        def down(*a, **k):
            raise RubricGradingUnavailable("connection refused")

        _, result = self.flow.grade(sid, down)
        self.assertEqual(result["error_code"], "AI_GRADING_UNAVAILABLE")
        self.assertNotIn(sid, self.flow.world.evals)
        self.assertEqual(self.flow.world.jobs[1]["status"], "PENDING")
        _, body = self.flow.student_result(self.student)
        self.assertIsNone(body["submission"]["result"])

    def test_lease_lost_before_completion_writes_nothing(self):
        sid = self.submit_ok("prime_correct")["submission_id"]
        grade_rubric, _ = fake_rubric(3, 2)

        def slow(*args):
            self.flow.world.now = self.flow.world.now.replace(hour=23)
            return grade_rubric(*args)

        _, result = self.flow.grade(sid, slow)
        self.assertEqual(result["outcome"], "OWNERSHIP_LOST")
        self.assertNotIn(sid, self.flow.world.evals)
        self.assertFalse(self.flow.world.crits)

    def test_job_claimed_once(self):
        sid = self.submit_ok("prime_correct")["submission_id"]
        grade_rubric, _ = fake_rubric(3, 2)
        self.flow.grade(sid, grade_rubric)
        self.assertIsNone(claim_grading_job_by_id(1, "someone-else"))
        with self.assertRaisesRegex(fsg.SubmissionNotEligible, "COMPLETED"):
            fsg.build_plan(sid)

    def test_recorded_evidence_must_match_database_tests(self):
        sid = self.submit_ok("prime_correct")["submission_id"]
        w = self.flow.world
        w.tests[2] = (*w.tests[2][:3], "3", w.tests[2][4])
        with self.assertRaises(reg.RecordIntegrityError):
            fsg.build_plan(sid)

    def test_production_worker_still_claims_nothing(self):
        self.submit_ok("prime_correct")
        import grading_pipeline
        result = grading_pipeline.run_worker_once("production-worker")
        self.assertEqual(result["outcome"], "SECURE_EXECUTION_DISABLED")
        self.assertEqual(self.flow.world.jobs[1]["status"], "PENDING")


class AtomicSubmissionTests(unittest.TestCase):

    def test_grading_job_failure_rolls_back_submission(self):
        flow = Flow(self)
        flow.assign(flow.login_teacher())
        flow.world.fail_job_insert = True
        r = flow.submit(flow.login_student("S-1"), "prime_correct")
        self.assertEqual(r.status_code, 500)
        self.assertEqual((flow.world.subs, flow.world.jobs), ({}, {}))


class AuthorizationTests(unittest.TestCase):

    def setUp(self):
        self.flow = Flow(self)
        self.teacher = self.flow.login_teacher()
        self.other_teacher = self.flow.login_teacher("BIIT999")
        self.student = self.flow.login_student("S-1")

    def test_wrong_password_refused(self):
        r = self.flow.client.post("/api/student/login", json={
            "email": "s1@example.test", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_assignment_rules(self):
        self.assertEqual(self.flow.assign(self.student).status_code, 403)
        self.assertEqual(self.flow.assign(self.other_teacher).status_code, 404)
        r = self.flow.assign(self.teacher, ("SEC000003",))   # not taught by BIIT235
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.flow.world.asec, {})
        self.assertEqual(self.flow.assign(self.teacher).status_code, 201)
        self.assertEqual(self.flow.assign(self.teacher).status_code, 409)

    def test_unassigned_or_unenrolled_students_cannot_see_or_submit(self):
        self.assertEqual(self.flow.submit(self.student, "prime_correct")
                         .status_code, 403)                    # not assigned yet
        self.flow.assign(self.teacher)
        outsider = self.flow.login_student("S-OUT")
        r = self.flow.client.get("/api/student/assessments", headers=outsider)
        self.assertEqual(r.get_json()["assessments"], [])
        self.assertEqual(self.flow.submit(outsider, "prime_correct").status_code, 403)
        self.assertEqual(self.flow.world.subs, {})

    def test_role_and_identity_enforced(self):
        self.flow.assign(self.teacher)
        c = self.flow.client
        self.assertEqual(self.flow.submit(self.teacher, "prime_correct").status_code, 403)
        r = c.post("/api/assessments/5/submissions", headers=self.student,
                   json={"source_code": "x", "student_id": "S-2"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c.post("/api/assessments/5/submissions",
                                json={"source_code": "x"}).status_code, 401)
        sid = self.flow.submit(self.student, "prime_correct") \
            .get_json()["submission"]["submission_id"]
        for url in (f"/api/submissions/{sid}/evaluation",
                    "/api/assessments/5/submissions"):
            self.assertEqual(c.get(url, headers=self.student).status_code, 403)
            self.assertEqual(c.get(url, headers=self.other_teacher).status_code, 404)
        r = c.post(f"/api/submissions/{sid}/evaluation/finalize",
                   headers=self.other_teacher, json={"final_score": 10})
        self.assertEqual(r.status_code, 404)
        r = c.post(f"/api/submissions/{sid}/evaluation/finalize",
                   headers=self.student, json={"final_score": 10})
        self.assertEqual(r.status_code, 403)
        # cannot finalize before automatic grading finished
        r = c.post(f"/api/submissions/{sid}/evaluation/finalize",
                   headers=self.teacher, json={"final_score": 10})
        self.assertEqual(r.status_code, 409)
        # another student cannot read this submission
        r = c.get("/api/student/assessments/5/submission",
                  headers=self.flow.login_student("S-2"))
        self.assertEqual(r.status_code, 404)

    def test_hidden_test_cases_not_reachable_by_students(self):
        self.flow.assign(self.teacher)
        connections = self.flow.world.connections
        r = self.flow.client.get("/api/assessments/5/test-cases",
                                 headers=self.student)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.flow.world.connections, connections)


class IsolationTests(unittest.TestCase):

    def test_dev_tools_not_reachable_from_flask(self):
        for rule in app_module.app.url_map.iter_rules():
            view = app_module.app.view_functions[rule.endpoint]
            self.assertFalse(view.__module__.startswith("dev_tools"), rule.rule)
        from pathlib import Path
        from tests.fakes import BACKEND
        for path in Path(BACKEND).glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in ("fixture_submission_grader", "e2e_api_client",
                         "recorded_evidence_grading", "trusted_execution_harness"):
                self.assertNotIn(name, text, f"{path.name} imports {name}")

    def test_match_fixture_requires_exact_bytes(self):
        source = teh.fixture_path("prime_correct").read_bytes().decode("utf-8")
        self.assertEqual(fsg.match_fixture(source)[0], "prime_correct")
        self.assertIsNone(fsg.match_fixture(source.replace("\n", "\r\n"))[0])
        self.assertIsNone(fsg.match_fixture(source + "\n")[0])

    def test_scan_flags_hidden_values(self):
        self.assertEqual(e2e_api_client.scan_student_payload(
            {"text": "fails for 994009"}, TESTS), ["test 9 input"])
        self.assertEqual(e2e_api_client.scan_student_payload(
            {"text": "prints YES for 2"}, TESTS), [])


if __name__ == "__main__":
    unittest.main()
