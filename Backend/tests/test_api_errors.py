"""
Uniform JSON error contract: {"status": "error", "message": str[, "code"]}.
No database, Ollama, AI service or compiler is reached (all patched).
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

import requests
from flask_jwt_extended import create_access_token

from tests import fakes  # noqa: F401

import app as app_module
import evaluation_routes
import teacher_approval_routes as drafts

flask_app = app_module.app


def auth(role="teacher", identity="BIIT235", **kwargs):
    with flask_app.app_context():
        token = create_access_token(identity=identity,
                                    additional_claims={"role": role}, **kwargs)
    return {"Authorization": f"Bearer {token}"}


class ErrorShapeMixin:
    def assertError(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertTrue(response.is_json, "error body must be JSON")
        body = response.get_json()
        self.assertEqual(body["status"], "error")
        self.assertIsInstance(body["message"], str)
        self.assertTrue(body["message"])
        if code:
            self.assertEqual(body["code"], code)
        for internal in ("msg", "error", "Traceback"):
            self.assertNotIn(internal, body)
        return body


class FrameworkErrorTests(ErrorShapeMixin, unittest.TestCase):

    def setUp(self):
        self.client = flask_app.test_client()

    def test_missing_token(self):
        self.assertError(self.client.get("/api/assessments/1/rubric"), 401,
                         "AUTH_REQUIRED")

    def test_expired_token(self):
        headers = auth(expires_delta=timedelta(seconds=-5))
        self.assertError(self.client.get("/api/grading/status", headers=headers),
                         401, "TOKEN_EXPIRED")

    def test_malformed_token(self):
        headers = {"Authorization": "Bearer not.a.jwt"}
        self.assertError(self.client.get("/api/grading/status", headers=headers),
                         422, "INVALID_TOKEN")

    def test_unknown_route_is_json_404(self):
        self.assertError(self.client.get("/api/does-not-exist"), 404, "NOT_FOUND")

    def test_wrong_method_is_json_405(self):
        self.assertError(self.client.delete("/api/teacher/login"), 405,
                         "METHOD_NOT_ALLOWED")

    def test_unhandled_exception_is_generic_500_without_details(self):
        old = flask_app.config.get("PROPAGATE_EXCEPTIONS")
        flask_app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            with patch.object(evaluation_routes, "_identity",
                              side_effect=RuntimeError("secret detail")):
                r = self.client.get("/api/grading/status", headers=auth())
        finally:
            flask_app.config["PROPAGATE_EXCEPTIONS"] = old
        body = self.assertError(r, 500, "INTERNAL_SERVER_ERROR")
        self.assertNotIn("secret detail", r.get_data(as_text=True))
        self.assertEqual(body["message"], "Internal server error")

    def test_status_codes_unchanged_for_existing_route_errors(self):
        r = self.client.get("/api/grading/status", headers=auth("student", "S1"))
        self.assertError(r, 403)


class DraftRouteErrorTests(ErrorShapeMixin, unittest.TestCase):
    """The AI draft routes now use the standard shape (were {"error": ...})."""

    def setUp(self):
        self.client = flask_app.test_client()
        self.teacher = auth()

    def test_student_forbidden(self):
        for method, url in (("post", "/api/ai/drafts"), ("get", "/api/ai/drafts/d1"),
                            ("post", "/api/ai/drafts/d1/actions"),
                            ("post", "/api/ai/drafts/d1/approve"),
                            ("post", "/api/ai/drafts/d1/finalize")):
            r = getattr(self.client, method)(url, headers=auth("student", "S1"),
                                             json={})
            self.assertError(r, 403)

    def test_invalid_body(self):
        self.assertError(self.client.post("/api/ai/drafts", headers=self.teacher,
                                          data="x", content_type="text/plain"), 400)
        self.assertError(self.client.post("/api/ai/drafts", headers=self.teacher,
                                          json={"problem": 1}), 400)
        self.assertError(self.client.post("/api/ai/drafts", headers=self.teacher,
                                          json={"problem": " ", "input_format": "",
                                                "output_format": ""}), 400)

    def test_ai_service_unavailable_saves_nothing(self):
        body = {"problem": "p", "input_format": "i", "output_format": "o"}
        with patch.object(drafts.requests, "post",
                          side_effect=requests.ConnectionError("down")), \
                patch.object(drafts, "save_draft") as save:
            self.assertError(self.client.post("/api/ai/drafts", headers=self.teacher,
                                              json=body), 502)
        save.assert_not_called()

    def test_not_found_and_conflict(self):
        with patch.object(drafts, "get_draft", return_value=None):
            self.assertError(self.client.get("/api/ai/drafts/d1",
                                             headers=self.teacher), 404)
        with patch.object(drafts, "get_draft_with_version", return_value=(None, None)):
            self.assertError(self.client.post("/api/ai/drafts/d1/finalize",
                                              headers=self.teacher), 404)
        with patch.object(drafts, "get_draft_with_version",
                          return_value=({"id": "d1"}, 1)), \
                patch.object(drafts, "apply_action",
                             side_effect=ValueError(drafts.CONFLICT_MESSAGE)):
            self.assertError(self.client.post("/api/ai/drafts/d1/actions",
                                              headers=self.teacher,
                                              json={"action": "ACCEPT"}), 409)
        with patch.object(drafts, "get_draft_with_version",
                          return_value=({"id": "d1"}, 1)), \
                patch.object(drafts, "approve_category",
                             side_effect=ValueError("Unknown category")):
            self.assertError(self.client.post("/api/ai/drafts/d1/approve",
                                              headers=self.teacher,
                                              json={"category": "x"}), 400)

    def test_success_bodies_carry_status(self):
        with patch.object(drafts, "get_draft", return_value={"id": "d1"}):
            r = self.client.get("/api/ai/drafts/d1", headers=self.teacher)
        self.assertEqual((r.status_code, r.get_json()["status"]), (200, "success"))


if __name__ == "__main__":
    unittest.main()
