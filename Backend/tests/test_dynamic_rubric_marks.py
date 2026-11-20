"""
Dynamic rubric + equal test-case marks:
  marks_allocation.compute_allocation     exact split, equal 2-decimal share
  secure_test_case_save                   10 equal rows, refuses inexact / unapproved
  approve_assessment_rubric               teacher approval, atomic, retry-safe
  rubric_suggestion_service               offline LLM suggestions, honest fallback
  rubric_workflow_routes                  auth, contract, error codes
  assessment_readiness_service            MARKS_ALLOCATION_INVALID / RUBRIC_NOT_APPROVED
In-memory fakes only; nothing reaches SQL Server or Ollama.
"""

import copy
import hashlib
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import requests

from tests.fakes import FakeConnection, approved_rubric_response
from tests.test_generate_click_flow import (
    FakeOracle, SOURCE_HASH, flask_app, headers, readiness_responder)

import assessment_readiness_service as readiness
import assessment_rubric_service as rubric_service
import rubric_suggestion_service as suggestion
import secure_test_case_save as save_mod
from grading_allocation import split_allocation
from marks_allocation import MarksAllocationError, compute_allocation, nearest_valid_percentages

D = Decimal

MAX_MIN_PROBLEM = ("Write a C++ program that reads N and then N integers, and uses a for loop "
                   "with if conditions to find and print the maximum and the minimum value.")
MAX_MIN_VALUES = "1 <= N <= 100\n-1000 <= A[i] <= 1000"
MAX_MIN_CODES = "for_loop_required\nconditional_required\nfunction_prohibited:sort"


# ----------------------------------------------------------------------
# Exact allocation
# ----------------------------------------------------------------------

class AllocationTests(unittest.TestCase):

    def test_examples_from_specification(self):
        a = compute_allocation(50, 60)
        self.assertEqual((a["test_marks"], a["rubric_marks"], a["per_case_marks"]),
                         ("30.00", "20.00", "3.00"))
        b = compute_allocation(10, 50)
        self.assertEqual((b["test_marks"], b["rubric_marks"], b["per_case_marks"]),
                         ("5.00", "5.00", "0.50"))

    def test_totals_and_percentages_are_exact(self):
        for total in (10, 20, 30, 50, 100):
            for pct in (0, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 100):
                with self.subTest(total=total, pct=pct):
                    try:
                        a = compute_allocation(total, pct)
                    except MarksAllocationError:
                        # only possible when total x pct/100 / 10 needs a 3rd decimal
                        self.assertNotEqual(D(total) * pct / 1000,
                                            (D(total) * pct / 1000).quantize(D("0.01")))
                        continue
                    test, rubric, per = (D(a["test_marks"]), D(a["rubric_marks"]),
                                         D(a["per_case_marks"]))
                    self.assertEqual(test, D(total) * D(pct) / 100)
                    self.assertEqual(test + rubric, D(total))
                    self.assertEqual(per * 10, test)              # no drift
                    self.assertEqual(per, per.quantize(D("0.01")))
                    self.assertEqual(a["rubric_required"], rubric > 0)
                    # grading's rounding split agrees exactly
                    self.assertEqual(split_allocation(total, pct), (test, rubric))

    def test_fractional_equal_shares(self):
        cases = {(10, 55): "0.55", (10, 50): "0.50", (7, 50): "0.35", (20, 35): "0.70",
                 (30, 30): "0.90", (100, 33): "3.30", ("12.50", 40): "0.50", (15, 2): "0.03"}
        for (total, pct), per in cases.items():
            with self.subTest(total=total, pct=pct):
                self.assertEqual(compute_allocation(total, pct)["per_case_marks"], per)

    def test_not_representable_test_marks(self):
        with self.assertRaises(MarksAllocationError) as ctx:
            compute_allocation(10, "33.33")
        e = ctx.exception
        self.assertEqual(e.code, "TEST_MARKS_NOT_REPRESENTABLE")
        self.assertEqual(e.details["exact_test_marks"], "3.333")
        for pct in e.details["nearest_valid_percentages"]:
            compute_allocation(10, pct)                         # suggestions really work
        self.assertEqual(e.details["nearest_valid_percentages"], ["33.00", "34.00"])

    def test_unequal_share_rejected_not_rounded(self):
        for total, pct in ((1, 55), (10, "0.5"), (3, 1), (7, 3), (100, "33.33")):
            with self.subTest(total=total, pct=pct):
                with self.assertRaises(MarksAllocationError) as ctx:
                    compute_allocation(total, pct)
                self.assertIn(ctx.exception.code,
                              ("CASE_MARKS_NOT_EQUAL_SHARE", "TEST_MARKS_NOT_REPRESENTABLE"))
                for p in ctx.exception.details["nearest_valid_percentages"]:
                    compute_allocation(total, p)

    def test_invalid_inputs(self):
        bad = [(0, 50), (-10, 50), ("10.001", 50), (None, 50), (True, 50), ("abc", 50),
               ("NaN", 50), ("Infinity", 50), (10, -1), (10, "100.01"), (10, "50.005"),
               (10, None), (10, False), ("100000000", 50)]
        for total, pct in bad:
            with self.subTest(total=total, pct=pct):
                with self.assertRaises(MarksAllocationError) as ctx:
                    compute_allocation(total, pct)
                self.assertTrue(ctx.exception.code.startswith("INVALID_"))

    def test_extremes(self):
        self.assertEqual(compute_allocation(10, 0)["test_marks"], "0.00")
        self.assertEqual(compute_allocation(10, 0)["rubric_marks"], "10.00")
        self.assertFalse(compute_allocation(10, 100)["rubric_required"])
        self.assertEqual(compute_allocation(10, 100)["per_case_marks"], "1.00")
        self.assertEqual(compute_allocation(0.1, 100)["per_case_marks"], "0.01")

    def test_nearest_valid_percentages_bounds(self):
        self.assertEqual(nearest_valid_percentages(D(10), D("0.1")), ["0.00", "1.00"])
        for p in nearest_valid_percentages(D(1), D("99.9")):
            self.assertLessEqual(D(p), 100)


