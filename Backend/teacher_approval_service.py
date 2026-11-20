
import copy
import uuid

VALID_CATEGORIES = {"value", "code"}
VALID_ACTIONS = {"ACCEPT", "EDIT", "DELETE", "ADD"}


def _clean_warnings(items):
    """Keep only well-formed {code, message} warnings from the AI service."""
    cleaned = []
    for item in items if isinstance(items, list) else []:
        if (isinstance(item, dict) and isinstance(item.get("code"), str)
                and isinstance(item.get("message"), str)):
            cleaned.append({"code": item["code"][:64],
                            "message": item["message"][:500]})
    return cleaned


def create_draft(ai_result):

    constraints = []

    for item in ai_result.get("value_constraints", []):
        entry = {
            "id": str(uuid.uuid4()),
            "category": "value",
            "text": item.get("text", ""),
            "source": "AI",
            "review_status": "PENDING",
            "evidence_status": item.get("status", "UNVERIFIED")
        }
        item_warnings = _clean_warnings(item.get("warnings"))
        if item_warnings:
            entry["warnings"] = item_warnings
        constraints.append(entry)

    for item in ai_result.get("code_constraints", []):
        constraints.append({
            "id": str(uuid.uuid4()),
            "category": "code",
            "text": item.get("type", ""),
            "source": "AI",
            "review_status": "PENDING",
            "evidence_status": item.get(
                "status", "NEEDS_TEACHER_REVIEW"
            )
        })

    draft = {
        "draft_id": str(uuid.uuid4()),
        "constraints": constraints,
        "approval": {
            "value": False,
            "code": False
        },
        "finalized": False
    }

    # e.g. INPUT_TOO_LONG: the model was not run, so the teacher must add the
    # value constraints manually. Informational only; approval is unchanged.
    ai_warnings = _clean_warnings(ai_result.get("warnings"))
    if ai_warnings:
        draft["ai_warnings"] = ai_warnings

    return draft


def apply_action(draft, action, category, constraint_id=None, text=None):

    if draft["finalized"]:
        raise ValueError("Finalized draft cannot be modified")

    action = action.upper()

    if action not in VALID_ACTIONS:
        raise ValueError("Invalid action")

    if category not in VALID_CATEGORIES:
        raise ValueError("Invalid category")

    updated = copy.deepcopy(draft)

    if updated["approval"][category]:
        raise ValueError("Approved category cannot be modified")

    if action == "ADD":

        if not isinstance(text, str) or not text.strip():
            raise ValueError("Constraint text required")

        updated["constraints"].append({
            "id": str(uuid.uuid4()),
            "category": category,
            "text": text.strip(),
            "source": "TEACHER",
            "review_status": "ACCEPTED",
            "evidence_status": "TEACHER_PROVIDED"
        })

        return updated

    matches = [
        item for item in updated["constraints"]
        if item["id"] == constraint_id
        and item["category"] == category
    ]

    if not matches:
        raise ValueError("Constraint not found")

    item = matches[0]

    if action == "ACCEPT":
        item["review_status"] = "ACCEPTED"

    elif action == "EDIT":

        if not isinstance(text, str) or not text.strip():
            raise ValueError("Edited text required")

        item["text"] = text.strip()
        item["review_status"] = "ACCEPTED"
        item["source"] = "TEACHER_EDITED"

    elif action == "DELETE":
        item["review_status"] = "REJECTED"

    return updated


def approve_category(draft, category):

    if category not in VALID_CATEGORIES:
        raise ValueError("Invalid category")

    if draft["finalized"]:
        raise ValueError("Draft already finalized")

    updated = copy.deepcopy(draft)

    pending = [
        item for item in updated["constraints"]
        if item["category"] == category
        and item["review_status"] == "PENDING"
    ]

    if pending:
        raise ValueError(
            "Resolve pending constraints before approval"
        )

    updated["approval"][category] = True

    return updated


MAX_INPUT_SCHEMA_CHARS = 20000


