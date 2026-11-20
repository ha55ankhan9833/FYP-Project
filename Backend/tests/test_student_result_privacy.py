"""
Student result privacy, teacher criterion-level approval and redaction.
Real Flask routes / services / pipeline / fixture grader against the
in-memory tests.e2e_world database. No SQL Server, no C++ execution.
"""

import json
import unittest
from decimal import Decimal

from tests.test_e2e_student_grading_flow import (HAVE_REPORTS, TESTS, Flow,
                                                 fake_rubric, must_not_call_ollama)

import student_result_builder as srb
from dev_tools import e2e_api_client

# Values that must never appear in any student response.
FORBIDDEN_TEXT = ("suggested", "Suggested total", "Reliability flags",
                  "FAILURES_NOT_EXPLAINED", "verification report", "teacher only",
                  "trusted_fixture_recorded", "DEVELOPMENT TEST", "provenance",
                  "Failed test numbers", "[hidden test input]",
                  "AI rubric assessment", "explanation")
AI_TEXT = "Loop is fine; N = 994009 printed YES and 2 edge cases pass."


def student_texts(flow, token):
    """Every student-facing response for this student, as text."""
    c = flow.client
    return json.dumps([c.get("/api/student/assessments", headers=token).get_json(),
                       c.get("/api/student/assessments/5/submission",
                             headers=token).get_json()])


