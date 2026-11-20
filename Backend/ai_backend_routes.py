import requests
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt

ai_bp = Blueprint("ai", __name__)

AI_SERVICE_URL = "http://127.0.0.1:5001/analyze"


@ai_bp.route("/api/ai/analyze", methods=["POST"])
@jwt_required()
def analyze_problem():

    if get_jwt().get("role") != "teacher":
        return jsonify({
            "status": "error",
            "message": "Teacher access required"
        }), 403

    if not request.is_json:
        return jsonify({
            "status": "error",
            "message": "JSON request required"
        }), 415

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Valid JSON object required"
        }), 400

    fields = ["problem", "input_format", "output_format"]

    for field in fields:
        if not isinstance(data.get(field), str):
            return jsonify({
                "status": "error",
                "message": f"{field} must be a string"
            }), 400

        if len(data[field]) > 10000:
            return jsonify({
                "status": "error",
                "message": f"{field} exceeds allowed length"
            }), 400

    if not data["problem"].strip():
        return jsonify({
            "status": "error",
            "message": "Problem statement is required"
        }), 400

    try:
        response = requests.post(
            AI_SERVICE_URL,
            json={field: data[field] for field in fields},
            timeout=120
        )

        response.raise_for_status()

        result = response.json()

        return jsonify({
            "status": "success",
            "data": result
        }), 200

    except requests.Timeout:
        return jsonify({
            "status": "error",
            "message": "AI service timed out"
        }), 504

    except (requests.RequestException, ValueError):
        return jsonify({
            "status": "error",
            "message": "AI service unavailable or returned an invalid response"
        }), 502
