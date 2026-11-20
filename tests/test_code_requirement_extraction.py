"""
Code-requirement polarity, value-bound deduplication and the analysis
response for the "Find Maximum and Minimum Using Loops" problem.
No model is loaded: value generation is replaced by a fixed result.
"""

import unittest
from unittest.mock import patch

import ai_constraint_service as svc
import code_constraint_checker as checker
import teacher_approval_service as drafts
from code_requirement_extractor import SUPPORTED, extract_code_requirements
from numeric_constraint_validator import extract_explicit_bounds


def types(text):
    return [c["type"] for c in extract_code_requirements(text)["code_constraints"]]


def manual(text):
    return [m["rule"] for m in extract_code_requirements(text)["manual_review"]]


MAXMIN_PROBLEM = (
    "Find Maximum and Minimum Using Loops\n"
    "Write a C++ program that reads N integers into an array and prints the maximum "
    "and the minimum value.\n"
    "Requirements:\n"
    "* You must use a for loop.\n"
    "* You must use if/else conditional statements.\n"
    "* You must NOT use sort() or STL sorting algorithms.\n"
    "* You must NOT use recursion.\n"
    "* Use cin and cout.\n"
    "Constraints:\n"
    "1 <= N <= 100\n"
    "-1000 <= A[i] <= 1000")
MAXMIN_INPUT = ("The first line contains N (1 <= N <= 100). The second line contains N "
                "integers A[i] (-1000 <= A[i] <= 1000).")
MAXMIN_OUTPUT = "Print the maximum and the minimum separated by a space."


class RecursionPolarityTests(unittest.TestCase):

    def test_must_use_recursion(self):
        self.assertEqual(types("You must use recursion."), ["recursion_required"])
        self.assertEqual(types("Calculate factorial of N using recursion."),
                         ["recursion_required"])
        self.assertEqual(types("Write a recursive function to compute N!."),
                         ["recursion_required"])

    def test_must_not_use_recursion(self):
        for text in ("You must not use recursion.", "You must NOT use recursion.",
                     "Do not use recursion.", "Don't use recursion.", "You cannot use recursion.",
                     "Recursion must not be used.", "You should not solve it recursively."):
            with self.subTest(text=text):
                self.assertEqual(types(text), ["recursion_prohibited"])

    def test_no_recursion_allowed(self):
        for text in ("No recursion allowed.", "No recursion is allowed.",
                     "Recursion is not allowed.", "Recursion is prohibited.",
                     "Solve it without recursion.", "Avoid recursion.",
                     "Use a loop instead of recursion."):
            with self.subTest(text=text):
                self.assertIn("recursion_prohibited", types(text))
                self.assertNotIn("recursion_required", types(text))

    def test_optional_is_not_a_constraint(self):
        self.assertEqual(types("You may use recursion."), [])
        self.assertEqual(types("Recursion is optional."), [])

    def test_negation_never_becomes_a_requirement(self):
        for text in ("You must NOT use recursion.", "No recursion is allowed.",
                     "Avoid recursion.", "Do not use recursion or loops."):
            with self.subTest(text=text):
                self.assertNotIn("recursion_required", types(text))