@unittest.skipUnless(HAVE_REPORTS, "recorded trusted-fixture reports not present")
class StudentResultPrivacyTests(unittest.TestCase):

    def setUp(self):
        self.flow = Flow(self)
        self.teacher = self.flow.login_teacher()
        self.assertEqual(self.flow.assign(self.teacher).status_code, 201)
        self.students = {s: self.flow.login_student(s)
                         for s in ("S-1", "S-2", "S-3")}
        self.sids = {}
        for student, fixture in (("S-1", "prime_correct"),
                                 ("S-2", "prime_buggy_square"),
                                 ("S-3", "prime_compile_error")):
            r = self.flow.submit(self.students[student], fixture)
            self.sids[fixture] = r.get_json()["submission"]["submission_id"]
        self.flow.grade(self.sids["prime_correct"], fake_rubric(3, 2)[0])
        self.flow.grade(self.sids["prime_buggy_square"],
                        fake_rubric(2, 2, text=AI_TEXT)[0])
        self.flow.grade(self.sids["prime_compile_error"], must_not_call_ollama)

    def finalize(self, sid, score, token=None, **extra):
        return self.flow.client.post(
            f"/api/submissions/{sid}/evaluation/finalize",
            headers=token or self.teacher, json={"final_score": score, **extra})

    def result(self, student):
        r = self.flow.client.get("/api/student/assessments/5/submission",
                                 headers=self.students[student])
        return r.get_json()["submission"]

    # --- privacy -------------------------------------------------------

    def test_no_internal_text_in_any_student_response_before_or_after(self):
        for student in self.students:
            before = student_texts(self.flow, self.students[student])
            # 8.00 is S-2's suggested total; 10.00 (total marks) is public.
            for text in FORBIDDEN_TEXT + ("8.00",):
                self.assertNotIn(text, before, f"{student} before: {text}")
        self.assertEqual(self.finalize(self.sids["prime_buggy_square"], "7.00")
                         .status_code, 200)
        self.assertEqual(self.finalize(self.sids["prime_correct"], "10.00")
                         .status_code, 200)
        self.assertEqual(self.finalize(self.sids["prime_compile_error"], "0")
                         .status_code, 200)
        for student in self.students:
            text = student_texts(self.flow, self.students[student])
            for forbidden in FORBIDDEN_TEXT:
                self.assertNotIn(forbidden, text, f"{student}: {forbidden}")
            self.assertEqual(e2e_api_client.scan_student_payload(
                json.loads(text), TESTS), [])
            for t in TESTS:
                if len(t["input_data"]) >= 3:
                    self.assertNotIn(t["input_data"], text)

    def test_student_sees_only_final_marks_and_reconciled_breakdown(self):
        self.finalize(self.sids["prime_buggy_square"], "7.00")
        self.finalize(self.sids["prime_correct"], "10.00")
        buggy, correct = self.result("S-2")["result"], self.result("S-1")["result"]
        # buggy: AI said 4.00 + 2.00 + 2.00 = 8.00, teacher published 7.00
        self.assertEqual((buggy["final_score"], buggy["test_marks"],
                          buggy["rubric_marks"], buggy["rubric_source"],
                          buggy["criteria"]),
                         ("7.00", "4.00", "3.00", "FINAL_TOTAL_ONLY", []))
        self.assertNotIn("8.00", json.dumps(buggy))
        # correct: AI 3 + 2 with 5.00 test marks reconciles with 10.00
        self.assertEqual(correct["rubric_source"],
                         "AI_RECOMMENDATION_ACCEPTED_BY_TEACHER")
        self.assertEqual(Decimal(correct["test_marks"]) + sum(
            Decimal(c["marks"]) for c in correct["criteria"]), Decimal("10.00"))
        self.assertEqual(set(correct["criteria"][0]),
                         {"criterion_name", "marks", "max_marks"})

    def test_short_hidden_inputs_do_not_mangle_text(self):
        # Hidden inputs include "1", "2" and "4"; marks such as 1.00 / 2.00
        # and counts must stay readable, with no placeholder text.
        self.flow.world.approval_table = True
        r = self.finalize(self.sids["prime_buggy_square"], "7.00", criteria=[
            {"criterion_name": "Logic & Algorithm", "marks": "1.00"},
            {"criterion_name": "Code Quality", "marks": 2}])
        self.assertEqual(r.status_code, 200, r.get_json())
        result = self.result("S-2")["result"]
        self.assertEqual([c["marks"] for c in result["criteria"]], ["1.00", "2.00"])
        self.assertEqual(result["summary"][0], "8 of 10 hidden tests passed.")
        self.assertNotIn("[hidden test input]", json.dumps(result))

    def test_compilation_diagnostics_without_label_or_provenance(self):
        self.finalize(self.sids["prime_compile_error"], "0")
        result = self.result("S-3")["result"]
        self.assertEqual(result["outcome"], "COMPILATION_FAILED")
        self.assertIn("student_solution.cpp:6:19: error:", result["compilation_diagnostics"])
        self.assertNotIn("DEVELOPMENT TEST", json.dumps(result))
        self.assertNotIn("Users", result["compilation_diagnostics"])

    def test_other_students_cannot_read_each_other(self):
        for sid in self.sids.values():
            self.finalize(sid, "0" if sid == self.sids["prime_compile_error"]
                          else ("10.00" if sid == self.sids["prime_correct"] else "7.00"))
        seen = {s: self.result(s)["submission_id"] for s in self.students}
        self.assertEqual(seen, {"S-1": self.sids["prime_correct"],
                                "S-2": self.sids["prime_buggy_square"],
                                "S-3": self.sids["prime_compile_error"]})
        for student, token in self.students.items():
            for sid in self.sids.values():
                r = self.flow.client.get(f"/api/submissions/{sid}/evaluation",
                                         headers=token)
                self.assertEqual(r.status_code, 403)
        text = json.dumps(self.result("S-1"))
        self.assertNotIn("7.00", text)          # S-2's mark

    # --- teacher view and overrides --------------------------------------

    def test_teacher_sees_ai_recommendation_and_teacher_decision(self):
        self.flow.world.approval_table = True
        sid = self.sids["prime_buggy_square"]
        r = self.finalize(sid, "7.00", instructor_feedback="Square root case.",
                          criteria=[{"criterion_name": "Logic & Algorithm",
                                     "marks": "1.00", "comment": "d*d < n bug"},
                                    {"criterion_name": "Code Quality", "marks": "2.00"}])
        self.assertEqual(r.get_json()["evaluation"]["criteria_approved"], 2)
        ev = self.flow.client.get(f"/api/submissions/{sid}/evaluation",
                                  headers=self.teacher).get_json()["submission"]["evaluation"]
        self.assertEqual([(c["criterion_name"], c["rating"]) for c in ev["ai_criteria"]],
                         [("Logic & Algorithm", "2.00"), ("Code Quality", "2.00")])
        self.assertEqual([(c["criterion_name"], c["approved_marks"], c["approved_by"])
                          for c in ev["teacher_criteria"]],
                         [("Logic & Algorithm", "1.00", "BIIT235"),
                          ("Code Quality", "2.00", "BIIT235")])
        self.assertEqual((ev["suggested_score"], ev["final_score"],
                          ev["verified_test_marks"]), ("8.00", "7.00", "4.00"))
        self.assertIn("FAILURES_NOT_EXPLAINED", ev["auto_evaluation_feedback"])
        # AI rows are the untouched audit trail
        self.assertEqual(sorted((c[2], c[3]) for c in self.flow.world.crits
                                if c[1] == sid),
                         [("Code Quality", Decimal("2.00")),
                          ("Logic & Algorithm", Decimal("2.00"))])
        # student sees only the teacher-approved marks
        result = self.result("S-2")["result"]
        self.assertEqual((result["rubric_source"], result["rubric_marks"]),
                         ("TEACHER_APPROVED", "3.00"))
        self.assertEqual([(c["criterion_name"], c["marks"], c["max_marks"])
                          for c in result["criteria"]],
                         [("Logic & Algorithm", "1.00", "3.00"),
                          ("Code Quality", "2.00", "2.00")])

    def test_override_validation_rejects_and_writes_nothing(self):
        self.flow.world.approval_table = True
        sid = self.sids["prime_buggy_square"]
        bad = [
            ("7.00", [{"criterion_name": "Logic & Algorithm", "marks": "1.00"}]),
            ("8.00", [{"criterion_name": "Logic & Algorithm", "marks": "4.00"},
                      {"criterion_name": "Code Quality", "marks": "0"}]),
            ("8.00", [{"criterion_name": "Logic & Algorithm", "marks": "2.00"},
                      {"criterion_name": "Code Quality", "marks": "1.00"}]),
            ("7.00", [{"criterion_name": "Logic & Algorithm", "marks": "1.00"},
                      {"criterion_name": "Logic & Algorithm", "marks": "2.00"}]),
            ("7.00", [{"criterion_name": "Style", "marks": "1.00"},
                      {"criterion_name": "Code Quality", "marks": "2.00"}]),
            ("7.00", [{"criterion_name": "Logic & Algorithm", "marks": "1.005"},
                      {"criterion_name": "Code Quality", "marks": "2.00"}]),
            ("7.00", [{"criterion_name": "Logic & Algorithm", "marks": True},
                      {"criterion_name": "Code Quality", "marks": "2.00"}]),
            ("7.00", "not a list"),
        ]
        for score, criteria in bad:
            with self.subTest(score=score, criteria=criteria):
                r = self.finalize(sid, score, criteria=criteria)
                self.assertEqual(r.status_code, 400, r.get_json())
        e = self.flow.world.evals[sid]
        self.assertEqual((e["result_status"], e["final"], self.flow.world.approvals),
                         (None, None, []))

    def test_unauthorized_or_unavailable_overrides_rejected(self):
        sid = self.sids["prime_buggy_square"]
        criteria = [{"criterion_name": "Logic & Algorithm", "marks": "1.00"},
                    {"criterion_name": "Code Quality", "marks": "2.00"}]
        # table not migrated yet -> 409, nothing written
        self.assertEqual(self.finalize(sid, "7.00", criteria=criteria).status_code, 409)
        self.flow.world.approval_table = True
        self.assertEqual(self.finalize(sid, "7.00", self.students["S-2"],
                                       criteria=criteria).status_code, 403)
        other = self.flow.login_teacher("BIIT999")
        self.assertEqual(self.finalize(sid, "7.00", other, criteria=criteria)
                         .status_code, 404)
        r = self.flow.client.post(f"/api/submissions/{sid}/evaluation/finalize",
                                  headers=self.teacher,
                                  json={"final_score": "7", "rubric": criteria})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.flow.world.approvals, [])
        self.assertIsNone(self.flow.world.evals[sid]["result_status"])

    def test_finalized_grades_unchanged_and_duplicates_rejected(self):
        sid = self.sids["prime_buggy_square"]
        self.assertEqual(self.finalize(sid, "7.00").status_code, 200)   # old API
        snapshot = dict(self.flow.world.evals[sid])
        self.flow.world.approval_table = True
        for body in ({"final_score": "7.00"},
                     {"final_score": "7.00", "criteria": [
                         {"criterion_name": "Logic & Algorithm", "marks": "1.00"},
                         {"criterion_name": "Code Quality", "marks": "2.00"}]}):
            r = self.flow.client.post(f"/api/submissions/{sid}/evaluation/finalize",
                                      headers=self.teacher, json=body)
            self.assertEqual(r.status_code, 409)
        self.assertEqual(self.flow.world.evals[sid], snapshot)
        self.assertEqual(self.flow.world.approvals, [])
        # published breakdown never contradicts the final mark
        result = self.result("S-2")["result"]
        self.assertEqual(result["rubric_source"], "FINAL_TOTAL_ONLY")

    def test_instructor_feedback_cannot_quote_hidden_tests(self):
        sid = self.sids["prime_buggy_square"]
        r = self.finalize(sid, "7.00", instructor_feedback="Fails for N = 994009.")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.flow.world.evals[sid]["result_status"])
        r = self.finalize(sid, "7.00", instructor_feedback=(
            "Logic 1/3: the loop skips the square-root divisor; 2 marks for style."))
        self.assertEqual(r.status_code, 200)