def set_input_schema(draft, input_schema):
    """
    Attach (or with None, remove) a structured input schema to a draft.

    The schema is part of the VALUE approval: it can only change while the
    value category is unapproved and the draft is not finalized, so once the
    teacher approves and finalizes, the schema is locked together with the
    approved value constraints. Drafts without a schema behave exactly as
    before (legacy 1-3 integer format).
    """
    import json

    from input_schema import check_schema

    if draft["finalized"]:
        raise ValueError("Finalized draft cannot be modified")

    if draft["approval"]["value"]:
        raise ValueError("Approved category cannot be modified")

    updated = copy.deepcopy(draft)

    if input_schema is None:
        updated.pop("input_schema", None)
        return updated

    if len(json.dumps(input_schema)) > MAX_INPUT_SCHEMA_CHARS:
        raise ValueError("Input schema is too large")

    checked = check_schema(input_schema)          # raises SchemaError (a ValueError)
    if checked["legacy"]:
        raise ValueError("A stored input schema cannot use the legacy flag")
    updated["input_schema"] = {k: checked[k] for k in
                               ("version", "variables", "items", "relations")}
    return updated


MAX_REVIEWED_ITEMS = 50
MAX_REVIEWED_TEXT = 500


class ReviewConflict(ValueError):
    """The draft is already approved/finalized with different content."""


