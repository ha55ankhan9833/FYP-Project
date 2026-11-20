"""
Static verification of teacher-approved CODE constraints (no execution).

Each constraint gets one status:
  SATISFIED     - a justified lexical check confirms it
  VIOLATED      - a justified lexical check contradicts it
  INCONCLUSIVE  - cannot be established reliably by static checks
                  (macros, lambdas, function pointers, goto, free text...)

Rules
- Comments, string literals, character literals and raw strings are
  removed BEFORE any check, so a keyword in a comment/string never counts.
- This module never deducts marks. VIOLATED and INCONCLUSIVE results set
  requires_review=True so the teacher decides; any penalty must come from
  an explicit teacher rubric policy (none exists in the current schema).
- Constraint names are the ones produced by ai_constraint_service
  (code_requirement_extractor.SUPPORTED, e.g. recursion_required,
  for_loop_required, conditional_required) plus structured teacher forms:
      function_prohibited:NAME   function_required:NAME  (a call NAME(...))
      helper_required:NAME       (student DEFINES NAME and calls it)
      header_prohibited:NAME     header_required:NAME
      container_required:NAME    container_prohibited:NAME

LIMITS: these are lexical checks on comment/literal-free text with a
heuristic function finder. They are NOT a C++ parser or semantic
analysis: templates, overloading, namespaces, macros and indirect calls
can defeat them, which is why uncertain cases report INCONCLUSIVE.
"""

import re

SATISFIED = "SATISFIED"
VIOLATED = "VIOLATED"
INCONCLUSIVE = "INCONCLUSIVE"

LOOP_KEYWORDS = {"for", "while", "do"}
CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return",
                    "sizeof", "do", "else", "new", "delete", "throw"}
CONTAINERS = {"vector", "set", "map", "unordered_map", "unordered_set",
              "multiset", "multimap", "deque", "list", "array", "stack",
              "queue", "priority_queue"}
HASH_CONTAINERS = {"unordered_map", "unordered_set"}
_IDENT = r"[A-Za-z_]\w*"


# ----------------------------------------------------------------------
# Lexical preparation
# ----------------------------------------------------------------------

def strip_comments_and_literals(source):
    """Return code with comments removed and every literal emptied."""
    out = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":                       # line comment
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and nxt == "*":                       # block comment
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        raw = re.match(r'(?:u8|u|U|L)?R"([^()\\\s]{0,16})\(', source[i:])
        if raw:                                           # raw string
            end = source.find(")" + raw.group(1) + '"', i + raw.end())
            i = n if end == -1 else end + len(raw.group(1)) + 2
            out.append('""')
            continue
        if c in "\"'":                                    # string / char
            quote, j = c, i + 1
            while j < n and source[j] != quote:
                j += 2 if source[j] == "\\" else 1
            i = j + 1
            out.append(quote * 2)
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _tokens(code):
    return re.findall(rf"{_IDENT}|::|[{{}}()\[\];,<>*&=]", code)


def _function_definitions(code):
    """
    Heuristic top-level function definitions: NAME ( ... ) [qualifiers] {
    Returns {name: body_text}. Overloads are merged.
    """
    defs = {}
    depth = 0
    i = 0
    pattern = re.compile(
        rf"({_IDENT})\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*"
        rf"(?:const|noexcept|override|final|\s|->\s*[\w:<>]+)*\{{")
    while i < len(code):
        ch = code[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            m = pattern.match(code, i)
            if m and m.group(1) not in CONTROL_KEYWORDS:
                start = m.end()
                level, j = 1, start
                while j < len(code) and level:
                    level += {"{": 1, "}": -1}.get(code[j], 0)
                    j += 1
                defs.setdefault(m.group(1), "")
                defs[m.group(1)] += code[start:j - 1] + "\n"
                i = j
                continue
        i += 1
    return defs


def _calls(body, names):
    return {name for name in names
            if re.search(rf"(?<![\w.>:]){re.escape(name)}\s*\(", body)}


def _analyse(source):
    full = strip_comments_and_literals(source)
    # Keywords inside preprocessor lines (#define LOOP ... for ...) are not
    # evidence of real code; macros are tracked separately as a reason
    # for INCONCLUSIVE instead.
    code = re.sub(r"^\s*#.*(?:\\\n.*)*$", "", full, flags=re.M)
    tokens = set(_tokens(code))
    defs = _function_definitions(code)
    graph = {name: _calls(body, defs) for name, body in defs.items()}

    def in_cycle(start):
        stack, seen = list(graph.get(start, ())), set()
        while stack:
            node = stack.pop()
            if node == start:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, ()))
        return False

    recursive = sorted(name for name in defs if in_cycle(name))
    return {
        "code": code,
        "tokens": tokens,
        "defs": defs,
        "recursive": recursive,
        "has_macros": bool(re.search(r"^\s*#\s*define\b", full, re.M)),
        "has_lambda": bool(re.search(r"\[[^\[\]]*\]\s*(\([^()]*\))?\s*"
                                     r"(mutable\s*)?(->\s*[\w:<>]+\s*)?\{", code)),
        "has_function_objects": bool(re.search(
            r"\bstd\s*::\s*function\b|\(\s*\*\s*" + _IDENT + r"\s*\)\s*\(", code)),
        "has_goto": "goto" in tokens,
        "includes": set(re.findall(r"#\s*include\s*<\s*([\w./]+)\s*>", full)),
    }


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------

