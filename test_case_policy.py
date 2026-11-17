"""
Single source of truth for the FYP test-case count rule (2026-11-17):

    Every assessment has EXACTLY 10 equally weighted test cases.
    Test Score = ROUND_HALF_UP(Allocated Test Marks x Passed Tests / 10, 2)

Enforced at generation (test_case_builder), persistence
(secure_test_case_save), loading for grading (grading_test_case_loader)
and grading (grading_pipeline). Scoring itself stays in
equal_weight_test_scoring / grading_score_calculator.
"""

REQUIRED_TEST_CASE_COUNT = 10


class TestCaseCountError(ValueError):
    pass


def enforce_test_case_count(count, where="Assessment"):
    if type(count) is not int or count != REQUIRED_TEST_CASE_COUNT:
        raise TestCaseCountError(
            f"{where} must have exactly {REQUIRED_TEST_CASE_COUNT} test "
            f"cases (found {count})")
    return count
