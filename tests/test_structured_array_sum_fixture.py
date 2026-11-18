"""
Structured E2E fixture (count-prefixed array sum): reference source bytes and
hash, planned inputs, Python-oracle outputs, driver guards.
Nothing here compiles or executes C++, and nothing touches SQL Server.
"""

import hashlib
import json
import re
import unittest
from decimal import Decimal
from pathlib import Path

from fixtures import build_structured_array_sum_payloads as payloads
from fixtures import run_structured_array_sum_e2e as driver
from input_schema import check_schema, validate_input
from test_case_builder import select_structured_inputs
from test_case_generation_service import choose_execution_mode, load_trusted_reference_hashes

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "fixtures" / "reference_solutions" / "array_sum_reference.cpp"
DATA = ROOT / "fixtures" / "structured_array_sum"


class ReferenceSourceTests(unittest.TestCase):

    def setUp(self):
        self.raw = REFERENCE.read_bytes()

    def test_exact_bytes_hash_to_the_reviewed_value(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), driver.EXPECTED_HASH)
        self.assertEqual(hashlib.sha256(self.raw.decode("utf-8").encode("utf-8")).hexdigest(),
                         driver.EXPECTED_HASH)       # what the API hashes (source_code text)

    def test_plain_ascii_lf_no_bom(self):
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", self.raw)
        self.assertTrue(all(b < 128 for b in self.raw))
        self.assertTrue(self.raw.endswith(b"\n"))

    def test_only_iostream_and_no_dangerous_calls(self):
        text = self.raw.decode("ascii")
        self.assertEqual(re.findall(r"#include\s*(\S+)", text), ["<iostream>"])
        for token in ("system(", "popen", "fopen", "fstream", "thread", "asm", "exec",
                      "socket", "getenv", "#pragma", "#define"):
            self.assertNotIn(token, text)

    def test_documented_grammar_matches_schema_bounds(self):
        text = self.raw.decode("ascii")
        self.assertIn("1 <= N <= 100", text)
        self.assertIn("-1000000000 <= Ai <= 1000000000", text)
        self.assertIn("n < 1 || n > 100", text)
        self.assertIn("a < -1000000000LL || a > 1000000000LL", text)
        self.assertEqual(payloads.INPUT_SCHEMA["variables"]["N"], {"min": 1, "max": 100})
        array = payloads.INPUT_SCHEMA["items"][1]
        self.assertEqual((array["min"], array["max"]), (-10 ** 9, 10 ** 9))


