
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
