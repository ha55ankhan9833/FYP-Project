"""
Exact marks allocation between the 10 equally weighted test cases and the
teacher-approved rubric.

    test_marks     = total_marks x validation_percentage / 100
    rubric_marks   = total_marks - test_marks
    per_case_marks = test_marks / 10

Every value is a Python Decimal; nothing is rounded. The database stores
marks as DECIMAL(10,2) (Assessment.total_marks, TestCase.marks) and
DECIMAL(5,2) (validation_percentage, criterion max_marks), so a value is
"representable" only when it has at most 2 decimal places. When test_marks
or per_case_marks would need a third decimal place the allocation is
REFUSED with a clear error and the nearest representable percentages; the
teacher's choice is never silently rounded or changed, and the 10 cases
never receive unequal marks.

grading_allocation.split_allocation (used by grading) rounds to 0.01; for
every allocation accepted here that rounding is a no-op, so grading gives
identical results.
"""

from decimal import Decimal, InvalidOperation

from test_case_policy import REQUIRED_TEST_CASE_COUNT

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
MAX_TOTAL_MARKS = Decimal("99999999.99")      # DECIMAL(10,2)
_SEARCH_WINDOW = 2000                          # per-case-cent steps scanned per side


class MarksAllocationError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def _decimal(value, name):
    if isinstance(value, bool) or value is None:
        raise MarksAllocationError(f"INVALID_{name.upper()}", f"{name} must be a number")
    if isinstance(value, float):
        value = repr(value)                    # 0.1 -> "0.1", not the binary expansion
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise MarksAllocationError(f"INVALID_{name.upper()}", f"{name} must be a number")
    if not number.is_finite():
        raise MarksAllocationError(f"INVALID_{name.upper()}", f"{name} must be finite")
    return number


def _has_two_places(value):
    return value == value.quantize(CENT) if value.adjusted() < 20 else False


def _fmt(value):
    return str(value.quantize(CENT))


def nearest_valid_percentages(total_marks, validation_percentage,
                              case_count=REQUIRED_TEST_CASE_COUNT):
    """Closest percentages (<= and >= the requested one) that give an exact
    2-decimal test allocation AND an exact 2-decimal equal per-case mark."""
    total = Decimal(total_marks)
    target = Decimal(validation_percentage)
    # per-case marks c = k cents  ->  test = case_count*k/100  ->  pct = case_count*k/total
    k0 = int((target * total / (case_count * HUNDRED)) / CENT)
    below = above = None
    for step in range(_SEARCH_WINDOW + 1):
        for k in (k0 - step, k0 + 1 + step):
            if k < 0:
                continue
            pct = Decimal(case_count) * k / total
            if pct > HUNDRED or not _has_two_places(pct):
                continue
            if pct <= target and (below is None or pct > below):
                below = pct
            if pct >= target and (above is None or pct < above):
                above = pct
        if below is not None and above is not None:
            break
    return [_fmt(p) for p in sorted({p for p in (below, above) if p is not None})]


def compute_allocation(total_marks, validation_percentage,
                       case_count=REQUIRED_TEST_CASE_COUNT):
    """Return the exact allocation or raise MarksAllocationError."""
    total = _decimal(total_marks, "total_marks")
    pct = _decimal(validation_percentage, "validation_percentage")

    if total <= 0:
        raise MarksAllocationError("INVALID_TOTAL_MARKS", "total_marks must be greater than 0")
    if total > MAX_TOTAL_MARKS or not _has_two_places(total):
        raise MarksAllocationError(
            "INVALID_TOTAL_MARKS",
            "total_marks must have at most 2 decimal places and fit DECIMAL(10,2)")
    if not Decimal("0") <= pct <= HUNDRED:
        raise MarksAllocationError("INVALID_VALIDATION_PERCENTAGE",
                                   "validation_percentage must be between 0 and 100")
    if not _has_two_places(pct):
        raise MarksAllocationError("INVALID_VALIDATION_PERCENTAGE",
                                   "validation_percentage allows at most 2 decimal places")

    test_marks = total * pct / HUNDRED
    details = {"total_marks": _fmt(total), "validation_percentage": _fmt(pct),
               "test_case_count": case_count}

    if not _has_two_places(test_marks):
        details["exact_test_marks"] = str(test_marks.normalize())
        details["nearest_valid_percentages"] = nearest_valid_percentages(total, pct, case_count)
        raise MarksAllocationError(
            "TEST_MARKS_NOT_REPRESENTABLE",
            f"{_fmt(pct)}% of {_fmt(total)} is {test_marks.normalize()} marks, which needs more "
            "than 2 decimal places; choose a different test-case percentage", details)

    per_case = test_marks / Decimal(case_count)
    if not _has_two_places(per_case):
        details["test_marks"] = _fmt(test_marks)
        details["exact_per_case_marks"] = str(per_case.normalize())
        details["nearest_valid_percentages"] = nearest_valid_percentages(total, pct, case_count)
        raise MarksAllocationError(
            "CASE_MARKS_NOT_EQUAL_SHARE",
            f"{_fmt(test_marks)} test marks cannot be split into {case_count} equal shares of at "
            f"most 2 decimal places ({per_case.normalize()} each); choose a different "
            "test-case percentage", details)

    rubric_marks = total - test_marks
    return {
        "total_marks": _fmt(total),
        "validation_percentage": _fmt(pct),
        "test_case_count": case_count,
        "test_marks": _fmt(test_marks),
        "per_case_marks": _fmt(per_case),
        "rubric_marks": _fmt(rubric_marks),
        "rubric_required": rubric_marks > 0,
        "formula": {
            "test_marks": f"{_fmt(total)} x {_fmt(pct)} / 100 = {_fmt(test_marks)}",
            "rubric_marks": f"{_fmt(total)} - {_fmt(test_marks)} = {_fmt(rubric_marks)}",
            "per_case_marks": f"{_fmt(test_marks)} / {case_count} = {_fmt(per_case)}",
        },
    }


def allocation_decimals(allocation):
    """Decimal view of compute_allocation's string result."""
    return {k: Decimal(allocation[k]) for k in
            ("total_marks", "validation_percentage", "test_marks", "per_case_marks",
             "rubric_marks")}