class OtherTechniqueTests(unittest.TestCase):

    def test_for_loop_required(self):
        self.assertEqual(types("You must use a for loop."), ["for_loop_required"])
        self.assertEqual(types("Use for-loops only."), ["for_loop_required"])

    def test_generic_loop_rules(self):
        self.assertEqual(types("Solve it using a loop."), ["loop_required"])
        self.assertEqual(types("Sort the array without using loops."), ["loops_prohibited"])

    def test_sorting_prohibited(self):
        self.assertEqual(types("You must NOT use sort() or STL sorting algorithms."),
                         ["function_prohibited:sort", "function_prohibited:stable_sort",
                          "function_prohibited:partial_sort"])
        self.assertEqual(types("Do not use the sort function."), ["function_prohibited:sort"])

    def test_conditionals_required(self):
        self.assertEqual(types("You must use if/else conditional statements."),
                         ["conditional_required"])
        self.assertEqual(types("Use if-else statements to compare values."),
                         ["conditional_required"])

    def test_mixed_required_and_forbidden(self):
        self.assertEqual(types("You must use a for loop and must not use recursion."),
                         ["for_loop_required", "recursion_prohibited"])
        self.assertEqual(sorted(types("Use a loop, not recursion.")),
                         ["loop_required", "recursion_prohibited"])

    def test_unrelated_mentions_are_ignored(self):
        for text in ("The Fibonacci sequence is defined recursively.",
                     "Recursion is a powerful idea.", "Print the sorted array.",
                     "If N is even print YES else print NO.",
                     "You must not exceed 100 iterations.",
                     "Write a program to count loops in a graph.",
                     "You must print the answer on one line."):
            with self.subTest(text=text):
                self.assertEqual(types(text), [])
                self.assertEqual(manual(text), [])

    def test_unsupported_rules_go_to_manual_review(self):
        self.assertEqual(manual("Use cin and cout."), ["UNKNOWN"])
        self.assertEqual(types("You must use a while loop."), [])
        self.assertEqual(manual("You must use a while loop."), ["REQUIRED"])
        self.assertEqual(types("You must use sort()."), [])           # no "sort required" key
        self.assertEqual(manual("You must use sort()."), ["REQUIRED"])

    def test_conflicting_instructions_withdrawn(self):
        text = "You must use recursion. Recursion is not allowed."
        self.assertEqual(types(text), [])
        self.assertEqual(manual(text), ["CONFLICT"])

    def test_existing_rules_still_work(self):
        self.assertEqual(types("Use dynamic programming."), ["dynamic_programming_required"])
        self.assertEqual(types("Without using Gaussian elimination, solve the system."),
                         ["gaussian_elimination_prohibited"])

    def test_items_carry_rule_and_honest_enforcement(self):
        items = extract_code_requirements(
            "You must NOT use recursion. Use dynamic programming.")["code_constraints"]
        by_type = {c["type"]: c for c in items}
        self.assertEqual(by_type["recursion_prohibited"]["rule"], "PROHIBITED")
        self.assertEqual(by_type["recursion_prohibited"]["enforcement"], "STATIC_CHECK")
        self.assertEqual(by_type["dynamic_programming_required"]["enforcement"],
                         "TEACHER_REVIEW_ONLY")
        for item in items:
            self.assertEqual(item["status"], "NEEDS_TEACHER_REVIEW")

    def test_every_static_key_is_really_checked(self):
        code = "#include <iostream>\nint main(){int a=1; if(a){} for(;;){break;} return 0;}"
        for key, enforcement in SUPPORTED.items():
            status = checker.check_code_constraints(code, [key])["checks"][0]["evidence"]
            with self.subTest(key=key):
                if enforcement == "STATIC_CHECK":
                    self.assertNotIn("no reliable static check", status)
                else:
                    self.assertIn("no reliable static check", status)


class CheckerNewKeyTests(unittest.TestCase):

    def check(self, code, key):
        return checker.check_code_constraints(code, [key])["checks"][0]["status"]

    def test_for_loop_required(self):
        self.assertEqual(self.check("int main(){for(int i=0;i<3;i++){}}", "for_loop_required"),
                         "SATISFIED")
        self.assertEqual(self.check("int main(){int i=0; while(i<3) i++;}", "for_loop_required"),
                         "VIOLATED")
        self.assertEqual(self.check('int main(){ /* for */ const char*s="for";}', "for_loop_required"),
                         "VIOLATED")

    def test_conditional_required(self):
        self.assertEqual(self.check("int main(){int a=1; if(a>0){a=2;} else {a=3;}}",
                                    "conditional_required"), "SATISFIED")
        self.assertEqual(self.check("int main(){int a=1; int b = a>0 ? 1 : 2;}",
                                    "conditional_required"), "SATISFIED")
        self.assertEqual(self.check('int main(){int a=1; const char*s="if ?";}',
                                    "conditional_required"), "VIOLATED")

    def test_sort_prohibited(self):
        self.assertEqual(self.check("int main(){std::sort(a, a+n);}", "function_prohibited:sort"),
                         "VIOLATED")
        self.assertEqual(self.check("int main(){int mx=a[0];}", "function_prohibited:sort"),
                         "SATISFIED")


