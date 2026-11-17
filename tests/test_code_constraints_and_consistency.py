"""
Static code-constraint verification, AI consistency safeguards, evidence
grounding and student-facing redaction. No DB, no C++ execution, no LLM.
"""

import unittest
from decimal import Decimal
from uuid import uuid4

from tests import fakes  # noqa: F401

import grading_pipeline as gp
from code_constraint_checker import (INCONCLUSIVE, SATISFIED, VIOLATED,
                                     check_code_constraints,
                                     strip_comments_and_literals)
from dev_tools import grading_preview_harness as harness
from evaluation_review_service import redact_ai_section, redact_hidden_inputs
from grading_consistency_checks import analyse_ai_result
from ollama_grading_service import (CriterionContractError,
                                    parse_criterion_only_response)
from rubric_ai_grader import build_llm_evidence, grade_rubric_with_ollama
from student_grading_evidence_builder import build_student_grading_evidence

RECURSIVE = """
#include <iostream>
long long fact(int n) { return n <= 1 ? 1 : n * fact(n - 1); }
int main() { int n; std::cin >> n; std::cout << fact(n) << '\\n'; }
"""
MUTUAL = """
bool isOdd(int n);
bool isEven(int n) { if (n == 0) return true; return isOdd(n - 1); }
bool isOdd(int n) { if (n == 0) return false; return isEven(n - 1); }
int main() { return isEven(4) ? 0 : 1; }
"""
ITERATIVE = """
#include <iostream>
// recursion would be nicer: fact(n - 1)  for (;;) while
int main() {
    const char* s = "recursion for while do";
    long long r = 1; int n = 5;
    for (int i = 2; i <= n; ++i) r *= i;
    std::cout << r << s;
}
"""
NO_LOOP_TRICKS = """
#include <iostream>
/* for (int i = 0; i < n; i++) */
int main() {
    std::cout << "while (true) { for }" << 'f' << R"(do { } while(0);)";
    return 0;
}
"""
LAMBDA_REC = """
#include <functional>
int main() {
    std::function<int(int)> f = [&](int n) { return n ? n * f(n - 1) : 1; };
    return f(3);
}
"""
MACRO = """
#define LOOP(i, n) for (int i = 0; i < n; ++i)
int main() { int s = 0; return s; }
"""
HELPER_UNUSED = """
int helper(int x) { return x * 2; }
int main() { return 0; }
"""


def statuses(source, constraints):
    return [c["status"] for c in check_code_constraints(source, constraints)["checks"]]


class LexicalTests(unittest.TestCase):
    def test_comments_strings_chars_and_raw_strings_removed(self):
        code = strip_comments_and_literals(NO_LOOP_TRICKS)
        for word in ("for", "while", "do"):
            self.assertNotRegex(code, rf"\b{word}\b")


