
import copy
import uuid

VALID_CATEGORIES = {"value", "code"}
VALID_ACTIONS = {"ACCEPT", "EDIT", "DELETE", "ADD"}


def create_draft(ai_result):

    constraints = []

    for item in ai_result.get("value_constraints", []):
        constraints.append({
            "id": str(uuid.uuid4()),
            "category": "value",
            "text": item.get("text", ""),
            "source": "AI",
            "review_status": "PENDING",
            "evidence_status": item.get("status", "UNVERIFIED")
        })

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

    return {
        "draft_id": str(uuid.uuid4()),
        "constraints": constraints,
        "approval": {
            "value": False,
            "code": False
        },
        "finalized": False
    }


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
