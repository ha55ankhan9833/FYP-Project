"""
Structured E2E fixture (count-prefixed array sum): reference source bytes and
hash, planned inputs, Python-oracle outputs, driver guards.
Nothing here compiles or executes C++, and nothing touches SQL Server.
"""

import hashlib
import json
import re
import unittest
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


if __name__ == "__main__":
    unittest.main()
