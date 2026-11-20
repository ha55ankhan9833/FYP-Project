"""
Rule-based extraction of CODE requirements from a problem statement.

Each statement is split into clauses. In every clause a technique mention
(recursion, loop, for loop, conditionals, sorting, ...) gets a polarity:

  1. a postfix cue right after it: "... is not allowed / prohibited",
     "... must not be used", "... is required", "... is optional";
  2. else a direct negator at most 2 words before it: "no recursion",
     "without (using) loops", "avoid recursion", "not recursion";
  3. else a usage verb at most 3 words before it ("use", "using",
     "implement", "write", "solve", ...), whose polarity comes from a
     modal at most 3 words before that verb ("must not use" -> PROHIBITED,
     "must use" -> REQUIRED, "may use" -> OPTIONAL, none -> REQUIRED);
  4. else no rule: a bare mention ("defined recursively", "count loops in
     a graph", "must not exceed 100 iterations") is ignored.

OPTIONAL never becomes a constraint. A prohibition is never turned into a
requirement. If both polarities are found for the same technique the rule
is withdrawn and reported for manual review.

Only keys that code_constraint_checker understands are emitted (see
SUPPORTED). Anything else that reads like a coding rule is returned as
`manual_review` text and is never converted into a constraint.
"""

import re

NEEDS_REVIEW = "NEEDS_TEACHER_REVIEW"
STATIC_CHECK = "STATIC_CHECK"
TEACHER_REVIEW_ONLY = "TEACHER_REVIEW_ONLY"

REQUIRED = "REQUIRED"
PROHIBITED = "PROHIBITED"
OPTIONAL = "OPTIONAL"

# Constraint key -> how code_constraint_checker verifies it.
SUPPORTED = {
    "recursion_required": STATIC_CHECK,
    "recursion_prohibited": STATIC_CHECK,
    "loop_required": STATIC_CHECK,
    "loops_prohibited": STATIC_CHECK,
    "for_loop_required": STATIC_CHECK,
    "conditional_required": STATIC_CHECK,
    "function_required": STATIC_CHECK,
    "stl_container_required": STATIC_CHECK,
    "hash_map_required": STATIC_CHECK,
    "function_prohibited:sort": STATIC_CHECK,
    "function_prohibited:stable_sort": STATIC_CHECK,
    "function_prohibited:partial_sort": STATIC_CHECK,
    "dynamic_programming_required": TEACHER_REVIEW_ONLY,
    "gaussian_elimination_prohibited": TEACHER_REVIEW_ONLY,
}

SORT_FUNCTIONS = ["function_prohibited:sort", "function_prohibited:stable_sort",
                  "function_prohibited:partial_sort"]

# technique -> (mention regex, keys if REQUIRED, keys if PROHIBITED)
# None = this polarity has no safe key -> manual review.
TECHNIQUES = {
    "for_loop": (r"\bfor[\s-]*loops?\b|\bfor\s*\(\s*\)\s*loops?\b",
                 ["for_loop_required"], None),
    "while_loop": (r"\b(?:do[\s-]*)?while[\s-]*loops?\b", None, None),
    "loop": (r"\b(?:loops?|looping|iterative(?:ly)?)\b",
             ["loop_required"], ["loops_prohibited"]),
    "recursion": (r"\brecursi(?:on|ve(?:ly)?)\b",
                  ["recursion_required"], ["recursion_prohibited"]),
    "conditional": (r"\bif\s*/\s*else\b|\bif\s*-\s*else\b|\bif\s+else\b|"
                    r"\bif\s+(?:and\s+else\s+)?statements?\b|\bconditional\s+statements?\b|"
                    r"\bconditionals\b|\bconditional\s+(?:logic|branching)\b",
                    ["conditional_required"], None),
    "sorting_algorithms": (r"\b(?:stl|built-?in|library|standard)\s+sort(?:ing)?\b(?:\s+(?:algorithms?|functions?))?|"
                           r"\bsorting\s+(?:algorithms?|functions?|routines?)\b|"
                           r"\bstd::(?:stable_|partial_)?sort\b|\b(?:stable|partial)_sort\b",
                           None, SORT_FUNCTIONS),
    "sort_call": (r"\bsort\s*\(\s*\)|\bsort\s+function\b",
                  None, ["function_prohibited:sort"]),
    "dynamic_programming": (r"\bdynamic\s+programming\b",
                            ["dynamic_programming_required"], None),
    "function": (r"\b(?:dedicated|separate|user-defined|helper)\s+(?:value-returning\s+)?functions?\b",
                 ["function_required"], None),
    "hash_map": (r"\bhash\s*maps?\b|\bunordered_map\b", ["hash_map_required"], None),
    "stl_container": (r"\bstd::(?:set|map|vector)\b", ["stl_container_required"], None),
    "gaussian_elimination": (r"\bgaussian\s+elimination\b", None,
                             ["gaussian_elimination_prohibited"]),
}

