import json
import tempfile
import unittest
from pathlib import Path

from app import _DatabaseAuthService, create_app
from backend.storage import SqliteRepository


class VercelAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "app.db"
        self.repository_factory = lambda: SqliteRepository(self.db_path)
        self.auth_factory = lambda repository: _DatabaseAuthService("test-admin-password",
                                                                    repository)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_api_are_served(self):
        client = create_app(self.repository_factory, self.auth_factory).test_client()

        home = client.get("/")
        booking_page = client.get("/book")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(booking_page.status_code, 200)
        home.close()
        booking_page.close()
        response = client.get("/api/vehicles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()), 9)

    def test_admin_session_survives_a_new_app_instance_and_logout_revokes_it(self):
        first_client = create_app(self.repository_factory, self.auth_factory).test_client()
        login = first_client.post("/api/admin/login",
                                  data=json.dumps({"password": "test-admin-password"}),
                                  content_type="application/json")
        self.assertEqual(login.status_code, 200)
        token = login.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]

        second_client = create_app(self.repository_factory, self.auth_factory).test_client()
        second_client.set_cookie("admin_session", token)
        self.assertTrue(second_client.get("/api/admin/session").get_json()["authenticated"])
        self.assertEqual(second_client.get("/api/admin/stats").status_code, 200)

        self.assertEqual(second_client.post("/api/admin/logout").status_code, 200)
        self.assertFalse(second_client.get("/api/admin/session").get_json()["authenticated"])

    def test_invalid_json_returns_client_error(self):
        client = create_app(self.repository_factory, self.auth_factory).test_client()
        response = client.post("/api/bookings", data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid JSON", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