class BuilderUnitTests(unittest.TestCase):

    def build(self, **overrides):
        values = dict(final_score="7.00", total_marks="10.00",
                      validation_percentage="50.00", auto_status="NEEDS_REVIEW",
                      tests_passed=8, tests_run=10, instructor_feedback=None,
                      stored_feedback="[DEVELOPMENT TEST x]\nSuggested total: 8.00",
                      rubric_rows=[{"criterion_name": "Logic", "max_marks": "3.00"},
                                   {"criterion_name": "Style", "max_marks": "2.00"}],
                      ai_criteria=[{"criterion_name": "Logic", "rating": "2.00"},
                                   {"criterion_name": "Style", "rating": "2.00"}],
                      approved_criteria=[])
        values.update(overrides)
        return srb.build_student_result(**values)

    def test_stored_feedback_never_forwarded_for_graded_results(self):
        result = self.build()
        self.assertNotIn("Suggested", json.dumps(result))
        self.assertIsNone(result["compilation_diagnostics"])

    def test_manual_grade_has_no_verified_test_marks(self):
        result = self.build(auto_status="MANUAL_GRADING", tests_passed=0,
                            tests_run=0, ai_criteria=[])
        self.assertEqual((result["outcome"], result["test_marks"],
                          result["rubric_marks"]), ("MANUAL_GRADE", None, None))

    def test_full_validation_assessment(self):
        result = self.build(validation_percentage="100.00", final_score="8.00",
                            ai_criteria=[], rubric_rows=[])
        self.assertEqual((result["test_marks"], result["rubric_marks_available"]),
                         ("8.00", "0.00"))

    def test_compile_diagnostics_strip_label_and_teacher_section(self):
        text = ("[DEVELOPMENT TEST - label]\nstudent_solution.cpp:1:1: error: x\n"
                + srb.TEACHER_ONLY_MARKER + "\nsecret")
        self.assertEqual(srb.compilation_diagnostics(text),
                         "student_solution.cpp:1:1: error: x")

    def test_label_prefix_matches_fixture_grader(self):
        from dev_tools.fixture_submission_grader import evidence_label
        self.assertTrue(evidence_label("k", "0" * 64)
                        .startswith(srb.DEV_EVIDENCE_LABEL_PREFIX))


if __name__ == "__main__":
    unittest.main()