# More specific techniques hide generic matches on the same text.
SHADOWS = {"for_loop": ["loop"], "while_loop": ["loop"], "sorting_algorithms": ["sort_call"],
           "hash_map": ["stl_container"]}

# Modals govern a following usage verb ("must not use", "should implement").
_GNEG = (r"must\s+not|mustn'?t|should\s+not|shouldn'?t|do\s+not|don'?t|does\s+not|doesn'?t|"
         r"cannot|can\s*not|can'?t|may\s+not|(?:are|is)\s+not\s+allowed\s+to|not\s+allowed\s+to|"
         r"(?:are|is)\s+not\s+permitted\s+to|never")
_GREQ = (r"must|should|need\s+to|needs\s+to|have\s+to|has\s+to|(?:is\s+|are\s+)?required\s+to|"
         r"mandatory\s+to|make\s+sure\s+to|ensure\s+(?:you|that)")
_GOPT = r"may|can|optionally|(?:are\s+|is\s+)?allowed\s+to|free\s+to|either"
# Direct negators attach to a nearby technique ("no recursion", "without loops").
_DNEG = r"without|avoid(?:ing)?|instead\s+of|rather\s+than|no|not"
_VERB = (r"use|uses|using|used|implement|implemented|implementing|write|written|writing|"
         r"solve|solved|solving|employ|apply|via|with|by|compute|computed|done")

PREFIX_CUE = re.compile(rf"\b(?:(?P<gneg>{_GNEG})|(?P<greq>{_GREQ})|(?P<gopt>{_GOPT})|"
                        rf"(?P<dneg>{_DNEG})|(?P<verb>{_VERB}))\b", re.IGNORECASE)
GOVERN = {"gneg": PROHIBITED, "dneg": PROHIBITED, "greq": REQUIRED, "gopt": OPTIONAL}
DNEG_WINDOW = 2       # "without using loops", "no recursion"
VERB_WINDOW = 3       # "use a for loop", "solve the problem recursively"
MODAL_WINDOW = 3      # "must not directly use", "must also use"
SUFFIX_CUE = re.compile(
    r"^[\s,]*(?:\([^)]*\)\s*)?(?:"
    r"(?P<neg>(?:is|are)\s+(?:not\s+(?:allowed|permitted)|prohibited|forbidden|banned|disallowed)"
    r"|(?:must|should|may)\s+not\s+be\s+used|not\s+allowed|prohibited|forbidden)"
    r"|(?P<req>(?:is|are)\s+(?:required|mandatory|compulsory)|(?:must|should)\s+be\s+used)"
    r"|(?P<opt>(?:is|are)\s+(?:optional|allowed|permitted)|(?:may|can)\s+be\s+used))\b",
    re.IGNORECASE)

# A clause that looks like a coding rule even though no supported technique
# was recognised in it (e.g. "Use cin and cout").
DIRECTIVE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:you\s+)?(?:must|should|do\s+not|don'?t|never|avoid|use|only\s+use|"
    r"cannot|may\s+not|are\s+not\s+allowed|is\s+not\s+allowed)\b|"
    r"\b(?:must|should)\s+(?:not\s+)?(?:use|include|call|declare|define|implement)\b|"
    r"\b(?:not\s+allowed|prohibited|forbidden)\b",
    re.IGNORECASE)
CODE_VOCAB = re.compile(
    r"\b(?:cin|cout|printf|scanf|getline|stl|library|libraries|header|#include|"
    r"function|functions|array|arrays|vector|pointer|pointers|string|class|struct|global|goto|"
    r"while|do-while|switch|break|continue|algorithm|algorithms|built-?in|variable|variables|"
    r"loop|loops|recursion|recursive|operator|template|lambda|namespace|bits/stdc\+\+\.h)\b|"
    r"<\s*\w+(?:\.h)?\s*>|\b\w+\s*\(\s*\)",
    re.IGNORECASE)

