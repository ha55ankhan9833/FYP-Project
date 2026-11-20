"""
Consistency safeguards for AI rubric results. FLAGS ONLY: no mark is ever
changed here; any flag sets NEEDS_REVIEW and the teacher decides.

Rules (codes appear in the teacher-facing feedback):
  DEFECT_ADMITTED_WITH_FULL_MARKS  explanation describes an unresolved
                                   defect but the criterion got full marks
  PRAISE_WITH_ZERO_MARKS           explanation is purely positive but the
                                   criterion got zero
  UNSUPPORTED_FAILURE_CLAIM        a cited / "observed" failing test does
                                   not exist or actually passed
  FAILURES_IGNORED                 failing tests exist but no criterion
                                   cites any and none are reported observed
  FAILURES_NOT_EXPLAINED           failing tests exist but every criterion
                                   explanation describes the code as correct
  EXECUTION_CLAIM                  the model claims it ran/executed code
  CONSTRAINT_NOT_VERIFIED          a code constraint is VIOLATED or
                                   INCONCLUSIVE in static analysis
  CONSTRAINT_CLAIM_CONTRADICTED    the model says a constraint is met while
                                   static analysis says VIOLATED

The text rules are lexical heuristics; they are labelled as such and only
ever raise a review flag.
"""

import re
from decimal import Decimal

DEFECT_PATTERN = re.compile(
    r"\b(bugs?|incorrect(ly)?|wrong(ly)?|errors?|fails?|failing|failed|"
    r"flaw(s|ed)?|defects?|mistakes?|off-by-one|should be|should use|"
    r"misses|missing|does not (correctly|handle|account|check|work)|"
    r"doesn't (correctly|handle|account|check|work)|not handled|"
    r"cannot|can't|broken|issue with|problem with)\b", re.I)

NEGATED_DEFECT = re.compile(
    r"\b(no|without|free of|not any|zero)\s+(\w+\s+){0,2}"
    r"(bugs?|issues?|errors?|problems?|flaws?|defects?|mistakes?)\b"
    r"|\bdoes not (contain|have|include) (any )?(unnecessary|redundant|"
    r"bugs?|errors?|issues?)\b|\bno unnecessary\b", re.I)

POSITIVE_PATTERN = re.compile(
    r"\b(correct(ly)?|well[- ]structured|readable|clear|efficient|"
    r"appropriate|follows the required|handles? (all|edge))\b", re.I)

EXECUTION_CLAIM = re.compile(
    r"\b(i|we)\s+(ran|run|executed|execute|compiled|tested)\b|"
    r"\bwhen (i|we) (ran|run|executed|tested)\b|\bupon running\b", re.I)

CONSTRAINT_MET_CLAIM = re.compile(
    r"\b(uses|implements|satisfies|meets|follows)\b[^.]{0,60}\b"
    r"(recursion|recursive|loop|function|constraint)", re.I)


def _flag(code, message, criterion=None):
    item = {"code": code, "message": message, "heuristic": code in {
        "DEFECT_ADMITTED_WITH_FULL_MARKS", "PRAISE_WITH_ZERO_MARKS",
        "EXECUTION_CLAIM", "CONSTRAINT_CLAIM_CONTRADICTED",
        "FAILURES_NOT_EXPLAINED"}}
    if criterion:
        item["criterion"] = criterion
    return item


def admits_defect(text):
    return bool(DEFECT_PATTERN.search(NEGATED_DEFECT.sub(" ", text or "")))


def analyse_ai_result(parsed, rubric, evidence, constraint_report=None):
    """
    parsed   - output of ollama_grading_service.parse_criterion_only_response
    rubric   - [{criterion_name, max_marks}]
    evidence - {"tests_run", "tests_passed", "cases": [{test_number, passed}]}
    """

    flags = []
    maxima = {c["criterion_name"]: Decimal(str(c["max_marks"])) for c in rubric}
    cases = {c["test_number"]: c for c in evidence.get("cases", [])}
    failing = {n for n, c in cases.items() if c.get("passed") is False}

    for crit in parsed.get("criteria", []):
        name, mark = crit["criterion_name"], Decimal(str(crit["mark"]))
        text = crit.get("explanation", "")

        if mark == maxima.get(name) and admits_defect(text):
            flags.append(_flag("DEFECT_ADMITTED_WITH_FULL_MARKS",
                               "Explanation describes a defect but the "
                               "criterion received full marks", name))
        if mark == 0 and POSITIVE_PATTERN.search(text) and not admits_defect(text):
            flags.append(_flag("PRAISE_WITH_ZERO_MARKS",
                               "Explanation is positive but the criterion "
                               "received zero", name))
        for number in crit.get("cited_failed_tests", []):
            if number not in failing:
                flags.append(_flag(
                    "UNSUPPORTED_FAILURE_CLAIM",
                    f"Cites test {number} as failing, but "
                    f"{'it passed' if number in cases else 'no such test was supplied'}",
                    name))
        if EXECUTION_CLAIM.search(text):
            flags.append(_flag("EXECUTION_CLAIM",
                               "Explanation claims the model ran the code", name))

    for item in parsed.get("observed_failures", []):
        if item["test_number"] not in failing:
            flags.append(_flag(
                "UNSUPPORTED_FAILURE_CLAIM",
                f"Reports test {item['test_number']} as an observed failure, but "
                f"{'it passed' if item['test_number'] in cases else 'no such test was supplied'}"))

    # Only citations of tests that REALLY failed count as addressing them.
    cited_any = any(set(c.get("cited_failed_tests") or []) & failing
                    for c in parsed.get("criteria", []))
    observed_any = any(i["test_number"] in failing
                       for i in parsed.get("observed_failures", []))
    if failing and not cited_any and not observed_any:
        flags.append(_flag("FAILURES_IGNORED",
                           f"{len(failing)} test(s) failed but the AI cited none "
                           f"of them: {sorted(failing)}"))

    # Live finding (2026-11-17): the model listed the failing tests yet
    # every criterion explanation described the code as correct (it even
    # praised the buggy `d * d < n`). Reporting a failure is not the same
    # as accounting for it in the marks.
    explanations = [c.get("explanation", "") for c in parsed.get("criteria", [])]
    if failing and explanations and not any(admits_defect(t) for t in explanations):
        flags.append(_flag("FAILURES_NOT_EXPLAINED",
                           f"{len(failing)} test(s) failed {sorted(failing)} but "
                           f"no criterion explanation identifies a defect"))

    all_text = " ".join([parsed.get("overall_feedback", "")] +
                        [c.get("explanation", "") for c in parsed.get("criteria", [])])
    if EXECUTION_CLAIM.search(parsed.get("overall_feedback", "")):
        flags.append(_flag("EXECUTION_CLAIM", "Feedback claims the model ran the code"))

    for check in (constraint_report or {}).get("checks", []):
        if check["status"] != "SATISFIED":
            flags.append(_flag(
                "CONSTRAINT_NOT_VERIFIED",
                f"Code constraint '{check['constraint']}' is {check['status']}: "
                f"{check['evidence']}"))
        if check["status"] == "VIOLATED" and CONSTRAINT_MET_CLAIM.search(all_text):
            flags.append(_flag(
                "CONSTRAINT_CLAIM_CONTRADICTED",
                f"AI text suggests '{check['constraint']}' is met, but static "
                f"analysis says VIOLATED"))

    # de-duplicate identical flags
    unique, seen = [], set()
    for f in flags:
        key = (f["code"], f.get("criterion"), f["message"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