# ----------------------------------------------------------------------
# Secure save: equal marks per case
# ----------------------------------------------------------------------

DRAFT_JSON = json.dumps({"finalized": True, "approval": {"value": True, "code": True},
                         "constraints": [{"category": "value", "review_status": "ACCEPTED",
                                          "text": "1 <= N <= 1000000"}]})


def save_responder(total, pct, rubric_ok=True):
    source = "SRC"
    digest = hashlib.sha256(source.encode()).hexdigest()

    def responder(sql, params):
        if "AssessmentRubricCriterion" in sql:
            if not rubric_ok and sql.startswith("SELECT rubric_criterion_id"):
                return [], -1
            return approved_rubric_response(sql, total, pct)
        if sql.startswith("SELECT total_marks, value_constraints"):
            return [(str(total), "1 <= N <= 1000000", "", str(pct))], -1
        if "FROM dbo.ConstraintReviewDraft" in sql:
            return [(DRAFT_JSON, True)], -1
        if sql.startswith("SELECT value_constraints, code_constraints"):
            return [("1 <= N <= 1000000", "")], -1
        if "FROM dbo.ReferenceSolution" in sql:
            return [(source, digest, "cpp", "APPROVED")], -1
        if "FROM dbo.TestCase" in sql:
            return [(0,)], -1
        if sql.startswith("INSERT INTO dbo.TestCase"):
            return [], 1
        raise AssertionError(sql)
    return responder, digest


def ten_cases():
    return [{"test_number": i, "test_name": f"t{i}", "category": "c", "input_data": str(i),
             "expected_output": "1\n"} for i in range(1, 11)]


class EqualCaseMarksSaveTests(unittest.TestCase):

    def save(self, total, pct, rubric_ok=True):
        responder, digest = save_responder(total, pct, rubric_ok)
        conn = FakeConnection(responder)
        with patch.object(save_mod, "get_connection", return_value=conn):
            try:
                result = save_mod.save_generated_test_cases_secure(
                    57, "T9", 9, digest, ten_cases())
            except Exception as exc:
                result = exc
        return conn, result

    def test_equal_marks_for_many_allocations(self):
        for total, pct, per in ((10, 50, "0.50"), (20, 60, "1.20"), (30, 70, "2.10"),
                                (50, 60, "3.00"), (100, 40, "4.00"), (10, 55, "0.55"),
                                (10, 100, "1.00"), ("12.50", 40, "0.50")):
            with self.subTest(total=total, pct=pct):
                conn, r = self.save(total, pct)
                self.assertIsInstance(r, dict, r)
                marks = [p[7] for s, p in conn.executed if s.startswith("INSERT INTO dbo.TestCase")]
                self.assertEqual(len(marks), 10)
                self.assertEqual(set(marks), {D(per)})
                self.assertEqual(sum(marks), D(r["test_marks_allocated"]))
                self.assertEqual(r["marks_per_test"], per)
                self.assertEqual(D(r["test_marks_allocated"]) + D(r["rubric_marks_allocated"]),
                                 D(str(total)))
                self.assertEqual(r["weighting"], "EQUAL")
                self.assertEqual(conn.commits, 1)

    def test_inexact_share_refused_without_writes(self):
        for total, pct in ((10, "33.33"), (1, 55), (7, 3)):
            with self.subTest(total=total, pct=pct):
                conn, r = self.save(total, pct)
                self.assertIsInstance(r, MarksAllocationError)
                self.assertFalse(conn.statements("INSERT"))
                self.assertEqual(conn.commits, 0)

    def test_unapproved_rubric_refused_without_writes(self):
        conn, r = self.save(10, 50, rubric_ok=False)
        self.assertIsInstance(r, rubric_service.RubricNotApprovedError)
        self.assertIn("5.00 rubric marks", str(r))
        self.assertFalse(conn.statements("INSERT"))

    def test_full_test_allocation_needs_no_rubric(self):
        conn, r = self.save(10, 100, rubric_ok=False)
        self.assertEqual(r["saved_count"], 10)