class ValueBoundTests(unittest.TestCase):

    def texts(self, text):
        return [b["text"] for b in extract_explicit_bounds(text)]

    def test_duplicate_bounds_removed(self):
        self.assertEqual(self.texts("1 <= N <= 100\nInput: N (1 <= N <= 100)"), ["1 <= N <= 100"])
        self.assertEqual(self.texts("N is between 1 and 100. 1 <= N <= 100."), ["1 <= N <= 100"])
        self.assertEqual(self.texts("1 <= N  <=  100 and 1<=N<=100"), ["1 <= N <= 100"])

    def test_distinct_bounds_kept(self):
        self.assertEqual(self.texts("1 <= N <= 100 and -1000 <= A[i] <= 1000"),
                         ["1 <= N <= 100", "-1000 <= A[i] <= 1000"])
        self.assertEqual(self.texts("1 <= N <= 100 and 1 <= N <= 50"),
                         ["1 <= N <= 100", "1 <= N <= 50"])
        self.assertEqual(self.texts("1 <= n <= 100, 1 <= N <= 100"),
                         ["1 <= n <= 100", "1 <= N <= 100"])

    def test_indexed_power_and_unicode_forms(self):
        self.assertEqual(self.texts("0 <= grid[i][j] <= 9"), ["0 <= grid[i][j] <= 9"])
        self.assertEqual(self.texts("-10^9 <= a_i <= 10^9"), ["-10^9 <= a_i <= 10^9"])
        self.assertEqual(self.texts("1 ≤ N ≤ 10^5"), ["1 <= N <= 10^5"])

    def test_power_is_not_truncated(self):
        # Previously captured as "1 <= N <= 10".
        self.assertEqual(self.texts("Given N (1 <= N <= 10^9), print N."), ["1 <= N <= 10^9"])
        self.assertEqual(self.texts("0 <= x <= 1.5"), [])        # not silently "<= 1"
        self.assertEqual(self.texts("Print N where 0 <= N <= 12."), ["0 <= N <= 12"])


class MaxMinAnalysisTests(unittest.TestCase):
    """The reported problem, end to end through analyze_cpp_problem."""

    def analyse(self):
        fake = {"text": "1 <= N <= 100", "status": "NEEDS_SEMANTIC_REVIEW",
                "unsupported_numbers": [], "warnings": [], "input_tokens": 150,
                "generated_tokens": 12, "output_complete": True}
        with patch.object(svc, "generate_value_constraints", return_value=fake):
            return svc.analyze_cpp_problem(MAXMIN_PROBLEM, MAXMIN_INPUT, MAXMIN_OUTPUT)

    def test_values_unique_and_array_bound_kept(self):
        result = self.analyse()
        self.assertEqual([v["text"] for v in result["value_constraints"]],
                         ["1 <= N <= 100", "-1000 <= A[i] <= 1000"])

    def test_code_rules(self):
        result = self.analyse()
        self.assertEqual([c["type"] for c in result["code_constraints"]],
                         ["for_loop_required", "conditional_required",
                          "function_prohibited:sort", "function_prohibited:stable_sort",
                          "function_prohibited:partial_sort", "recursion_prohibited"])
        self.assertNotIn("recursion_required", [c["type"] for c in result["code_constraints"]])
        self.assertEqual([w["code"] for w in result["warnings"]],
                         ["CODE_REQUIREMENT_NEEDS_MANUAL_REVIEW"])
        self.assertTrue(result["warnings"][0]["message"].endswith("Use cin and cout."))
        self.assertEqual(set(result), {"value_constraints", "code_constraints",
                                       "teacher_approval_required", "warnings", "model"})
        self.assertTrue(result["teacher_approval_required"])

    def test_draft_shows_rules_and_review_note(self):
        draft = drafts.create_draft(self.analyse())
        code_texts = [c["text"] for c in draft["constraints"] if c["category"] == "code"]
        self.assertIn("recursion_prohibited", code_texts)
        self.assertNotIn("recursion_required", code_texts)
        self.assertEqual(draft["ai_warnings"][0]["code"], "CODE_REQUIREMENT_NEEDS_MANUAL_REVIEW")
        self.assertIn("cin and cout", draft["ai_warnings"][0]["message"])
        self.assertFalse(any(c["review_status"] != "PENDING" for c in draft["constraints"]))


if __name__ == "__main__":
    unittest.main()
