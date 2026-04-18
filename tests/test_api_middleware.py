"""API middleware tests for auth and CORS behavior."""

from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


class ApiMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["AIOPS_API_TOKEN"] = "test-token"
        os.environ["AIOPS_DATABASE_URL"] = f"sqlite+aiosqlite:///{self.tmpdir.name}/aiops.db"
        get_settings.cache_clear()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        self.tmpdir.cleanup()
        get_settings.cache_clear()

    def test_health_is_public(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_protected_route_requires_token(self) -> None:
        response = self.client.get("/v1/tasks")

        self.assertEqual(response.status_code, 401)

    def test_cors_allows_local_network_origin_with_port(self) -> None:
        origin = "http://192.168.3.155:3001"
        response = self.client.options(
            "/v1/tasks",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)


if __name__ == "__main__":
    unittest.main()