class AllowlistS2Tests(unittest.TestCase):
    """S2 (owner-approved 2026-11-18): exactly these bytes may run on the host."""

    FACTORIAL_HASH = "53b7d425901f831aae4dae9bfaeab839de9ec51415f319ce4943f43b07893993"

    def setUp(self):
        self.raw = REFERENCE.read_bytes()

    def test_allowlist_file_holds_exactly_factorial_and_array_sum(self):
        data = json.loads((ROOT / "trusted_reference_hashes.json").read_text(encoding="utf-8"))
        self.assertEqual([e["sha256"] for e in data["references"]],
                         [self.FACTORIAL_HASH, driver.EXPECTED_HASH])
        self.assertEqual(load_trusted_reference_hashes(),
                         frozenset({self.FACTORIAL_HASH, driver.EXPECTED_HASH}))

    def test_exact_approved_bytes_are_accepted_for_host_generation(self):
        digest = hashlib.sha256(self.raw).hexdigest()
        self.assertEqual(digest, driver.EXPECTED_HASH)
        self.assertEqual(choose_execution_mode(digest, False), "trusted_host")
        self.assertEqual(choose_execution_mode(digest, True), "sandbox")   # sandbox still preferred

    def test_modified_bytes_are_rejected(self):
        variants = {
            "crlf": self.raw.replace(b"\n", b"\r\n"),
            "bom": b"\xef\xbb\xbf" + self.raw,
            "trailing_space": self.raw + b" ",
            "no_final_newline": self.raw[:-1],
            "bound_changed": self.raw.replace(b"n > 100", b"n > 101"),
            "extra_include": b"#include <cstdlib>\n" + self.raw,
        }
        for name, data in variants.items():
            self.assertNotEqual(data, self.raw, name)
            with self.subTest(name), self.assertRaisesRegex(ValueError, "not been reviewed"):
                choose_execution_mode(hashlib.sha256(data).hexdigest(), False)

    def test_orchestrator_refuses_modified_source_without_compiling(self):
        from unittest.mock import patch
        import test_case_generation_orchestrator as orchestrator

        modified = self.raw.decode("ascii").replace("sum += a;", "sum += a + 1;")
        with patch.object(orchestrator, "compile_cpp",
                          side_effect=AssertionError("must not compile")) as compile_cpp:
            with self.assertRaises(orchestrator.HostExecutionNotApproved):
                orchestrator._structured_on_host(modified, check_schema(payloads.INPUT_SCHEMA),
                                                 payloads.ADDITIONAL_INPUTS)
            compile_cpp.assert_not_called()

    def test_student_and_other_cpp_remain_blocked(self):
        trusted = load_trusted_reference_hashes()
        others = sorted((ROOT / "dev_tools" / "trusted_fixtures").glob("*.cpp")) + \
            sorted((ROOT / "dev_tools" / "synthetic_submissions").glob("*.cpp")) + \
            [ROOT / "fixtures" / "reference_solutions" / "prime_check_reference.cpp"]
        self.assertGreaterEqual(len(others), 10)
        for path in others:
            self.assertNotIn(hashlib.sha256(path.read_bytes()).hexdigest(), trusted, path.name)
        student = b'#include <iostream>\nint main(){long long n,s=0,a;std::cin>>n;' \
                  b'while(n--){std::cin>>a;s+=a;}std::cout<<s<<"\\n";}\n'
        with self.assertRaises(ValueError):
            choose_execution_mode(hashlib.sha256(student).hexdigest(), False)

    def test_production_student_execution_gate_stays_disabled(self):
        import secure_execution_gate
        self.assertIsNone(secure_execution_gate.get_secure_sandbox())

    def test_allowlist_is_consulted_only_by_reference_generation(self):
        users = sorted(p.name for p in ROOT.glob("*.py")
                       if "trusted_reference_hashes" in p.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(users, ["test_case_generation_orchestrator.py",
                                 "test_case_generation_service.py"])


class PlannedCasesTests(unittest.TestCase):

    def setUp(self):
        self.schema = check_schema(payloads.INPUT_SCHEMA)
        self.planned = json.loads((DATA / "planned_test_cases.json").read_text(encoding="utf-8"))

    def test_payload_files_match_the_builder_and_reference(self):
        self.assertEqual(self.planned["reference_sha256"], driver.EXPECTED_HASH)
        self.assertEqual(json.loads((DATA / "04_reference_solution.json").read_text(
            encoding="utf-8"))["body"]["source_code"].encode("utf-8"), REFERENCE.read_bytes())
        self.assertEqual(json.loads((DATA / "02_input_schema.json").read_text(
            encoding="utf-8"))["body"], {"input_schema": payloads.INPUT_SCHEMA})
        self.assertEqual(json.loads((DATA / "06_generate_test_cases.json").read_text(
            encoding="utf-8"))["body"], {"additional_inputs": payloads.ADDITIONAL_INPUTS})

    def test_exactly_ten_unique_valid_inputs_reproduced_deterministically(self):
        selected = select_structured_inputs(self.schema, payloads.ADDITIONAL_INPUTS)
        cases = self.planned["cases"]
        self.assertEqual(len(cases), 10)
        self.assertEqual([c["input_data"] for c in cases], [s["input_data"] for s in selected])
        self.assertEqual(len({c["input_data"] for c in cases}), 10)
        for case in cases:
            self.assertTrue(validate_input(self.schema, case["input_data"])["valid"])
            self.assertEqual(case["input_data"].count("\n"), 2)        # multi-line, exact text
        self.assertEqual(cases[0]["input_data"], "3\n10 20 30\n")

    def test_oracle_outputs(self):
        expected = {"3\n10 20 30\n": "60\n", "1\n-1000000000\n": "-1000000000\n",
                    "5\n1000000000 1000000000 1000000000 1000000000 1000000000\n": "5000000000\n",
                    "4\n-5 5 -7 7\n": "0\n", "6\n-1 -2 -3 -4 -5 -6\n": "-21\n"}
        for text, output in expected.items():
            self.assertEqual(payloads.oracle(text), output)
        # Same whitespace tolerance as the schema validator and std::cin.
        self.assertTrue(validate_input(self.schema, "2\n1  2\n")["valid"])
        self.assertEqual(payloads.oracle("2\n1  2\n"), "3\n")
        for case in self.planned["cases"]:
            values = [int(v) for v in case["input_data"].split("\n")[1].split()]
            self.assertEqual(case["expected_output_python_oracle"], f"{sum(values)}\n")

    def test_oracle_rejects_inputs_outside_the_grammar(self):
        for bad in ("0\n\n", "101\n" + " ".join(["1"] * 101) + "\n", "2\n1\n",
                    "1\n1000000001\n", "1\n-1000000001\n", "3\n1 2\n"):
            with self.assertRaises(ValueError):
                payloads.oracle(bad)
            self.assertFalse(validate_input(self.schema, bad)["valid"])

    def test_compare_with_oracle(self):
        cases = self.planned["cases"]
        saved = [(c["test_number"], c["input_data"],
                  c["expected_output_python_oracle"].replace("\n", "\r\n")) for c in cases]
        self.assertEqual(driver.compare_with_oracle(saved, cases), [])
        saved[3] = (4, saved[3][1], "1\n")
        saved[5] = (6, saved[5][1].rstrip("\n"), saved[5][2])
        self.assertEqual(driver.compare_with_oracle(saved, cases), [
            "test 4: reference output differs from the Python oracle",
            "test 6: input differs from the planned exact text"])
        self.assertEqual(driver.compare_with_oracle(saved[:9], cases)[0],
                         "saved 9 test cases, planned 10")


class FakeApi:
    def __init__(self, status=None, me=None):
        self.status = status or {"active_database": "FYP_E2E_Test",
                                 "student_execution_enabled": False}
        self.me = me or {"role": "teacher", "user_id": "BIIT235"}
        self.calls = []

    def call(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path))
        return 200, (self.status if path == "/api/grading/status" else self.me)


