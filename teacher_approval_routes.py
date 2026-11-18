import requests
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from api_errors import error_response
from teacher_approval_service import (
    create_draft,
    apply_action,
    approve_category,
    finalize_draft,
    set_input_schema
)
from constraint_draft_db import save_draft, get_draft, get_draft_with_version, update_draft

approval_bp = Blueprint("approval", __name__)

AI_URL = "http://127.0.0.1:5001/analyze"
CONFLICT_MESSAGE = "Draft changed, finalized, or access denied"


def teacher_id_or_error():
    if get_jwt().get("role") != "teacher":
        return None
    return str(get_jwt_identity())


def _teacher_required():
    return error_response("Teacher access required", 403)


def _draft_error(exc):
    if str(exc) == CONFLICT_MESSAGE:
        return error_response(str(exc), 409)
    return error_response(str(exc), 400)


@approval_bp.post("/api/ai/drafts")
@jwt_required()
def new_draft():
    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Valid JSON required", 400)

    fields = ("problem", "input_format", "output_format")
    if any(not isinstance(data.get(f), str) for f in fields):
        return error_response("All fields must be strings", 400)
    if not data["problem"].strip():
        return error_response("Problem is required", 400)
    if any(len(data[f]) > 10000 for f in fields):
        return error_response("Input too long", 400)

    try:
        response = requests.post(
            AI_URL,
            json={f: data[f] for f in fields},
            timeout=120
        )
        response.raise_for_status()
        ai_result = response.json()
        draft = create_draft(ai_result)
        save_draft(teacher_id, draft)
        return jsonify({"status": "success", "draft": draft}), 201
    except requests.RequestException:
        return error_response("AI service unavailable", 502)
    except (ValueError, KeyError, TypeError):
        return error_response("Invalid AI response", 502)


@approval_bp.get("/api/ai/drafts/<draft_id>")
@jwt_required()
def read_draft(draft_id):
    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    draft = get_draft(draft_id, teacher_id)
    if draft is None:
        return error_response("Draft not found", 404)

    return jsonify({"status": "success", "draft": draft}), 200


@approval_bp.post("/api/ai/drafts/<draft_id>/actions")
@jwt_required()
def draft_action(draft_id):
    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Valid JSON required", 400)

    draft, version = get_draft_with_version(draft_id, teacher_id)
    if draft is None:
        return error_response("Draft not found", 404)

    try:
        updated = apply_action(
            draft,
            data.get("action", ""),
            data.get("category"),
            data.get("constraint_id"),
            data.get("text")
        )
        update_draft(teacher_id, updated, version)
        return jsonify({"status": "success", "draft": updated}), 200
    except (ValueError, AttributeError) as exc:
        return _draft_error(exc)


@approval_bp.post("/api/ai/drafts/<draft_id>/approve")
@jwt_required()
def draft_approve(draft_id):
    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Valid JSON required", 400)

    draft, version = get_draft_with_version(draft_id, teacher_id)
    if draft is None:
        return error_response("Draft not found", 404)

    try:
        updated = approve_category(draft, data.get("category"))
        update_draft(teacher_id, updated, version)
        return jsonify({"status": "success", "draft": updated}), 200
    except ValueError as exc:
        return _draft_error(exc)


@approval_bp.put("/api/ai/drafts/<draft_id>/input-schema")
@jwt_required()
def draft_input_schema(draft_id):
    """Set (object) or clear (null) the structured input schema of a draft.
    Allowed only before the value category is approved; it is then locked by
    approval and finalization like the value constraints themselves."""
    from input_schema import SchemaError

    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"input_schema"}:
        return error_response("Body must be {\"input_schema\": object or null}", 400,
                              "INVALID_REQUEST")

    draft, version = get_draft_with_version(draft_id, teacher_id)
    if draft is None:
        return error_response("Draft not found", 404)

    try:
        updated = set_input_schema(draft, data["input_schema"])
        update_draft(teacher_id, updated, version)
        return jsonify({"status": "success", "draft": updated}), 200
    except SchemaError as exc:
        return error_response(str(exc), 400 if exc.code == "INVALID_INPUT_SCHEMA" else 422,
                              exc.code)
    except ValueError as exc:
        return _draft_error(exc)


@approval_bp.post("/api/ai/drafts/<draft_id>/finalize")
@jwt_required()
def draft_finalize(draft_id):
    teacher_id = teacher_id_or_error()
    if teacher_id is None:
        return _teacher_required()

    draft, version = get_draft_with_version(draft_id, teacher_id)
    if draft is None:
        return error_response("Draft not found", 404)

    try:
        result = finalize_draft(draft)
        update_draft(teacher_id, result["draft"], version)
        return jsonify({
            "status": "success",
            "draft": result["draft"],
            "approved_constraints": result["approved_constraints"]
        }), 200
    except ValueError as exc:
        return _draft_error(exc)