class CodeConstraintTests(unittest.TestCase):
    def test_keywords_in_comments_or_strings_do_not_count(self):
        self.assertEqual(statuses(NO_LOOP_TRICKS, ["loop_required"]), [VIOLATED])
        self.assertEqual(statuses(NO_LOOP_TRICKS, ["loops_prohibited"]), [SATISFIED])
        self.assertEqual(statuses(ITERATIVE, ["recursion_required"]), [VIOLATED])

    def test_loops(self):
        self.assertEqual(statuses(ITERATIVE, ["loop_required", "loops_prohibited"]),
                         [SATISFIED, VIOLATED])
        self.assertEqual(statuses(MACRO, ["loop_required"]), [INCONCLUSIVE])

    def test_recursion(self):
        self.assertEqual(statuses(RECURSIVE, ["recursion_required"]), [SATISFIED])
        self.assertEqual(statuses(MUTUAL, ["recursion_required"]), [SATISFIED])
        self.assertEqual(statuses(RECURSIVE, ["recursion_prohibited"]), [VIOLATED])
        self.assertEqual(statuses(ITERATIVE, ["recursion_prohibited"]), [SATISFIED])
        self.assertEqual(statuses(LAMBDA_REC, ["recursion_required"]), [INCONCLUSIVE])
        self.assertEqual(statuses(LAMBDA_REC, ["recursion_prohibited"]), [INCONCLUSIVE])
        self.assertEqual(statuses(MACRO, ["recursion_required"]), [INCONCLUSIVE])

    def test_functions(self):
        self.assertEqual(statuses(RECURSIVE, ["function_required"]), [SATISFIED])
        self.assertEqual(statuses(ITERATIVE, ["function_required"]), [VIOLATED])
        self.assertEqual(statuses(HELPER_UNUSED, ["function_required"]), [INCONCLUSIVE])
        uses_sqrt = "#include <cmath>\nint main(){ return (int)std::sqrt(9.0); }"
        mentions_sqrt = "int main(){ /* sqrt(9) */ const char*s=\"sqrt(\"; return 0; }"
        self.assertEqual(statuses(uses_sqrt, ["function_prohibited:sqrt",
                                              "header_prohibited:cmath",
                                              "header_required:cmath"]),
                         [VIOLATED, VIOLATED, SATISFIED])
        self.assertEqual(statuses(mentions_sqrt, ["function_prohibited:sqrt"]),
                         [SATISFIED])

    def test_unknown_or_free_text_is_inconclusive(self):
        self.assertEqual(statuses(ITERATIVE, ["dynamic_programming_required",
                                              "Use a clean modular design",
                                              "gaussian_elimination_prohibited"]),
                         [INCONCLUSIVE] * 3)

    def test_real_fixtures(self):
        code = {name: harness.strip_developer_header(
                    (harness.SUBMISSIONS / f).read_text(encoding="utf-8"))[0]
                for name, (f, _m, _p) in harness.SCENARIOS.items()}
        self.assertEqual(statuses(code["all_pass"], ["function_required",
                                                     "loop_required",
                                                     "recursion_prohibited"]),
                         [SATISFIED, SATISFIED, SATISFIED])
        self.assertEqual(statuses(code["most_pass"], ["function_required"]),
                         [VIOLATED])
        self.assertEqual(statuses(code["half_range"], ["stl_container_required",
                                                       "hash_map_required"]),
                         [SATISFIED, VIOLATED])
        self.assertEqual(statuses(code["none_pass"], ["recursion_required"]),
                         [VIOLATED])

    def test_report_never_changes_marks_and_requests_review(self):
        r = check_code_constraints(ITERATIVE, "loop_required\nrecursion_required")
        self.assertFalse(r["marks_changed"])
        self.assertTrue(r["requires_review"])
        self.assertEqual(r["summary"], {SATISFIED: 1, VIOLATED: 1, INCONCLUSIVE: 0})
        ok = check_code_constraints(ITERATIVE, ["loop_required"])
        self.assertFalse(ok["requires_review"])
        none = check_code_constraints(ITERATIVE, "")
        self.assertEqual(none["checks"], [])
        self.assertFalse(none["requires_review"])


RUBRIC = [{"criterion_name": "Logic & Algorithm", "max_marks": "3"},
          {"criterion_name": "Code Quality", "max_marks": "2"}]
EVIDENCE = {"tests_run": 10, "tests_passed": 8, "cases": [
    {"test_number": n, "passed": n not in (6, 9), "input_data": str(n * 11),
     "execution_status": "completed", "expected_output": "NO\n",
     "student_output": "YES\n" if n in (6, 9) else "NO\n"} for n in range(1, 11)]}


def parsed(logic=2, quality=2, logic_text="Uses d * d <= n correctly.",
           quality_text="Readable names.", cited=(), observed=()):
    return parse_criterion_only_response({
        "criteria": [{"criterion_name": "Logic & Algorithm", "mark": logic,
                      "explanation": logic_text, "cited_failed_tests": list(cited)},
                     {"criterion_name": "Code Quality", "mark": quality,
                      "explanation": quality_text}],
        "observed_failures": [{"test_number": t, "description": "x"}
                              for t in observed],
        "suspected_bugs": [], "overall_feedback": "ok", "issues": [],
        "improvement_suggestions": []}, RUBRIC)


def codes(flags):
    return sorted({f["code"] for f in flags})


