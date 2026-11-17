import re

def extract_explicit_bounds(text):
    """Extract explicitly stated numeric ranges."""
    results = []

    patterns = [
        # N is between 0 and 12
        r"\b([A-Za-z_]\w*)\s+(?:is\s+)?between\s+(-?\d+)\s+and\s+(-?\d+)\b",

        # 0 <= N <= 12
        r"(?<!\w)(-?\d+)\s*<=\s*([A-Za-z_]\w*)\s*<=\s*(-?\d+)\b",

        # N ranges from 0 to 12
        r"\b([A-Za-z_]\w*)\s+ranges?\s+from\s+(-?\d+)\s+to\s+(-?\d+)\b"
    ]

    for index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if index == 1:
                low, variable, high = match.groups()
            else:
                variable, low, high = match.groups()

            low, high = int(low), int(high)

            if low > high:
                continue

            results.append({
                "text": f"{low} <= {variable} <= {high}",
                "evidence": match.group(0),
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