# ----------------------------------------------------------------------
# Rubric approval (service)
# ----------------------------------------------------------------------

class RubricStore:
    def __init__(self, total="50.00", pct="50.00", teacher="T1", criteria=None,
                 submissions=0, test_cases=0, table=True):
        self.assessment = {"total": D(total), "pct": D(pct), "teacher": teacher}
        self.criteria = list(criteria or [])
        self.submissions, self.test_cases, self.table = submissions, test_cases, table
        self.next_id = 100


class RubricConnection(FakeConnection):
    def __init__(self, store):
        super().__init__(self.respond)
        self.store = store
        self.pending = None

    def _work(self):
        if self.pending is None:
            self.pending = {"pct": self.store.assessment["pct"],
                            "criteria": copy.deepcopy(self.store.criteria)}
        return self.pending

    def commit(self):
        super().commit()
        if self.pending is not None:
            self.store.assessment["pct"] = self.pending["pct"]
            self.store.criteria = self.pending["criteria"]
        self.pending = None

    def rollback(self):
        super().rollback()
        self.pending = None

    def respond(self, sql, p):
        s, w = self.store, self._work()
        if "OBJECT_ID" in sql:
            return [(1 if s.table else 0,)], -1
        if sql.startswith("SELECT total_marks, validation_percentage FROM dbo.Assessment"):
            a = s.assessment
            return ([(a["total"], w["pct"])] if p == (7, a["teacher"]) else []), -1
        if "FROM dbo.Submission" in sql:
            return [(s.submissions,)], -1
        if "FROM dbo.TestCase" in sql:
            return [(s.test_cases,)], -1
        if sql.startswith("SELECT rubric_criterion_id"):
            return [(c["id"], c["name"], c["marks"], c["desc"]) for c in w["criteria"]], -1
        if sql.startswith("UPDATE dbo.Assessment SET validation_percentage"):
            w["pct"] = p[0]
            return [], 1
        if sql.startswith("DELETE FROM dbo.AssessmentRubricCriterion"):
            n = len(w["criteria"])
            w["criteria"] = []
            return [], n
        if sql.startswith("INSERT INTO dbo.AssessmentRubricCriterion"):
            s.next_id += 1
            w["criteria"].append({"id": s.next_id, "name": p[1], "marks": p[2], "desc": p[3]})
            return [], 1
        raise AssertionError(sql)


def maxmin_criteria(*marks):
    names = ["Correct for-loop use", "Correct conditional statements",
             "Maximum/minimum logic", "Code readability"]
    return [{"criterion_name": n, "max_marks": m, "description": f"Marks for {n.lower()}"}
            for n, m in zip(names, marks)]


