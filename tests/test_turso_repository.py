import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend.services import RentalService
from backend.storage import TursoRepository


class TursoRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "turso-mock.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _connect(self, _url, *, auth_token=None):
        self.assertEqual(auth_token, "test-token")
        return sqlite3.connect(self.db_path)

    def test_database_api_usage_persists_bookings_and_admin_sessions(self):
        with patch("turso_serverless.connect", side_effect=self._connect):
            repository = TursoRepository("libsql://test.turso.io", "test-token")
            service = RentalService(repository, today=lambda: date(2026, 11, 20))
            booking = service.create_booking(
                name="Ali Khan",
                email="ali@example.com",
                phone="0300-1234567",
                license_no="LHR-12345",
                vehicle_id="V-101",
                start_date="2026-12-01",
                end_date="2026-12-03",
            )

            repository.store_admin_session("session-secret", 2_000_000_000)
            restarted = TursoRepository("libsql://test.turso.io", "test-token")
            reloaded = RentalService(restarted, today=lambda: date(2026, 11, 20))

            self.assertEqual(reloaded.list_bookings()[0]["booking_id"], booking["booking_id"])
            self.assertTrue(restarted.is_admin_session_valid("session-secret", 1_900_000_000))
            restarted.delete_admin_session("session-secret")
            self.assertFalse(restarted.is_admin_session_valid("session-secret", 1_900_000_000))


if __name__ == "__main__":
    unittest.main()