class ConsistencyTests(unittest.TestCase):
    def test_defect_admitted_with_full_marks(self):
        flags = analyse_ai_result(parsed(3, 2, "The loop condition d * d < n is "
                                         "incorrect for perfect squares.", cited=[9]),
                                  RUBRIC, EVIDENCE)
        self.assertIn("DEFECT_ADMITTED_WITH_FULL_MARKS", codes(flags))

    def test_negated_defect_words_do_not_flag(self):
        flags = analyse_ai_result(parsed(3, 2, "No bugs found; the logic is "
                                         "correct.", "There are no issues and it "
                                         "does not contain unnecessary code.",
                                         cited=[9]), RUBRIC, EVIDENCE)
        self.assertNotIn("DEFECT_ADMITTED_WITH_FULL_MARKS", codes(flags))

    def test_praise_with_zero_marks(self):
        flags = analyse_ai_result(parsed(0, 0, "The primality logic is correct.",
                                         "The code is readable and clear.",
                                         cited=[9]), RUBRIC, EVIDENCE)
        self.assertEqual([f["criterion"] for f in flags
                          if f["code"] == "PRAISE_WITH_ZERO_MARKS"],
                         ["Logic & Algorithm", "Code Quality"])

    def test_unsupported_failure_claims(self):
        flags = analyse_ai_result(parsed(1, 2, "fails", cited=[3, 99], observed=[5]),
                                  RUBRIC, EVIDENCE)
        messages = " ".join(f["message"] for f in flags
                            if f["code"] == "UNSUPPORTED_FAILURE_CLAIM")
        self.assertIn("test 3 as failing, but it passed", messages)
        self.assertIn("test 99 as failing, but no such test", messages)
        self.assertIn("test 5 as an observed failure, but it passed", messages)

    def test_supported_citations_do_not_flag(self):
        flags = analyse_ai_result(parsed(1, 2, "d * d < n is wrong", cited=[6, 9],
                                         observed=[9]), RUBRIC, EVIDENCE)
        self.assertNotIn("UNSUPPORTED_FAILURE_CLAIM", codes(flags))
        self.assertNotIn("FAILURES_IGNORED", codes(flags))

    def test_failures_ignored(self):
        self.assertIn("FAILURES_IGNORED", codes(analyse_ai_result(parsed(),
                                                                  RUBRIC, EVIDENCE)))
        all_pass = {**EVIDENCE, "tests_passed": 10,
                    "cases": [{**c, "passed": True} for c in EVIDENCE["cases"]]}
        self.assertEqual(analyse_ai_result(parsed(), RUBRIC, all_pass), [])

    def test_bogus_citation_does_not_hide_ignored_failures(self):
        # Live most_pass + constraints run: model cited/observed test 1
        # (which passed) and ignored the real failures 6 and 9.
        live = parsed(2, 2, "does not handle N = 1", cited=[1], observed=[1])
        found = codes(analyse_ai_result(live, RUBRIC, EVIDENCE))
        self.assertIn("UNSUPPORTED_FAILURE_CLAIM", found)
        self.assertIn("FAILURES_IGNORED", found)

    def test_failures_reported_but_not_explained(self):
        # The live most_pass pattern: failures listed as observed, but both
        # explanations call the code correct.
        live = parsed(2, 2, "It efficiently checks divisibility; the loop "
                      "condition `d * d < n` ensures the loop runs only up to "
                      "the square root.", "Well-structured with clear names.",
                      observed=[6, 9])
        self.assertIn("FAILURES_NOT_EXPLAINED", codes(analyse_ai_result(
            live, RUBRIC, EVIDENCE)))
        explained = parsed(1, 2, "The condition d * d < n misses the divisor of "
                           "perfect squares.", observed=[6, 9], cited=[6, 9])
        self.assertNotIn("FAILURES_NOT_EXPLAINED", codes(analyse_ai_result(
            explained, RUBRIC, EVIDENCE)))

    def test_execution_claim(self):
        flags = analyse_ai_result(parsed(1, 2, "When I ran the code it printed YES.",
                                         cited=[9]), RUBRIC, EVIDENCE)
        self.assertIn("EXECUTION_CLAIM", codes(flags))

    def test_constraint_flags(self):
        report = check_code_constraints(ITERATIVE, ["recursion_required",
                                                    "dynamic_programming_required"])
        flags = analyse_ai_result(parsed(3, 2, "It uses recursion as required.",
                                         cited=[9]), RUBRIC, EVIDENCE, report)
        self.assertEqual(sum(f["code"] == "CONSTRAINT_NOT_VERIFIED" for f in flags), 2)
        self.assertIn("CONSTRAINT_CLAIM_CONTRADICTED", codes(flags))

    def test_flags_never_change_marks(self):
        def grader(**kwargs):
            return parsed(3, 2, "d * d < n is incorrect", cited=[9])
        r = grade_rubric_with_ollama(
            {"problem": "p", "input_format": "i", "output_format": "o",
             "constraints": "c"},
            [{"criterion_name": "Logic & Algorithm", "max_marks": Decimal("3")},
             {"criterion_name": "Code Quality", "max_marks": Decimal("2")}],
            Decimal("5"), "int main(){}", EVIDENCE, grader=grader)
        self.assertEqual([c["rating"] for c in r["criteria"]], ["3.00", "2.00"])
        self.assertEqual(r["suggested_score"], "5.00")
        self.assertTrue(r["manual_review_required"])
        self.assertTrue(any("DEFECT_ADMITTED_WITH_FULL_MARKS" in f
                            for f in r["reliability_flags"]))