class RubricApprovalServiceTests(unittest.TestCase):

    def approve(self, store, pct, criteria, teacher="T1"):
        conn = RubricConnection(store)
        try:
            result = rubric_service.approve_assessment_rubric(
                7, teacher, pct, criteria, connection_factory=lambda: conn)
        except Exception as exc:
            result = exc
        self.assertTrue(conn.closed)
        return conn, result

    def test_approves_allocation_and_criteria_atomically(self):
        store = RubricStore(total="50.00", pct="50.00")
        conn, r = self.approve(store, "60", maxmin_criteria("6", "5", "6", "3"))
        self.assertTrue(r["approved"])
        self.assertFalse(r["unchanged"])
        self.assertEqual(r["allocation"]["rubric_marks"], "20.00")
        self.assertEqual(r["allocation"]["per_case_marks"], "3.00")
        self.assertEqual(store.assessment["pct"], D("60.00"))
        self.assertEqual(sum(c["marks"] for c in store.criteria), D("20"))
        self.assertEqual(conn.commits, 1)

    def test_identical_retry_writes_nothing_and_never_duplicates(self):
        store = RubricStore(total="10.00", pct="50.00")
        body = maxmin_criteria("1.50", "1.50", "1.00", "1.00")
        self.approve(store, "50", body)
        ids = [c["id"] for c in store.criteria]
        conn, r = self.approve(store, "50.00", body)
        self.assertTrue(r["unchanged"])
        self.assertEqual(conn.commits, 0)
        self.assertFalse(conn.statements("INSERT") or conn.statements("DELETE"))
        self.assertEqual([c["id"] for c in store.criteria], ids)
        self.assertEqual(len(store.criteria), 4)

    def test_teacher_edits_replace_never_append(self):
        store = RubricStore(total="10.00", pct="50.00")
        self.approve(store, 50, maxmin_criteria("1.50", "1.50", "1.00", "1.00"))
        edited = maxmin_criteria("2", "2", "1")                       # removed one, re-marked
        edited[0]["criterion_name"] = "Uses a for loop over all N values"
        edited.append({"criterion_name": "Handles N = 1", "max_marks": "0", "description": None})
        _, bad = self.approve(store, 50, edited)
        self.assertEqual(bad.code, "INVALID_CRITERION")                # zero marks
        edited[-1]["max_marks"] = "0.00"
        edited.pop()
        _, r = self.approve(store, 50, edited)
        self.assertEqual([c["criterion_name"] for c in r["criteria"]],
                         ["Uses a for loop over all N values", "Correct conditional statements",
                          "Maximum/minimum logic"])
        self.assertEqual(len(store.criteria), 3)

    def test_rubric_sum_must_match_allocation(self):
        store = RubricStore(total="50.00", pct="50.00")
        _, e = self.approve(store, 60, maxmin_criteria("6", "5", "6", "4"))
        self.assertEqual((e.code, e.status), ("RUBRIC_TOTAL_MISMATCH", 422))
        self.assertEqual(e.details["rubric_marks"], "20.00")
        self.assertEqual(e.details["criteria_total"], "21")
        self.assertEqual(store.criteria, [])
        self.assertEqual(store.assessment["pct"], D("50.00"))         # teacher's data untouched

    def test_negative_and_invalid_marks_rejected(self):
        store = RubricStore(total="10.00", pct="50.00")
        for marks in ("-1", "abc", "1.005", True, None):
            with self.subTest(marks=marks):
                crit = maxmin_criteria("5")
                crit[0]["max_marks"] = marks
                _, e = self.approve(store, 50, crit)
                self.assertIsInstance(e, rubric_service.RubricApprovalError)
                self.assertEqual(e.code, "INVALID_CRITERION")
        dup = maxmin_criteria("2.5", "2.5")
        dup[1]["criterion_name"] = dup[0]["criterion_name"].upper()
        _, e = self.approve(store, 50, dup)
        self.assertEqual(e.code, "INVALID_CRITERION")
        _, e = self.approve(store, 50, [{"criterion_name": "x", "max_marks": "5", "source": "AI"}])
        self.assertEqual(e.code, "INVALID_CRITERION")

    def test_invalid_allocation_rejected(self):
        store = RubricStore(total="10.00", pct="50.00")
        _, e = self.approve(store, "33.33", maxmin_criteria("6.67"))
        self.assertEqual(e.code, "MARKS_ALLOCATION_INVALID")
        self.assertEqual(e.details["reason"], "TEST_MARKS_NOT_REPRESENTABLE")
        self.assertEqual(store.criteria, [])

    def test_full_test_allocation_requires_empty_rubric(self):
        store = RubricStore(total="10.00", pct="50.00")
        _, r = self.approve(store, 100, [])
        self.assertTrue(r["approved"])
        self.assertEqual(r["allocation"]["rubric_marks"], "0.00")
        _, e = self.approve(store, 100, maxmin_criteria("1"))
        self.assertEqual(e.code, "RUBRIC_TOTAL_MISMATCH")
        _, e = self.approve(store, 50, [])
        self.assertEqual(e.code, "RUBRIC_TOTAL_MISMATCH")

    def test_locked_after_submissions(self):
        store = RubricStore(submissions=1)
        _, e = self.approve(store, 50, maxmin_criteria("25"))
        self.assertIsInstance(e, rubric_service.RubricLockedError)

    def test_percentage_locked_after_test_cases_saved(self):
        store = RubricStore(total="10.00", pct="50.00", test_cases=10)
        _, e = self.approve(store, 60, maxmin_criteria("4"))
        self.assertEqual((e.code, e.status), ("TEST_CASES_ALREADY_SAVED", 409))
        _, r = self.approve(store, 50, maxmin_criteria("3", "2"))     # same pct: criteria ok
        self.assertTrue(r["approved"])
        # the lock is reported even when the old criteria no longer fit the new %
        _, e = self.approve(store, 70, maxmin_criteria("3", "2"))
        self.assertEqual((e.code, e.status), ("TEST_CASES_ALREADY_SAVED", 409))
        self.assertEqual(store.assessment["pct"], D("50.00"))

    def test_other_teacher_cannot_approve(self):
        _, e = self.approve(RubricStore(teacher="T1"), 50, maxmin_criteria("25"), teacher="T2")
        self.assertIsInstance(e, rubric_service.RubricNotFoundError)

    def test_storage_unavailable(self):
        _, e = self.approve(RubricStore(table=False), 50, maxmin_criteria("25"))
        self.assertIsInstance(e, rubric_service.RubricStorageUnavailable)