CLAUSE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|(?<=\S)\s+(?=[-*•]\s)")
# Coordinated clauses keep their own modal: "use X and must not use Y".
SUB_SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:but|and|while|whereas)\s+(?=(?:you\s+)?(?:must|should|do|don'?t|"
                       r"cannot|can'?t|may|never|avoid|use|using)\b)", re.IGNORECASE)


def _clauses(text):
    for part in CLAUSE_SPLIT.split(text or ""):
        part = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", part).strip()
        if not part:
            continue
        for sub in SUB_SPLIT.split(part):
            sub = sub.strip(" \t,")
            if sub:
                yield sub


def _words(text):
    return len(re.findall(r"\S+", text))


def _prefix_polarity(clause, start):
    cues = list(PREFIX_CUE.finditer(clause[:start]))

    negators = [c for c in cues if c.lastgroup == "dneg"]
    if negators and _words(clause[negators[-1].end():start]) <= DNEG_WINDOW:
        return PROHIBITED

    verbs = [c for c in cues if c.lastgroup == "verb"
             and _words(clause[c.end():start]) <= VERB_WINDOW]
    if not verbs:
        return None
    verb = verbs[-1]
    governors = [c for c in cues if c.lastgroup in GOVERN and c.end() <= verb.start()
                 and _words(clause[c.end():verb.start()]) <= MODAL_WINDOW]
    return GOVERN[governors[-1].lastgroup] if governors else REQUIRED


def _polarity(clause, start, end):
    suffix = SUFFIX_CUE.match(clause[end:end + 60])
    prefix = _prefix_polarity(clause, start)
    if suffix:
        rule = {"neg": PROHIBITED, "req": REQUIRED, "opt": OPTIONAL}[suffix.lastgroup]
        # "No recursion is allowed": the negator wins over "is allowed".
        return PROHIBITED if rule == OPTIONAL and prefix == PROHIBITED else rule
    return prefix


def _mentions(clause):
    found = {}
    for name, (pattern, _req, _proh) in TECHNIQUES.items():
        spans = [m.span() for m in re.finditer(pattern, clause, re.IGNORECASE)]
        if spans:
            found[name] = spans
    for specific, generics in SHADOWS.items():
        for span in found.get(specific, []):
            for generic in generics:
                found[generic] = [s for s in found.get(generic, [])
                                  if s[1] <= span[0] or s[0] >= span[1]]
    return {k: v for k, v in found.items() if v}


def extract_code_requirements(statement):
    """
    Returns {"code_constraints": [...], "manual_review": [...]}.

    code_constraints: {"type", "evidence", "status", "rule", "enforcement"}
    manual_review:    {"text", "rule", "reason"}  (never auto-approved)
    """
    hits = {}            # key -> {"evidence", "rule"}
    polarities = {}      # technique -> set of polarities
    manual = []

    for clause in _clauses(statement):
        mentions = _mentions(clause)
        directive_handled = False

        for technique, spans in mentions.items():
            _pattern, req_keys, proh_keys = TECHNIQUES[technique]
            for start, end in spans:
                rule = _polarity(clause, start, end)
                if rule in (None, OPTIONAL):
                    continue
                directive_handled = True
                polarities.setdefault(technique, set()).add(rule)
                keys = req_keys if rule == REQUIRED else proh_keys
                if keys is None:
                    manual.append({"text": clause[:300], "rule": rule,
                                   "reason": "NOT_IN_SUPPORTED_TAXONOMY"})
                    continue
                for key in keys:
                    hits.setdefault(key, {"evidence": clause[:300], "rule": rule,
                                          "technique": technique})

        if not directive_handled and DIRECTIVE.search(clause) and CODE_VOCAB.search(clause):
            manual.append({"text": clause[:300], "rule": "UNKNOWN",
                           "reason": "NOT_IN_SUPPORTED_TAXONOMY"})

    # Contradictory instructions for one technique: keep neither.
    for technique, rules in polarities.items():
        if {REQUIRED, PROHIBITED} <= rules:
            for key in [k for k, v in hits.items() if v["technique"] == technique]:
                del hits[key]
            manual.append({"text": f"Conflicting instructions about {technique.replace('_', ' ')}",
                           "rule": "CONFLICT", "reason": "CONFLICTING_INSTRUCTIONS"})

    # A specific requirement makes the generic one redundant.
    if "for_loop_required" in hits:
        hits.pop("loop_required", None)
    if "recursion_prohibited" in hits:
        hits.pop("recursion_required", None)
    if "loops_prohibited" in hits:
        hits.pop("loop_required", None)

    constraints = [{"type": key, "evidence": value["evidence"], "status": NEEDS_REVIEW,
                    "rule": value["rule"], "enforcement": SUPPORTED[key]}
                   for key, value in hits.items()]

    seen, unique_manual = set(), []
    for item in manual:
        marker = (item["text"].casefold(), item["rule"])
        if marker not in seen:
            seen.add(marker)
            unique_manual.append(item)

    return {"code_constraints": constraints, "manual_review": unique_manual}


def manual_review_warnings(items):
    """Top-level analysis warnings (copied into the draft as ai_warnings)."""
    return [{"code": "CODE_REQUIREMENT_NEEDS_MANUAL_REVIEW",
             "message": ("Not converted to an automatic code constraint ("
                         + ("conflicting instructions" if item["rule"] == "CONFLICT"
                            else "no supported check") + "): " + item["text"])[:500]}
            for item in items]