class DriverGuardTests(unittest.TestCase):

    def test_write_steps_require_explicit_confirm(self):
        for step in ("draft", "assessment", "reference", "generate"):
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, None)
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, "FYP_Database")
            driver.require_confirm(step, "FYP_E2E_Test")
        driver.require_confirm("check", None)
        driver.require_confirm("verify", None)

    def test_environment_accepted_only_for_e2e_backend_and_teacher(self):
        driver.require_environment(FakeApi(), lambda: "FYP_E2E_Test")
        refusals = [
            (FakeApi(), lambda: "FYP_Database"),
            (FakeApi(status={"active_database": "FYP_Database"}), lambda: "FYP_E2E_Test"),
            (FakeApi(status={"active_database": "FYP_E2E_Test",
                             "student_execution_enabled": True}), lambda: "FYP_E2E_Test"),
            (FakeApi(me={"role": "teacher", "user_id": "OTHER"}), lambda: "FYP_E2E_Test"),
            (FakeApi(me={"role": "student", "user_id": "BIIT235"}), lambda: "FYP_E2E_Test"),
        ]
        for api, name in refusals:
            with self.assertRaises(driver.Stop):
                driver.require_environment(api, name)

    def test_protected_assessments_refused_without_sql(self):
        for aid in (2, 4, 5):
            with self.assertRaises(driver.Stop):
                driver.owned_assessment({"assessment_id": aid})
        with self.assertRaises(driver.Stop):
            driver.owned_assessment({})

    def test_final_draft_check_compares_normalized_schema(self):
        stored = {k: v for k, v in check_schema(payloads.INPUT_SCHEMA).items() if k != "legacy"}
        self.assertEqual(stored["items"][1]["distinct"], False)      # backend-added defaults
        draft = {"finalized": True, "approval": {"value": True, "code": True},
                 "input_schema": stored,
                 "constraints": [{"category": "value", "text": "1 <= N <= 100",
                                  "review_status": "REJECTED"},
                                 {"category": "value", "text": payloads.VALUE_RULE,
                                  "review_status": "ACCEPTED"}]}
        self.assertTrue(driver.final_draft_ok(draft, payloads.INPUT_SCHEMA, payloads.VALUE_RULE))
        wrong = json.loads(json.dumps(draft))
        wrong["input_schema"]["items"][1]["max"] = 10
        self.assertFalse(driver.final_draft_ok(wrong, payloads.INPUT_SCHEMA, payloads.VALUE_RULE))
        for change in ({"finalized": False}, {"input_schema": None},
                       {"approval": {"value": True, "code": False}}):
            self.assertFalse(driver.final_draft_ok(dict(draft, **change),
                                                   payloads.INPUT_SCHEMA, payloads.VALUE_RULE))

    def test_reviewed_source_matches_payload(self):
        self.assertEqual(driver.reviewed_source().encode("utf-8"), REFERENCE.read_bytes())

    def test_demo_setup_steps_are_write_gated(self):
        for step in ("rubric", "assign"):
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, None)
            driver.require_confirm(step, "FYP_E2E_Test")
        for step in ("teacher-view", "student-view"):
            driver.require_confirm(step, None)


