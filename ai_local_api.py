import os

# Must be set BEFORE importing ai_constraint_service, which reads it at
# import time to build MODEL_PATH (previously set too late in __main__,
# so the service looked for the model on an old Google Colab path).
os.environ.setdefault(
    "FYP_MODEL_BASE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
)

from flask import Flask, request, jsonify
from ai_constraint_service import analyze_cpp_problem

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({
        "status": "running",
        "service": "FYP Local AI",
        "model": "flan_t5_constraint_final"
    }), 200


@app.post("/analyze")
def analyze():
    # Only accept JSON requests
    if not request.is_json:
        return jsonify({
            "error": "JSON request required"
        }), 415

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid JSON object"
        }), 400

    problem = data.get("problem")
    input_format = data.get("input_format")
    output_format = data.get("output_format")

    fields = [problem, input_format, output_format]

    if not all(isinstance(x, str) for x in fields):
        return jsonify({
            "error": "All fields must be strings"
        }), 400

    if not problem.strip():
        return jsonify({
            "error": "Problem statement is required"
        }), 400

    if any(len(x) > 10000 for x in fields):
        return jsonify({
            "error": "Input exceeds allowed length"
        }), 400

    try:
        result = analyze_cpp_problem(
            problem=problem,
            input_format=input_format,
            output_format=output_format
        )
        return jsonify(result), 200

    except Exception:
        app.logger.exception("AI analysis failed")
        return jsonify({
            "error": "AI analysis failed"
        }), 500


if __name__ == "__main__":
    os.environ.setdefault(
        "FYP_MODEL_BASE_PATH",
        os.path.join(os.path.dirname(__file__), "models")
    )

    app.run(
        host="127.0.0.1",
        port=5001,
        debug=False,
        use_reloader=False
    )