# ----------------------------------------------------------------------
# Offline LLM suggestions
# ----------------------------------------------------------------------

def model(*criteria, warnings=()):
    return lambda messages: {"criteria": [
        {"criterion_name": n, "description": d, "marks": m} for n, d, m in criteria],
        "warnings": list(warnings)}


MAXMIN_MODEL = model(
    ("Correct for-loop use", "Uses a for loop to visit all N values", 1.5),
    ("Correct conditional statements", "Uses if conditions to update max and min", 1.5),
    ("Maximum/minimum logic", "Initialises max/min from the first value", 1.5),
    ("Code readability", "Clear names and indentation", 0.5))


class SuggestionTests(unittest.TestCase):

    def suggest(self, call_fn, rubric_marks="5.00", problem=MAX_MIN_PROBLEM,
                values=MAX_MIN_VALUES, codes=MAX_MIN_CODES):
        return suggestion.suggest_rubric(problem, values, codes, D(rubric_marks), call_fn=call_fn)

    def test_relevant_suggestions_summing_exactly(self):
        r = self.suggest(MAXMIN_MODEL)
        self.assertEqual(r["status"], "GENERATED")
        self.assertFalse(r["approved"])
        self.assertEqual(sum(D(c["suggested_marks"]) for c in r["criteria"]), D("5.00"))
        self.assertEqual([w["code"] for w in r["warnings"]], [])
        self.assertEqual(r["criteria"][0]["criterion_name"], "Correct for-loop use")

    def test_prompt_contains_problem_constraints_and_allocation(self):
        seen = {}

        def call(messages):
            seen["user"] = json.loads(messages[1]["content"])
            seen["system"] = messages[0]["content"]
            return MAXMIN_MODEL(messages)
        self.suggest(call, rubric_marks="20.00")
        self.assertEqual(seen["user"]["rubric_marks"], "20.00")
        self.assertEqual(seen["user"]["code_constraints"],
                         ["Must use a for loop", "Must use conditional (if/else) statements",
                          "Must not call sort() (ONE combined criterion)"])
        self.assertEqual(seen["user"]["value_constraints"], MAX_MIN_VALUES)
        self.assertIn("maximum", seen["user"]["problem_statement"])
        self.assertIn("never mention", seen["system"])

    def test_model_total_mismatch_is_rescaled_and_flagged(self):
        r = self.suggest(model(("Loop use", "a for loop", 3), ("Conditionals", "if checks", 3),
                               ("Readability", "names", 4)), rubric_marks="20.00")
        self.assertEqual([c["suggested_marks"] for c in r["criteria"]], ["6.00", "6.00", "8.00"])
        self.assertEqual([c["model_marks"] for c in r["criteria"]], ["3", "3", "4"])
        self.assertIn("MARKS_RESCALED", [w["code"] for w in r["warnings"]])
        odd = self.suggest(model(("A loop", "", 1), ("B condition", "", 1), ("C read", "", 1)),
                           rubric_marks="10.00")
        self.assertEqual(sum(D(c["suggested_marks"]) for c in odd["criteria"]), D("10.00"))

    def test_irrelevant_loop_criterion_flagged_for_palindrome(self):
        r = self.suggest(model(("Correct for-loop use", "Iterates with a loop", 2),
                               ("String reversal logic", "Compares characters", 2),
                               ("Readability", "Clear names", 1)),
                         problem="Read a word and print YES if it is a palindrome, else NO.",
                         values="1 <= length <= 100", codes="")
        flagged = [w for w in r["warnings"] if w["code"] == "CRITERION_MAY_BE_IRRELEVANT"]
        self.assertEqual([w["criterion_name"] for w in flagged], ["Correct for-loop use"])
        self.assertEqual(len(r["criteria"]), 3)                        # flagged, never dropped

    def test_uncovered_code_constraint_flagged(self):
        r = self.suggest(model(("Maximum logic", "max and min", 4), ("Readability", "names", 1)))
        uncovered = [w["code_constraint"] for w in r["warnings"]
                     if w["code"] == "CODE_CONSTRAINT_NOT_COVERED"]
        self.assertEqual(uncovered, ["for_loop_required", "conditional_required"])

    def test_bad_items_discarded_duplicates_removed(self):
        r = self.suggest(lambda m: {"criteria": [
            {"criterion_name": "Loop use", "marks": 2}, {"criterion_name": "loop USE", "marks": 1},
            {"criterion_name": "", "marks": 1}, {"criterion_name": "Neg", "marks": -1},
            "junk", {"criterion_name": "Conditions", "marks": "x"},
            {"criterion_name": "Conditionals", "marks": 3}]})
        self.assertEqual([c["criterion_name"] for c in r["criteria"]], ["Loop use", "Conditionals"])
        codes = [w["code"] for w in r["warnings"]]
        self.assertEqual(codes.count("CRITERION_DISCARDED"), 4)
        self.assertEqual(codes.count("DUPLICATE_CRITERION_DISCARDED"), 1)

    def test_llm_unavailable_returns_no_fabricated_rubric(self):
        failures = [requests.ConnectionError("refused"), requests.Timeout("slow")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(suggestion.requests, "post", side_effect=failure):
                    r = suggestion.suggest_rubric(MAX_MIN_PROBLEM, MAX_MIN_VALUES, MAX_MIN_CODES,
                                                  D("5.00"))
                self.assertEqual(r["status"], "UNAVAILABLE")
                self.assertEqual(r["criteria"], [])
                self.assertTrue(r["teacher_authored_allowed"])
                self.assertEqual(r["warnings"][0]["code"], "LLM_UNAVAILABLE")

    def test_invalid_model_output_is_unavailable(self):
        class Resp:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": self.content}}
        for content, code in (("not json", "LLM_INVALID_RESPONSE"),
                              ('{"foo": 1}', "LLM_INVALID_RESPONSE"),
                              ('{"criteria": []}', "LLM_NO_USABLE_CRITERIA"),
                              ('{"criteria": [{"criterion_name": "x", "marks": 0}]}',
                               "LLM_NO_USABLE_CRITERIA")):
            with self.subTest(content=content):
                with patch.object(suggestion.requests, "post", return_value=Resp(content)):
                    r = suggestion.suggest_rubric(MAX_MIN_PROBLEM, "", "", D("5"))
                self.assertEqual((r["status"], r["criteria"]), ("UNAVAILABLE", []))
                self.assertEqual(r["warnings"][0]["code"], code)

    def test_no_rubric_marks_does_not_call_model(self):
        def boom(_):
            raise AssertionError("model called")
        r = self.suggest(boom, rubric_marks="0.00")
        self.assertEqual((r["status"], r["criteria"]), ("NOT_REQUIRED", []))

    def test_marks_always_two_places_and_identifiers_humanized(self):
        r = self.suggest(model(("for_loop_required", "loop", 2.5), ("recursion_prohibited", "", 1),
                               ("Max/min tracking", "if updates", 1.5)))
        self.assertEqual([c["suggested_marks"] for c in r["criteria"]], ["2.50", "1.00", "1.50"])
        self.assertEqual([c["criterion_name"] for c in r["criteria"]][:2],
                         ["Must use a for loop", "Must not use recursion"])
        self.assertEqual(suggestion.humanize_code_constraint("function_prohibited:stable_sort"),
                         "Must not call stable_sort()")
        self.assertEqual(suggestion.humanize_code_constraint("Use a while loop"), "Use a while loop")

    def test_rescale_helper(self):
        self.assertEqual(suggestion.rescale_to_allocation([D(1)] * 3, D("1.00")),
                         [D("0.34"), D("0.33"), D("0.33")])
        self.assertIsNone(suggestion.rescale_to_allocation([D(1)] * 3, D("0.02")))
        out = suggestion.rescale_to_allocation([D("0.001"), D(99)], D("1.00"))
        self.assertEqual(sum(out), D("1.00"))
        self.assertTrue(all(m >= D("0.01") for m in out))


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

def context_responder(owned=True, approved=True, total=10, pct=50):
    from tests.test_generate_click_flow import accepted, ai_draft, finalized_draft
    draft = finalized_draft() if approved else ai_draft()

    def responder(sql, params):
        if sql.startswith("SELECT title, problem_statement"):
            return ([("Max/Min", MAX_MIN_PROBLEM, total, pct)] if owned else []), -1
        if "FROM dbo.ConstraintReviewDraft" in sql:
            return [(json.dumps(draft), 1)], -1
        if "SELECT value_constraints, code_constraints FROM dbo.Assessment" in sql:
            return [("\n".join(accepted(draft, "value")), "\n".join(accepted(draft, "code")))], -1
        raise AssertionError(sql)
    return responder


class RouteTests(unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()

    def test_auth_on_every_endpoint(self):
        for method, url in (("get", "/api/marks-allocation?total_marks=10&validation_percentage=50"),
                            ("post", "/api/assessments/7/rubric/suggest"),
                            ("post", "/api/assessments/7/rubric/approve")):
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 401)
                r = getattr(self.client, method)(url, headers=headers(role="student"), json={})
                self.assertEqual((r.status_code, r.get_json()["code"]), (403, "TEACHER_ONLY"))

    def test_allocation_preview(self):
        r = self.client.get("/api/marks-allocation?total_marks=50&validation_percentage=60",
                            headers=headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["allocation"]["per_case_marks"], "3.00")
        r = self.client.get("/api/marks-allocation?total_marks=10&validation_percentage=33.33",
                            headers=headers())
        body = r.get_json()
        self.assertEqual((r.status_code, body["code"]), (422, "MARKS_ALLOCATION_INVALID"))
        self.assertEqual(body["details"]["nearest_valid_percentages"], ["33.00", "34.00"])
        r = self.client.get("/api/marks-allocation?total_marks=10", headers=headers())
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/marks-allocation?total_marks=10&validation_percentage=5&x=1",
                            headers=headers())
        self.assertEqual(r.status_code, 400)

    def suggest(self, body=None, responder=None, call=MAXMIN_MODEL, identity="T1"):
        conn = FakeConnection(responder or context_responder())
        with patch("db.get_connection", return_value=conn), \
                patch.object(suggestion, "call_ollama", side_effect=call):
            r = self.client.post("/api/assessments/7/rubric/suggest", headers=headers(identity=identity),
                                 json=body if body is not None else {})
        self.assertEqual(conn.commits, 0)                              # read-only
        for sql, _ in conn.executed:
            self.assertFalse(sql.upper().startswith(("INSERT", "UPDATE", "DELETE")))
        return r, conn

    def test_suggest_uses_teacher_choice_and_approved_constraints(self):
        r, conn = self.suggest({"validation_percentage": 60})
        s = r.get_json()["suggestion"]
        self.assertEqual(r.status_code, 200)
        self.assertEqual(s["status"], "GENERATED")
        self.assertEqual(s["allocation"]["rubric_marks"], "4.00")
        self.assertEqual(sum(D(c["suggested_marks"]) for c in s["criteria"]), D("4.00"))
        self.assertIn("for_loop_required", s["based_on"]["code_constraints"])
        self.assertFalse(s["approved"])
        self.assertEqual(conn.executed[0][1], (7, "T1"))               # teacher from JWT

    def test_suggest_defaults_to_stored_percentage(self):
        r, _ = self.suggest()
        self.assertEqual(r.get_json()["suggestion"]["allocation"]["rubric_marks"], "5.00")

    def test_suggest_llm_unavailable_still_200(self):
        def down(_):
            raise suggestion.RubricSuggestionUnavailable("LLM_UNAVAILABLE", "down")
        r, _ = self.suggest(call=down)
        s = r.get_json()["suggestion"]
        self.assertEqual((r.status_code, s["status"], s["criteria"]), (200, "UNAVAILABLE", []))
        self.assertTrue(s["teacher_authored_allowed"])

    def test_suggest_errors(self):
        r, _ = self.suggest(responder=context_responder(owned=False))
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "ASSESSMENT_NOT_FOUND"))
        r, _ = self.suggest(responder=context_responder(approved=False))
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "CONSTRAINTS_NOT_APPROVED"))
        r, _ = self.suggest({"validation_percentage": "33.33"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (422, "MARKS_ALLOCATION_INVALID"))
        r, _ = self.suggest({"criteria": []})
        self.assertEqual(r.status_code, 400)

    def test_approve_route_contract(self):
        store = RubricStore(total="50.00", pct="50.00")
        url = "/api/assessments/7/rubric/approve"

        def post(body, identity="T1"):
            conn = RubricConnection(store)
            with patch("assessment_rubric_service.get_connection", return_value=conn), \
                    patch.object(rubric_service.approve_assessment_rubric, "__defaults__",
                                 (lambda: conn,)):
                return self.client.post(url, headers=headers(identity=identity), json=body)

        body = {"validation_percentage": 60, "criteria": maxmin_criteria("6", "5", "6", "3")}
        r = post(body)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["rubric"]["allocation"]["test_marks"], "30.00")
        self.assertEqual(r.get_json()["message"], "Rubric approved")
        r = post(body)
        self.assertEqual(r.get_json()["message"], "Rubric already approved; nothing changed")
        self.assertEqual(len(store.criteria), 4)
        r = post({"criteria": []})
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "INVALID_REQUEST"))
        r = post({**body, "teacher_id": "T1"})
        self.assertEqual(r.status_code, 400)
        r = post({"validation_percentage": 60, "criteria": maxmin_criteria("6", "5", "6", "4")})
        self.assertEqual((r.status_code, r.get_json()["code"]), (422, "RUBRIC_TOTAL_MISMATCH"))
        r = post(body, identity="T2")
        self.assertEqual((r.status_code, r.get_json()["code"]), (404, "ASSESSMENT_NOT_FOUND"))
        store.submissions = 1
        r = post({"validation_percentage": 60, "criteria": maxmin_criteria("20")})
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "RUBRIC_LOCKED"))

    def test_generation_routes_map_new_errors(self):
        from test_case_routes import _marks_or_rubric_error
        with flask_app.app_context():
            resp, status = _marks_or_rubric_error(
                MarksAllocationError("CASE_MARKS_NOT_EQUAL_SHARE", "m", {"x": 1}))
            self.assertEqual((status, resp.get_json()["code"]), (422, "MARKS_ALLOCATION_INVALID"))
            resp, status = _marks_or_rubric_error(rubric_service.RubricNotApprovedError("r"))
            self.assertEqual((status, resp.get_json()["code"]), (409, "RUBRIC_NOT_APPROVED"))
            self.assertIsNone(_marks_or_rubric_error(ValueError("other")))