class DemoSetupPayloadTests(unittest.TestCase):

    def test_rubric_and_section_payloads_match_the_approved_plan(self):
        rubric = json.loads((DATA / "07_rubric.json").read_text(encoding="utf-8"))
        self.assertEqual((rubric["method"], rubric["body"]),
                         ("PUT", {"criteria": payloads.RUBRIC_CRITERIA}))
        self.assertEqual([(c["criterion_name"], c["max_marks"]) for c in payloads.RUBRIC_CRITERIA],
                         [("Logic & Correctness", "3.00"), ("Code Quality", "2.00")])
        section = json.loads((DATA / "08_section_assignment.json").read_text(encoding="utf-8"))
        self.assertEqual((section["method"], section["body"]),
                         ("POST", {"section_ids": ["SEC000001"]}))

    def test_rubric_plus_tests_equal_total_marks(self):
        from decimal import Decimal
        from grading_allocation import split_allocation
        tests, rubric = split_allocation(payloads.ASSESSMENT["total_marks"],
                                         payloads.ASSESSMENT["validation_percentage"])
        self.assertEqual(rubric, sum(Decimal(c["max_marks"]) for c in payloads.RUBRIC_CRITERIA))
        self.assertEqual(tests, Decimal("5.00"))


class ScriptedApi:
    """Answers (method, path) from a script; records every call."""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def call(self, method, path, payload=None, ok=(200, 201), **kwargs):
        self.calls.append((method, path, payload))
        status, data = self.responses[(method, path)](payload)
        if status not in ok:
            raise driver.Stop(f"{method} {path} -> HTTP {status}")
        return status, data