class ParserV11Tests(unittest.TestCase):
    def test_optional_fields_default_empty(self):
        r = parse_criterion_only_response({
            "criteria": [{"criterion_name": "Logic & Algorithm", "mark": 1,
                          "explanation": "x"},
                         {"criterion_name": "Code Quality", "mark": 1,
                          "explanation": "y"}],
            "overall_feedback": "", "issues": [], "improvement_suggestions": []},
            RUBRIC)
        self.assertEqual((r["observed_failures"], r["suspected_bugs"]), ([], []))
        self.assertEqual(r["criteria"][0]["cited_failed_tests"], [])

    def test_malformed_evidence_fields_rejected(self):
        base = parsed()
        bad_bodies = [
            {"cited": ["9"]}, {"cited": [0]}, {"cited": [True]},
            {"observed": [{"test_number": "9"}]}, {"observed": ["9"]},
            {"suspected": [1]}]
        for bad in bad_bodies:
            body = {"criteria": [
                {"criterion_name": "Logic & Algorithm", "mark": 1, "explanation": "x",
                 "cited_failed_tests": bad.get("cited", [])},
                {"criterion_name": "Code Quality", "mark": 1, "explanation": "y"}],
                "observed_failures": bad.get("observed", []),
                "suspected_bugs": bad.get("suspected", []),
                "overall_feedback": "", "issues": [], "improvement_suggestions": []}
            with self.subTest(bad=bad), self.assertRaises(CriterionContractError):
                parse_criterion_only_response(body, RUBRIC)
        self.assertEqual(base["criteria"][0]["cited_failed_tests"], [])


class EvidenceGroundingTests(unittest.TestCase):
    def test_builder_carries_inputs(self):
        ev = build_student_grading_evidence(
            [{"test_number": 1, "input_data": "994009", "expected_output": "NO\n"}],
            [{"test_number": 1, "status": "completed", "stdout": "YES\n"}])
        self.assertEqual(ev["cases"][0]["input_data"], "994009")
        self.assertFalse(ev["cases"][0]["passed"])

    def test_llm_evidence_failures_first_with_inputs_and_constraints(self):
        report = check_code_constraints(ITERATIVE, ["recursion_required"])
        ev = build_llm_evidence({**EVIDENCE, "synthetic": True,
                                 "code_constraint_checks": report})
        self.assertEqual([c["test_number"] for c in ev["cases"][:2]], [6, 9])
        self.assertEqual(ev["cases"][0]["input"], "66")
        self.assertEqual(ev["failed_test_numbers"], [6, 9])
        self.assertEqual(ev["code_constraint_checks"][0]["status"], VIOLATED)
        self.assertEqual(ev["evidence_type"], "synthetic_development_fixture")

    def test_prompt_states_evidence_rules(self):
        from ollama_grading_service import CRITERION_ONLY_SYSTEM_PROMPT as p
        for phrase in ("OBSERVED failure", "SUSPECTED bug",
                       "Never invent inputs", "VIOLATED or INCONCLUSIVE",
                       "Do NOT calculate or output any total"):
            self.assertIn(phrase, p)