class AssessmentMarksValidationRouteTests(unittest.TestCase):
    """POST/PUT /api/assessments refuse allocations without an exact equal share."""

    def test_create_rejects_inexact_allocation_before_db(self):
        client = flask_app.test_client()
        body = {"course_code": "CSC-101", "assessment_type": "assignment", "title": "t",
                "problem_statement": "p", "input_format": "i", "output_format": "o",
                "total_marks": 10, "deadline_at": "2099-01-01T10:00:00",
                "validation_percentage": "33.33", "draft_id": "d-1"}
        with patch("app.get_connection", side_effect=AssertionError("db touched")):
            r = client.post("/api/assessments", headers=headers(), json=body)
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "MARKS_ALLOCATION_INVALID"))
        self.assertEqual(r.get_json()["details"]["reason"], "TEST_MARKS_NOT_REPRESENTABLE")

    def update(self, body, tests=0):
        def responder(sql, params):
            if sql.startswith("SELECT total_marks, late_penalty_marks"):
                return [(D("10.00"), D("0"), 0, D("50.00"))], -1
            if "FROM dbo.TestCase" in sql:
                return [(tests,)], -1
            if "FROM dbo.Submission" in sql:
                return [(0,)], -1
            if "AssessmentRubricCriterion" in sql:
                return approved_rubric_response(sql, "10.00", "50.00")
            if sql.startswith("UPDATE Assessment"):
                return [], 1
            raise AssertionError(sql)
        conn = FakeConnection(responder)
        with patch("app.get_connection", return_value=conn):
            r = flask_app.test_client().put("/api/assessments/7", headers=headers(), json=body)
        return r, conn

    def test_update_rejects_inexact_allocation(self):
        r, conn = self.update({"validation_percentage": "33.33"})
        self.assertEqual((r.status_code, r.get_json()["code"]), (400, "MARKS_ALLOCATION_INVALID"))
        self.assertFalse(conn.statements("UPDATE"))

    def test_update_allocation_locked_once_cases_saved(self):
        r, conn = self.update({"validation_percentage": 60}, tests=10)
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "TEST_CASES_ALREADY_SAVED"))
        self.assertFalse(conn.statements("UPDATE"))
        r, _ = self.update({"validation_percentage": 50}, tests=10)    # unchanged value is fine
        self.assertEqual(r.status_code, 200)


