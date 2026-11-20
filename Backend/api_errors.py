"""
Uniform JSON error responses for the whole API.

Every error body has the same shape:

    {"status": "error", "message": "<human-readable>", "code": "<MACHINE_CODE>"}

`code` is optional on route-specific errors (existing routes return
status + message) and always present on the framework-level errors
registered here. Extra keys (e.g. `errors` for section assignment) may be
added by individual routes. HTTP status codes are unchanged from the
previous behaviour.
"""

import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


def error_response(message, status_code, code=None, **extra):
    body = {"status": "error", "message": message}
    if code:
        body["code"] = code
    body.update(extra)
    return jsonify(body), status_code


def _code_for(exc):
    return (exc.name or "ERROR").upper().replace(" ", "_").replace("'", "")


def register_error_handlers(app, jwt):
    """JWT failures and HTTP errors (404, 405, 413, 500 ...) as JSON."""

    @jwt.unauthorized_loader
    def _missing_token(reason):
        return error_response("Authorization token required", 401,
                              "AUTH_REQUIRED")

    @jwt.expired_token_loader
    def _expired_token(jwt_header, jwt_payload):
        return error_response("Token has expired; please log in again", 401,
                              "TOKEN_EXPIRED")

    @jwt.invalid_token_loader
    def _invalid_token(reason):
        # flask-jwt-extended's default status for a malformed token.
        return error_response("Invalid authorization token", 422,
                              "INVALID_TOKEN")

    @jwt.revoked_token_loader
    def _revoked_token(jwt_header, jwt_payload):
        return error_response("Token has been revoked", 401, "TOKEN_REVOKED")

    @jwt.needs_fresh_token_loader
    def _needs_fresh(jwt_header, jwt_payload):
        return error_response("Fresh login required", 401, "FRESH_TOKEN_REQUIRED")

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        # Includes the 500 Flask raises for an unhandled exception when
        # exceptions are not propagated (production). Details stay in the
        # server log, never in the response.
        if exc.code == 500:
            logger.error("Unhandled server error", exc_info=exc.original_exception
                         if hasattr(exc, "original_exception") else None)
            return error_response("Internal server error", 500,
                                  "INTERNAL_SERVER_ERROR")
        return error_response(exc.name, exc.code, _code_for(exc))