class PipelineConstraintTests(unittest.TestCase):
    def run_pipeline(self, constraints, source=ITERATIVE):
        calls = {"complete": [], "ai": 0}
        tests = [{"test_number": i, "input_data": str(900000 + i),
                  "expected_output": "NO\n"} for i in range(1, 11)]

        class Sandbox:
            def compile(self, src):
                return {"status": "COMPILED", "artifact": "A"}

            def run_tests(self, artifact, cases):
                return [{"test_number": c["test_number"], "status": "completed",
                         "stdout": "YES\n" if c["test_number"] == 3 else "NO\n"}
                        for c in cases]

            def cleanup(self, artifact):
                pass

        deps = gp.GradingDependencies(
            prepare=lambda *a: {"job_id": 1, "submission_id": 1,
                                "source_code": source},
            renew_lease=lambda *a: "ok",
            load_assessment_marks=lambda s: {"assessment_id": 5},
            load_config=lambda a: {"total_marks": Decimal("10"),
                                   "validation_percentage": Decimal("100")},
            load_context=lambda a: {"problem": "p", "input_format": "i",
                                    "output_format": "o", "constraints": "c",
                                    "code_constraints": constraints},
            load_rubric=lambda a: [], load_test_cases=lambda a: tests,
            get_sandbox=Sandbox,
            grade_rubric=lambda *a: calls.__setitem__("ai", calls["ai"] + 1),
            complete=lambda *a, **k: calls["complete"].append((a, k)),
            complete_compilation_failure=lambda *a: None,
            release=lambda *a: {"job_status": "PENDING"})
        result = gp.process_claimed_job({"job_id": 1, "submission_id": 1,
                                         "claim_token": str(uuid4())}, "w", deps)
        return result, calls

    def test_violated_constraint_needs_review_without_changing_score(self):
        result, calls = self.run_pipeline(["recursion_required"])
        args, kwargs = calls["complete"][0]
        self.assertEqual(kwargs["auto_evaluation_status"], "NEEDS_REVIEW")
        self.assertEqual(args[3], Decimal("9.00"))          # 9/10 tests, unchanged
        self.assertIn("recursion_required: VIOLATED", args[4])
        self.assertEqual(calls["ai"], 0)                     # 100% validation

    def test_satisfied_constraint_no_review(self):
        _, calls = self.run_pipeline(["loop_required"])
        self.assertEqual(calls["complete"][0][1]["auto_evaluation_status"],
                         "COMPLETED")

    def test_feedback_lists_failed_numbers_but_never_hidden_inputs(self):
        _, calls = self.run_pipeline([])
        feedback = calls["complete"][0][0][4]
        self.assertIn("Failed test numbers: [3]", feedback)
        self.assertNotIn("900003", feedback)


class RedactionTests(unittest.TestCase):
    def test_whole_values_only(self):
        text = "N = 994009 fails; 2.00 marks; 9940090 differs; test 4 and 4."
        out = redact_hidden_inputs(text, ["994009", "4"])
        self.assertNotRegex(out, r"(?<!\d)994009(?!\d)")
        self.assertIn("2.00", out)
        self.assertIn("9940090", out)
        self.assertEqual(out.count("[hidden test input]"), 3)

    def test_only_ai_section_is_redacted(self):
        feedback = ("Tests passed: 4 / 10\n=== AI rubric assessment ===\n"
                    "fails for 4")
        out = redact_ai_section(feedback, ["4"])
        self.assertTrue(out.startswith("Tests passed: 4 / 10"))
        self.assertTrue(out.endswith("fails for [hidden test input]"))


if __name__ == "__main__":
    unittest.main()