def _result(constraint, status, evidence):
    return {"constraint": constraint, "status": status, "method": "static",
            "evidence": evidence}


def _check(constraint, a):
    name = constraint.strip()
    key = name.lower()
    loops = sorted(a["tokens"] & LOOP_KEYWORDS)
    indirect = a["has_lambda"] or a["has_function_objects"]

    if key in ("loop_required", "loops_required"):
        if loops:
            return _result(name, SATISFIED, f"loop keyword(s) in code: {loops}")
        if a["has_macros"] or a["has_goto"]:
            return _result(name, INCONCLUSIVE, "no loop keyword, but macros/goto "
                           "could hide iteration")
        return _result(name, VIOLATED, "no for/while/do in code")

    if key == "for_loop_required":
        if "for" in a["tokens"]:
            return _result(name, SATISFIED, "'for' loop keyword in code")
        if a["has_macros"]:
            return _result(name, INCONCLUSIVE, "no 'for' keyword, but macros could "
                           "hide one")
        return _result(name, VIOLATED, "no 'for' loop in code")

    if key == "conditional_required":
        # '?' can only be the conditional operator once literals are removed.
        branches = sorted(a["tokens"] & {"if", "switch"})
        if branches or "?" in a["code"]:
            return _result(name, SATISFIED, "conditional(s) in code: "
                           f"{branches + (['?:'] if '?' in a['code'] else [])}")
        if a["has_macros"]:
            return _result(name, INCONCLUSIVE, "no if/switch/?:, but macros could "
                           "hide one")
        return _result(name, VIOLATED, "no if/else, switch or ?: in code")

    if key in ("loops_prohibited", "loop_prohibited"):
        if loops:
            return _result(name, VIOLATED, f"loop keyword(s) in code: {loops}")
        if a["has_macros"] or a["has_goto"]:
            return _result(name, INCONCLUSIVE, "macros/goto could hide iteration")
        if re.search(r"\b(for_each|accumulate|transform|iota)\s*\(", a["code"]):
            return _result(name, INCONCLUSIVE, "STL algorithm iterates internally")
        return _result(name, SATISFIED, "no for/while/do/goto in code")

    if key == "recursion_required":
        if a["recursive"]:
            return _result(name, SATISFIED,
                           f"self/mutual call cycle in: {a['recursive']}")
        if indirect or a["has_macros"]:
            return _result(name, INCONCLUSIVE, "no direct recursion; lambdas, "
                           "function objects or macros could recurse indirectly")
        return _result(name, VIOLATED, "no function calls itself directly or "
                       "mutually")

    if key in ("recursion_prohibited", "no_recursion"):
        if a["recursive"]:
            return _result(name, VIOLATED,
                           f"self/mutual call cycle in: {a['recursive']}")
        if indirect or a["has_macros"]:
            return _result(name, INCONCLUSIVE, "lambdas, function objects or "
                           "macros could recurse indirectly")
        return _result(name, SATISFIED, "no recursive call cycle found")

    if key == "function_required":
        helpers = sorted(set(a["defs"]) - {"main"})
        main_body = a["defs"].get("main", "")
        called = sorted(_calls(main_body, helpers)) if helpers else []
        if called:
            return _result(name, SATISFIED,
                           f"user-defined function(s) called from main: {called}")
        if helpers:
            return _result(name, INCONCLUSIVE, f"defined but not called "
                           f"directly from main: {helpers}")
        return _result(name, VIOLATED, "no user-defined function besides main")

    if key in ("stl_container_required", "hash_map_required"):
        wanted = HASH_CONTAINERS if key == "hash_map_required" else CONTAINERS
        used = sorted(wanted & a["tokens"])
        if used:
            return _result(name, SATISFIED, f"container type(s) used: {used}")
        if a["has_macros"]:
            return _result(name, INCONCLUSIVE, "macros could hide the container")
        return _result(name, VIOLATED, f"none of {sorted(wanted)} used")

    m = re.fullmatch(rf"helper_required\s*:\s*({_IDENT})", name, flags=re.I)
    if m:
        target = m.group(1)
        if target not in a["defs"]:
            if a["has_macros"]:
                return _result(name, INCONCLUSIVE,
                               f"{target} not found; macros could define it")
            return _result(name, VIOLATED, f"no definition of {target}()")
        callers = sorted(n for n, body in a["defs"].items()
                         if n != target and target in _calls(body, {target}))
        if callers:
            return _result(name, SATISFIED,
                           f"{target}() defined and called from {callers}")
        if target in a["recursive"]:
            return _result(name, INCONCLUSIVE, f"{target}() only calls itself")
        return _result(name, INCONCLUSIVE, f"{target}() defined but never called "
                       "from another function")

    m = re.fullmatch(rf"container_(required|prohibited)\s*:\s*({_IDENT})",
                     name, flags=re.I)
    if m:
        rule, target = m.group(1).lower(), m.group(2)
        found = target in a["tokens"]
        if a["has_macros"] and not found:
            return _result(name, INCONCLUSIVE, f"macros could hide {target}")
        if rule == "required":
            return _result(name, SATISFIED if found else VIOLATED,
                           f"{target} {'used' if found else 'not used'}")
        return _result(name, VIOLATED if found else SATISFIED,
                       f"{target} {'used' if found else 'not used'}")

    m = re.fullmatch(rf"(function|header)_(prohibited|required)\s*:\s*({_IDENT}"
                     rf"(?:[./]{_IDENT})*)", name, flags=re.I)
    if m:
        kind, rule, target = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if kind == "function":
            found = bool(re.search(rf"(?<![\w.>]){re.escape(target)}\s*\(",
                                   a["code"]))
            where = f"call to {target}()"
        else:
            found = target in a["includes"]
            where = f"#include <{target}>"
        if a["has_macros"] and not found:
            return _result(name, INCONCLUSIVE, "macros could hide " + where)
        if rule == "prohibited":
            return _result(name, VIOLATED if found else SATISFIED,
                           (where + " present") if found else ("no " + where))
        return _result(name, SATISFIED if found else VIOLATED,
                       (where + " present") if found else ("no " + where))

    return _result(name, INCONCLUSIVE,
                   "no reliable static check for this constraint; teacher "
                   "review required")


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def parse_code_constraints(text_or_list):
    if text_or_list is None:
        return []
    if isinstance(text_or_list, str):
        items = text_or_list.splitlines()
    else:
        items = list(text_or_list)
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


def check_code_constraints(source_code, constraints):
    """
    Returns {"checks": [...], "requires_review": bool,
             "summary": {SATISFIED: n, VIOLATED: n, INCONCLUSIVE: n},
             "marks_changed": False}
    """
    constraints = parse_code_constraints(constraints)
    if not constraints:
        return {"checks": [], "requires_review": False,
                "summary": {SATISFIED: 0, VIOLATED: 0, INCONCLUSIVE: 0},
                "marks_changed": False}
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("Source code is required")

    analysis = _analyse(source_code)
    checks = [_check(c, analysis) for c in constraints]
    summary = {s: sum(c["status"] == s for c in checks)
               for s in (SATISFIED, VIOLATED, INCONCLUSIVE)}
    return {"checks": checks,
            "requires_review": summary[VIOLATED] + summary[INCONCLUSIVE] > 0,
            "summary": summary,
            "marks_changed": False}
