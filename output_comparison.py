"""
Output comparison policies for deciding whether a test passed.

The ONLY policy in use is TRIMMED_EXACT, which is exactly what the system
has always done (`actual.strip() == expected.strip()`): leading/trailing
whitespace (including the final newline) is ignored, everything else -
case, inner spaces, line breaks - must match.

Other policies are defined so the rule is explicit and testable, but the
Assessment table has no column to store a per-assessment choice, so every
assessment uses DEFAULT_POLICY. Selecting another policy per assessment
would need an approved schema change.
"""

TRIMMED_EXACT = "TRIMMED_EXACT"
EXACT = "EXACT"
WHITESPACE_NORMALIZED = "WHITESPACE_NORMALIZED"

DEFAULT_POLICY = TRIMMED_EXACT
POLICIES = (TRIMMED_EXACT, EXACT, WHITESPACE_NORMALIZED)


def outputs_match(actual, expected, policy=DEFAULT_POLICY):
    if policy not in POLICIES:
        raise ValueError(f"Unknown comparison policy {policy!r}")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    if policy == EXACT:
        return actual == expected
    if policy == WHITESPACE_NORMALIZED:
        return actual.split() == expected.split()
    return actual.strip() == expected.strip()
