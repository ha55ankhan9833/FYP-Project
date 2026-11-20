def check_grading_reliability(grading_result, execution_evidence, max_marks):
    """
    Flag suspicious LLM grades for instructor review.

    Does not calculate or change awarded marks.
    Does not authenticate execution evidence.
    """

    if not isinstance(grading_result, dict):
        raise ValueError("Invalid grading result")

    if not isinstance(execution_evidence, dict):
        raise ValueError("Invalid execution evidence")

    if isinstance(max_marks, bool) or not isinstance(max_marks, (int, float)):
        raise ValueError("Invalid maximum marks")

    if max_marks <= 0:
        raise ValueError("Maximum marks must be positive")

    score = grading_result.get("suggested_score")

    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("Invalid suggested score")

    if not 0 <= score <= max_marks:
        raise ValueError("Suggested score outside valid range")

    tests_run = execution_evidence.get("tests_run")
    tests_passed = execution_evidence.get("tests_passed")

    if type(tests_run) is not int or tests_run <= 0:
        raise ValueError("Invalid test count")

    if type(tests_passed) is not int or not 0 <= tests_passed <= tests_run:
        raise ValueError("Invalid passed count")

    flags = []

    if tests_passed == 0 and score == max_marks:
        flags.append(
            "All supplied tests failed but LLM awarded full marks."
        )

    if tests_passed == tests_run and score == 0:
        flags.append(
            "All supplied tests passed but LLM awarded zero marks."
        )

    if tests_passed == 0 and score >= max_marks * 0.8:
        flags.append(
            "High LLM score despite zero passed tests."
        )

    # Live evidence (2026-11-17): qwen2.5-coder:3b gave full rubric marks
    # to a d*d < n off-by-one that failed 2 of 10 tests. Flag only;
    # marks are never changed by this guard.
    if 0 < tests_passed < tests_run and score == max_marks:
        flags.append(
            "Full rubric marks despite failed tests; check the AI's "
            "reasoning against the failing cases."
        )

    return {
        "suggested_score": score,
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "reliability_flags": flags,
        "manual_review_required": bool(flags),
        "grade_modified": False,
        "evidence_authenticated": False
    }


if __name__ == "__main__":
    print("GRADING RELIABILITY GUARD READY")