# ----------------------------------------------------------------------
# Readiness
# ----------------------------------------------------------------------

class ReadinessMarksTests(unittest.TestCase):

    def check(self, total=10, pct=50, rubric_ok=True):
        conn = FakeConnection(readiness_responder(marks=(D(str(total)), D(str(pct)))))

        def rubric(*_):
            if not rubric_ok:
                raise ValueError("Assessment is not open for submissions yet")
            return True
        with patch("assessment_rubric_service.check_rubric_ready", side_effect=rubric):
            return readiness.get_generation_readiness(
                7, "T1", connection_factory=lambda: conn, sandbox_available=False,
                trusted_hashes=frozenset({SOURCE_HASH}), oracles={"f": FakeOracle()})

    def codes(self, r):
        return [m["code"] for m in r["missing_for_generation"]]

    def test_ready_shows_allocation(self):
        r = self.check(50, 60)
        self.assertTrue(r["ready_to_generate"], r)
        self.assertEqual(r["checks"]["marks_allocation"]["per_case_marks"], "3.00")

    def test_unapproved_rubric_blocks_generation(self):
        r = self.check(rubric_ok=False)
        self.assertEqual(self.codes(r), ["RUBRIC_NOT_APPROVED"])
        self.assertFalse(r["ready_to_generate"])

    def test_inexact_allocation_blocks_generation(self):
        r = self.check(10, "33.33")
        self.assertIn("MARKS_ALLOCATION_INVALID", self.codes(r))
        self.assertIsNone(r["checks"]["marks_allocation"])


if __name__ == "__main__":
    unittest.main()