class ReviewRejected(ValueError):
    """The reviewed content cannot be approved as submitted (422)."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def _reviewed_list(values, category, allow_empty):
    if not isinstance(values, list):
        raise ValueError(f"{category}_constraints must be a list of strings")
    if len(values) > MAX_REVIEWED_ITEMS:
        raise ValueError(f"At most {MAX_REVIEWED_ITEMS} {category} constraints")
    cleaned, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{category} constraints must be non-empty strings")
        text = value.strip()
        if len(text) > MAX_REVIEWED_TEXT:
            raise ValueError(f"A {category} constraint is longer than "
                             f"{MAX_REVIEWED_TEXT} characters")
        key = " ".join(text.split()).casefold()
        if key in seen:
            raise ValueError(f"Duplicate {category} constraint: {text}")
        seen.add(key)
        cleaned.append(text)
    if not cleaned and not allow_empty:
        raise ValueError(f"At least one {category} constraint is required")
    return cleaned


def _accepted(draft, category):
    return [item["text"].strip() for item in draft["constraints"]
            if item["category"] == category and item["review_status"] == "ACCEPTED"]


def _same_texts(a, b):
    norm = lambda items: sorted(" ".join(t.split()).casefold() for t in items)  # noqa: E731
    return norm(a) == norm(b)


def _checked_schema(input_schema):
    if input_schema is None:
        return None
    from input_schema import check_schema
    checked = check_schema(input_schema)          # SchemaError on problems
    return {k: checked[k] for k in ("version", "variables", "items", "relations")}


def schema_constraint_mismatches(value_constraints, input_schema):
    """
    Explicit bounds in the reviewed value constraints that contradict the
    structured schema (same variable / array name, different range).
    Bounds for names the schema does not declare are not judged.
    """
    from numeric_constraint_validator import extract_explicit_bounds

    ranges = {name: (spec.get("min"), spec.get("max"))
              for name, spec in (input_schema.get("variables") or {}).items()}
    for item in input_schema.get("items") or []:
        if item.get("type") == "array" and item.get("name"):
            ranges[item["name"]] = (item.get("min"), item.get("max"))

    mismatches = []
    for bound in extract_explicit_bounds("\n".join(value_constraints)):
        low_text, variable, high_text = [p.strip() for p in bound["text"].split("<=")]
        name = variable.split("[", 1)[0]
        if name not in ranges:
            continue
        try:
            low, high = int(low_text), int(high_text)
        except ValueError:
            continue                                  # powers: not compared
        if (low, high) != tuple(ranges[name]):
            mismatches.append({"constraint": bound["text"], "schema_name": name,
                               "schema_range": list(ranges[name])})
    return mismatches


def approve_reviewed_constraints(draft, value_constraints, code_constraints,
                                 input_schema):
    """
    One explicit teacher action ("Generate Test Cases"): make the draft hold
    EXACTLY the reviewed value and code constraints and schema that the
    teacher saw, approve both categories and finalize.

    Built only from the existing operations (apply_action, set_input_schema,
    approve_category, finalize_draft), applied to a copy; the caller writes
    the result once with optimistic concurrency.

    Returns (result, replay):
      replay=False  -> result = finalize_draft(...) output, must be saved
      replay=True   -> draft was already finalized with identical content;
                       nothing to save (idempotent retry).
    Raises ReviewConflict when an approved/finalized category differs, and
    ReviewRejected for content that cannot be generated from.
    """
    values = _reviewed_list(value_constraints, "value", allow_empty=False)
    codes = _reviewed_list(code_constraints, "code", allow_empty=True)
    schema = _checked_schema(input_schema)

    if schema is not None:
        mismatches = schema_constraint_mismatches(values, schema)
        if mismatches:
            raise ReviewRejected(
                "SCHEMA_CONSTRAINT_MISMATCH",
                "Value constraints contradict the input schema", mismatches)
    else:
        from generic_test_inputs import parse_approved_value_constraints
        try:
            parse_approved_value_constraints("\n".join(values))
        except ValueError as error:
            raise ReviewRejected(
                "INPUT_SCHEMA_REQUIRED",
                "These value constraints need a reviewed structured input "
                "schema before test cases can be generated", [str(error)])

    def stored_schema(d):
        return _checked_schema(d.get("input_schema"))

    if draft["finalized"]:
        if (_same_texts(_accepted(draft, "value"), values)
                and _same_texts(_accepted(draft, "code"), codes)
                and stored_schema(draft) == schema):
            return {"draft": draft,
                    "approved_constraints": [i for i in draft["constraints"]
                                             if i["review_status"] == "ACCEPTED"]}, True
        raise ReviewConflict("Draft is already finalized with different "
                             "constraints or input schema")

    updated = copy.deepcopy(draft)

    for category, wanted in (("value", values), ("code", codes)):
        if updated["approval"][category]:
            if not _same_texts(_accepted(updated, category), wanted):
                raise ReviewConflict(f"The {category} constraints are already "
                                     "approved with different content")
            continue

        remaining = list(wanted)
        for item in [i for i in updated["constraints"] if i["category"] == category]:
            text = item["text"].strip()
            match = next((w for w in remaining
                          if " ".join(w.split()).casefold() == " ".join(text.split()).casefold()),
                         None)
            if match is not None:
                remaining.remove(match)
                action = "ACCEPT" if match == text else "EDIT"
                updated = apply_action(updated, action, category, item["id"], match)
            elif item["review_status"] != "REJECTED":
                updated = apply_action(updated, "DELETE", category, item["id"])
        for text in remaining:
            updated = apply_action(updated, "ADD", category, None, text)

    if updated["approval"]["value"]:
        if stored_schema(updated) != schema:
            raise ReviewConflict("The input schema is locked by an earlier "
                                 "value approval and differs")
    else:
        updated = set_input_schema(updated, schema)
        updated = approve_category(updated, "value")

    if not updated["approval"]["code"]:
        updated = approve_category(updated, "code")

    return finalize_draft(updated), False


def finalize_draft(draft):

    if draft["finalized"]:
        raise ValueError("Draft already finalized")

    if not all(draft["approval"].values()):
        raise ValueError(
            "Both value and code categories must be approved"
        )

    updated = copy.deepcopy(draft)
    updated["finalized"] = True

    approved_constraints = [
        item for item in updated["constraints"]
        if item["review_status"] == "ACCEPTED"
    ]

    return {
        "draft": updated,
        "approved_constraints": approved_constraints
    }