class DemoSetupStepTests(unittest.TestCase):

    def setUp(self):
        from unittest import mock
        self.state = {"assessment_id": 6}
        self.tests = [(28 + i, i + 1, "0.50") for i in range(10)]
        patches = [
            mock.patch.object(driver, "owned_assessment", lambda state: 6),
            mock.patch.object(driver, "test_case_rows", lambda aid: self.tests),
            mock.patch.object(driver, "save_state", lambda state: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.criteria = []
        self.sections = []

    def rubric(self, _=None):
        total = sum(Decimal(c["max_marks"]) for c in self.criteria) if self.criteria else 0
        return 200, {"rubric": {
            "storage_available": True, "locked": False, "total_marks": "10.00",
            "rubric_marks_allocated": "5.00", "test_marks_allocated": "5.00",
            "rubric_complete": bool(self.criteria) and total == Decimal("5.00"),
            "criteria": [dict(c, rubric_criterion_id=3 + i) for i, c in enumerate(self.criteria)]}}

    def put_rubric(self, payload):
        self.criteria = [dict(c) for c in payload["criteria"]]
        return self.rubric()

    def api(self):
        return ScriptedApi({
            ("GET", "/api/assessments/6/rubric"): self.rubric,
            ("PUT", "/api/assessments/6/rubric"): self.put_rubric,
            ("GET", "/api/assessments/6/sections"): lambda _: (200, {"sections": self.sections}),
            ("GET", "/api/assessments/6/assignable-sections"):
                lambda _: (200, {"sections": [{"section_id": "SEC000001", "already_assigned": 0}]}),
            ("POST", "/api/assessments/6/sections"): self.post_sections,
        })

    def post_sections(self, payload):
        self.sections = [{"section_id": s, "assessment_section_id": 3, "enrolled_students": 4,
                          "submission_count": 0} for s in payload["section_ids"]]
        return 201, {}

    def test_rubric_written_once_then_idempotent(self):
        api = self.api()
        driver.step_rubric(api, self.state)
        driver.step_rubric(api, self.state)
        self.assertEqual([c for c in api.calls if c[0] == "PUT"],
                         [("PUT", "/api/assessments/6/rubric",
                           {"criteria": payloads.RUBRIC_CRITERIA})])
        self.assertEqual(self.state["rubric_criterion_ids"], [3, 4])

    def test_rubric_refuses_to_overwrite_a_different_rubric(self):
        self.criteria = [{"criterion_name": "Other", "max_marks": "5.00", "description": None}]
        api = self.api()
        with self.assertRaises(driver.Stop):
            driver.step_rubric(api, self.state)
        self.assertFalse([c for c in api.calls if c[0] != "GET"])

    def test_rubric_refuses_when_tests_are_not_ten(self):
        self.tests = self.tests[:9]
        api = self.api()
        with self.assertRaises(driver.Stop):
            driver.step_rubric(api, self.state)
        self.assertEqual(api.calls, [])

    def test_assign_requires_rubric_first(self):
        api = self.api()
        with self.assertRaises(driver.Stop):
            driver.step_assign(api, self.state)
        self.assertFalse([c for c in api.calls if c[0] != "GET"])

    def test_assign_once_then_idempotent(self):
        self.criteria = [dict(c) for c in payloads.RUBRIC_CRITERIA]
        api = self.api()
        driver.step_assign(api, self.state)
        driver.step_assign(api, self.state)
        self.assertEqual([c for c in api.calls if c[0] == "POST"],
                         [("POST", "/api/assessments/6/sections", {"section_ids": ["SEC000001"]})])
        self.assertEqual(self.state["assessment_section_id"], 3)

    def test_assign_refuses_when_other_sections_exist(self):
        self.criteria = [dict(c) for c in payloads.RUBRIC_CRITERIA]
        self.sections = [{"section_id": "SEC000099", "assessment_section_id": 9}]
        api = self.api()
        with self.assertRaises(driver.Stop):
            driver.step_assign(api, self.state)
        self.assertFalse([c for c in api.calls if c[0] == "POST"])

    def test_student_view_requires_known_test_student(self):
        with self.assertRaises(driver.Stop):
            driver.step_student_view(self.api(), self.state, student="NOT-AN-E2E-STUDENT")

    def test_hidden_leak_scan(self):
        tests = [(1, "3\n10 20 30\n", "60\n"), (2, "1\n5\n", "5000000000\n")]
        self.assertEqual(driver.hidden_leaks({"assessments": [{"title": "Array Sum"}]}, tests), [])
        self.assertEqual(driver.hidden_leaks({"x": "3\n10 20 30", "y": "5000000000"}, tests),
                         ["test 1 input", "test 2 expected_output"])
        self.assertEqual(driver.hidden_leaks({"expected_output": ""}, []), ["field expected_output"])

    def test_w3_steps_are_write_gated(self):
        for step in ("w3-submit", "w3-manual-grade"):
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, None)
            driver.require_confirm(step, "FYP_E2E_Test")
        driver.require_confirm("w3-verify", None)

    def test_w3_payloads_are_reviewed_and_honest(self):
        sub = driver.w3_source()
        self.assertEqual(sub["file_name"], "w3_synthetic_submission.cpp")
        self.assertIn("SYNTHETIC E2E SUBMISSION", sub["source_code"])
        self.assertNotIn("\r", sub["source_code"])
        grade = json.loads((DATA / "10_w3_manual_grade.json").read_text(encoding="utf-8"))
        self.assertEqual(grade["path"], "/api/submissions/<submission_id>/evaluation/manual-grade")
        body = grade["body"]
        self.assertEqual(set(body), {"final_score", "confirm_manual_grade", "justification",
                                     "instructor_feedback"})       # route's allowed fields
        self.assertIs(body["confirm_manual_grade"], True)
        rubric_total = sum(Decimal(c["max_marks"]) for c in payloads.RUBRIC_CRITERIA)
        self.assertEqual(Decimal(body["final_score"]), rubric_total)   # test portion 0, not run
        self.assertEqual(Decimal(body["final_score"]), driver.W3_FINAL_SCORE)
        for text in (body["justification"], body["instructor_feedback"]):
            self.assertIn("NOT AUTO-EXECUTED", text)
            self.assertNotRegex(text.lower(), r"\btests? passed\b|\d+\s*/\s*10 tests")
        import evaluation_review_service as review
        self.assertEqual(review.validate_final_score(body["final_score"], Decimal("10.00")),
                         Decimal("5.00"))

    def test_w3_submit_refuses_unexpected_existing_submissions(self):
        from unittest import mock
        api = ScriptedApi({
            ("GET", "/api/assessments/6/rubric"): lambda _: (200, {"rubric": {
                "rubric_complete": True,
                "criteria": [{"rubric_criterion_id": 3}, {"rubric_criterion_id": 4}]}}),
            ("GET", "/api/assessments/6/sections"):
                lambda _: (200, {"sections": [{"section_id": "SEC000001"}]}),
        })
        state = {"assessment_id": 6, "rubric_criterion_ids": [3, 4]}
        rows = [(9, "E2E-TEST-0002", 9, "PENDING", 0, None, None)]
        with mock.patch.object(driver, "owned_assessment", lambda s: 6), \
                mock.patch.object(driver, "require_ten_tests", lambda aid: Decimal("5.00")), \
                mock.patch.object(driver, "a6_submissions", lambda aid: rows):
            with self.assertRaises(driver.Stop):
                driver.step_w3_submit(api, state, student_api_factory=self.fail)
            state["rubric_criterion_ids"] = [1, 2]
            with self.assertRaises(driver.Stop):
                driver.step_w3_submit(api, state, student_api_factory=self.fail)

    def test_w3_manual_grade_refuses_non_pending_or_graded(self):
        from unittest import mock
        api = ScriptedApi({("GET", "/api/grading/status"):
                           lambda _: (200, {"student_execution_enabled": False})})
        state = {"assessment_id": 6, "w3_submission_id": 9}
        for row in ((9, "E2E-TEST-0001", 9, "PROCESSING", 1, "w", None),
                    (9, "E2E-TEST-0001", 9, "COMPLETED", 1, "w", "COMPLETED")):
            with mock.patch.object(driver, "owned_assessment", lambda s: 6), \
                    mock.patch.object(driver, "a6_submissions", lambda aid, r=row: [r]):
                with self.assertRaises(driver.Stop):
                    driver.step_w3_manual_grade(api, state)
            self.assertFalse([c for c in api.calls if c[0] == "POST"])
        with mock.patch.object(driver, "owned_assessment", lambda s: 6), \
                self.assertRaises(driver.Stop):
            driver.step_w3_manual_grade(api, {"assessment_id": 6})

    def test_gate_l_steps_are_write_gated(self):
        for step in ("l-submit", "l-finalize"):
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, None)
            driver.require_confirm(step, "FYP_E2E_Test")
        for step in ("l-report", "l-verify"):
            driver.require_confirm(step, None)

    def test_gate_l_payloads_are_reviewed_and_honest(self):
        src = driver.l_source()
        self.assertIn("SYNTHETIC E2E SUBMISSION", src["source_code"])
        self.assertIn("int sum = 0;", src["source_code"])
        grade = json.loads((DATA / "11_gate_l_finalize.json").read_text(encoding="utf-8"))
        self.assertTrue(grade["path"].endswith("/evaluation/finalize"))
        body = grade["body"]
        self.assertEqual(set(body), {"final_score", "criteria", "instructor_feedback"})
        self.assertEqual([c["criterion_name"] for c in body["criteria"]],
                         [c["criterion_name"] for c in payloads.RUBRIC_CRITERIA])
        total = sum(Decimal(c["marks"]) for c in body["criteria"])
        self.assertEqual(total, Decimal(body["final_score"]))       # no test marks added
        for c, r in zip(body["criteria"], payloads.RUBRIC_CRITERIA):
            self.assertLessEqual(Decimal(c["marks"]), Decimal(r["max_marks"]))
        self.assertIn("NOT EXECUTED", body["instructor_feedback"])
        self.assertNotRegex(body["instructor_feedback"].lower(), r"\btests? passed\b")

    def test_gate_l_submit_refuses_unexpected_submissions(self):
        from unittest import mock
        state = {"assessment_id": 6, "rubric_criterion_ids": [3, 4], "w3_submission_id": 6}
        rows = [(6, "E2E-TEST-0001", 5, "COMPLETED", 0, "m", "MANUALLY_GRADED"),
                (8, "E2E-TEST-0003", 7, "PENDING", 0, None, None)]
        with mock.patch.object(driver, "a6_submissions", lambda aid: rows):
            with self.assertRaises(driver.Stop):
                driver.l_rows(6, state)
        with mock.patch.object(driver, "a6_submissions", lambda aid: rows[:1]):
            self.assertEqual(driver.l_rows(6, state), [])

    def test_auto_demo_steps_are_write_gated(self):
        for step in ("auto-submit", "auto-finalize"):
            with self.assertRaises(driver.Stop):
                driver.require_confirm(step, None)
            driver.require_confirm(step, "FYP_E2E_Test")
        for step in ("auto-preflight", "auto-wait", "auto-verify"):
            driver.require_confirm(step, None)

    def test_auto_demo_payloads_are_reviewed_and_honest(self):
        src = driver.auto_source()
        self.assertIn("SYNTHETIC E2E SUBMISSION", src["source_code"])
        self.assertIn("long long total", src["source_code"])
        self.assertNotIn("\r", src["source_code"])
        body = json.loads((DATA / "13_auto_finalize.json").read_text(encoding="utf-8"))["body"]
        self.assertEqual(set(body), {"final_score", "criteria", "instructor_feedback"})
        self.assertEqual(sum(Decimal(c["marks"]) for c in body["criteria"]),
                         Decimal(body["final_score"]))
        self.assertEqual(Decimal(body["final_score"]), driver.AUTO_FINAL_SCORE)
        self.assertIn("NOT EXECUTED", body["instructor_feedback"])

    def test_auto_submit_refuses_when_other_jobs_are_open(self):
        from unittest import mock
        state = {"assessment_id": 6, "w3_submission_id": 6, "l_submission_id": 7}
        rows = [(6, "E2E-TEST-0001", 5, "COMPLETED", 0, "m", "MANUALLY_GRADED"),
                (7, "E2E-TEST-0002", 6, "COMPLETED", 1, "l", "AI_ANALYSIS_UNVERIFIED")]
        with mock.patch.object(driver, "owned_assessment", lambda s: 6), \
                mock.patch.object(driver, "a6_submissions", lambda aid: rows), \
                mock.patch.object(driver, "open_jobs", lambda: [(9, 99, "PENDING")]):
            with self.assertRaises(driver.Stop):
                driver.step_auto_submit(None, state, student_api_factory=self.fail)

    def test_auto_wait_follows_transitions_and_times_out(self):
        from unittest import mock
        states = iter([("PENDING", 0, None), ("PROCESSING", 1, "llm-analysis-loop-H"),
                       ("COMPLETED", 1, "llm-analysis-loop-H")])
        with mock.patch.object(driver, "job_row", lambda j: next(states)), \
                mock.patch.object(driver, "step_l_report", lambda *a, **k: None):
            driver.step_auto_wait(None, {"auto_job_id": 8}, sleep=lambda s: None)
        with mock.patch.object(driver, "job_row", lambda j: ("COMPLETED", 1, "llm-analysis-e2e-H")), \
                mock.patch.object(driver, "step_l_report", lambda *a, **k: None):
            with self.assertRaises(driver.Stop):        # finished by the wrong (manual) runner
                driver.step_auto_wait(None, {"auto_job_id": 8}, sleep=lambda s: None)
        ticks = iter(range(0, 10000, 100))
        with mock.patch.object(driver, "job_row", lambda j: ("PENDING", 0, None)):
            with self.assertRaises(driver.Stop):
                driver.step_auto_wait(None, {"auto_job_id": 8}, timeout=300,
                                      clock=lambda: next(ticks), sleep=lambda s: None)

    def test_values_published_in_the_statement_are_not_leaks(self):
        tests = [(2, "1\n-1000000000\n", "-1000000000\n"), (3, "2\n7 9\n", "1234567\n")]
        fmt = payloads.ASSESSMENT["input_format"]
        listing = {"assessments": [{"input_format": fmt}]}
        self.assertEqual(driver.hidden_leaks(listing, tests), ["test 2 expected_output"])
        self.assertEqual(driver.hidden_leaks(listing, tests, fmt), [])
        listing["assessments"].append({"note": "1234567"})
        self.assertEqual(driver.hidden_leaks(listing, tests, fmt), ["test 3 expected_output"])


if __name__ == "__main__":
    unittest.main()
