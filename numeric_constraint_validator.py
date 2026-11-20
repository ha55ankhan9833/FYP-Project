import re

# A variable, optionally indexed: N, A[i], grid[i][j], a_i.
_VAR = r"[A-Za-z_]\w*(?:\s*\[\s*[A-Za-z_]\w*\s*\])*"
# An integer, optionally a power: 100, -1000, 10^9, -10^18.
_NUM = r"-?\d+(?:\s*\^\s*\d+)?"
# The number must end there: "10^9" is one value, never "10" followed by "^9".
_END = r"(?![\w^*/]|\.\d)"
_LE = r"(?:<=|≤)"

PATTERNS = [
    # N is between 0 and 12
    re.compile(rf"(?<![\w\]])({_VAR})\s+(?:is\s+)?between\s+({_NUM}){_END}\s+and\s+({_NUM}){_END}",
               re.IGNORECASE),
    # 0 <= N <= 12   /   -1000 <= A[i] <= 1000   /   1 ≤ N ≤ 10^9
    re.compile(rf"(?<![\w^])({_NUM}){_END}\s*{_LE}\s*({_VAR})\s*{_LE}\s*({_NUM}){_END}",
               re.IGNORECASE),
    # N ranges from 0 to 12
    re.compile(rf"(?<![\w\]])({_VAR})\s+ranges?\s+from\s+({_NUM}){_END}\s+to\s+({_NUM}){_END}",
               re.IGNORECASE),
]


def _value(number):
    base, _, exponent = number.replace(" ", "").partition("^")
    value = int(base)
    if exponent:
        sign = -1 if value < 0 else 1
        value = sign * (abs(value) ** int(exponent))
    return value


def _canonical(text):
    return re.sub(r"\s+", "", text)


def extract_explicit_bounds(text):
    """
    Extract explicitly stated numeric ranges.

    Each distinct (variable, low, high) is returned once, in order of first
    appearance; the same bound restated elsewhere (e.g. in the statement
    and again in the input format, or as "N is between 1 and 100") is not
    repeated. Different variables or different limits are always kept.
    """
    found = []

    for index, pattern in enumerate(PATTERNS):
        for match in pattern.finditer(text):
            if index == 1:
                low, variable, high = match.groups()
            else:
                variable, low, high = match.groups()

            low_text, high_text = _canonical(low), _canonical(high)
            if _value(low_text) > _value(high_text):
                continue

            variable = _canonical(variable)
            found.append((match.start(), variable, low_text, high_text, match.group(0)))

    results, seen = [], set()
    for _position, variable, low, high, evidence in sorted(found):
        key = (variable, _value(low), _value(high))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "text": f"{low} <= {variable} <= {high}",
            "evidence": evidence,
            "status": "NEEDS_TEACHER_REVIEW"
        })

    return results


if __name__ == "__main__":
    problem = (
        "Write a C++ program to calculate factorial "
        "of N using recursion. N is between 0 and 12."
    )

    print("Extracted constraints:")
    for item in extract_explicit_bounds(problem):
        print(item)
