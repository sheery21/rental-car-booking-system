"""Unit tests – run with:  python -m unittest discover tests"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from backend.exceptions import (AuthError, NotFoundError, RentalError, TooManyAttemptsError,
                                VehicleNotAvailableError)
from backend.models import (SUV, CardPayment, CashPayment, EconomyCar, ElectricCar,
                            LuxuryCar, Vehicle, VehicleFactory, WalletPayment)
from backend.services import AuthService, RentalService
from backend.storage import InMemoryRepository, SqliteRepository, import_legacy_json

TODAY = date(2026, 1, 10)


def make_service(repo=None) -> RentalService:
    return RentalService(repo or InMemoryRepository(), today=lambda: TODAY)


CUSTOMER = {"name": "Ali Khan", "email": "ali@example.com", "phone": "0300-1234567",
            "license_no": "LHR-12345"}


def book(svc, vehicle_id="V-101", start="2026-01-12", end="2026-01-15", method="cash", **kw):
    return svc.create_booking(**{**CUSTOMER, **kw}, vehicle_id=vehicle_id, start_date=start,
                              end_date=end, payment_method=method)


class VehicleTests(unittest.TestCase):
    def test_vehicle_is_abstract(self):
        with self.assertRaises(TypeError):
            Vehicle("X", "A", "B", 2020, 1000, 4)

    def test_polymorphic_rent(self):
        args = ("V", "Make", "Model", 2023, 10000, 5)
        self.assertEqual(EconomyCar(*args).calculate_rent(2), 20000)
        self.assertEqual(EconomyCar(*args).calculate_rent(7), 63000)  # 10% off
        self.assertEqual(SUV(*args).calculate_rent(2), 23000)         # + 3000
        self.assertEqual(LuxuryCar(*args).calculate_rent(2), 25000)   # + 2500/day
        self.assertEqual(ElectricCar(*args).calculate_rent(2), 19000)  # 5% off

    def test_validation(self):
        car = EconomyCar("V", "M", "M", 2023, 5000, 4)
        with self.assertRaises(ValueError):
            car.daily_rate = 0
        with self.assertRaises(ValueError):
            EconomyCar("V", "", "M", 2023, 5000, 4)
        with self.assertRaises(ValueError):
            EconomyCar("V", "M", "M", 2023, 5000, 40)

    def test_factory_round_trip(self):
        car = LuxuryCar("V-9", "Audi", "A6", 2021, 32000, 5, chauffeur=True, active=False)
        clone = VehicleFactory.create(car.to_dict())
        self.assertIsInstance(clone, LuxuryCar)
        self.assertEqual(clone.to_dict(), car.to_dict())


class PaymentTests(unittest.TestCase):
    def test_fees(self):
        self.assertEqual(CashPayment(10000).total, 10000)
        self.assertEqual(CardPayment(10000).total, 10200)
        self.assertEqual(WalletPayment(10000).total, 10100)


class BookingFlowTests(unittest.TestCase):
    def test_seeded_fleet(self):
        self.assertEqual(len(make_service().list_vehicles()), 9)

    def test_booking_flow_and_overlap(self):
        svc = make_service()
        booking = book(svc, method="card")
        self.assertEqual(booking["days"], 3)
        self.assertEqual(booking["status"], "confirmed")
        with self.assertRaises(VehicleNotAvailableError):
            book(svc, start="2026-01-14", end="2026-01-16")
        book(svc, start="2026-01-15", end="2026-01-16")  # back-to-back is fine
        svc.cancel_booking(booking["booking_id"])
        book(svc, start="2026-01-12", end="2026-01-14")
        self.assertEqual(svc.stats()["customers"], 1)  # same email reused

    def test_invalid_input(self):
        svc = make_service()
        with self.assertRaises(RentalError):
            svc.quote("V-101", "2026-01-05", "2026-01-06")  # in the past
        with self.assertRaises(RentalError):
            svc.quote("V-101", "2026-01-12", "2026-01-12")  # zero days
        with self.assertRaises(NotFoundError):
            svc.quote("NOPE", "2026-01-12", "2026-01-13")
        with self.assertRaises(RentalError):
            book(svc, name="A", email="bad", license_no="x")
        self.assertEqual(svc.stats()["customers"], 0)  # nothing half-saved

    def test_cannot_cancel_twice(self):
        svc = make_service()
        b = book(svc, vehicle_id="V-201", end="2026-01-13")
        svc.complete_booking(b["booking_id"])
        with self.assertRaises(RentalError):
            svc.cancel_booking(b["booking_id"])

    def test_customer_lookup_and_cancel(self):
        svc = make_service()
        b = book(svc)
        second = book(svc, vehicle_id="V-103")
        book(svc, vehicle_id="V-201", email="other@example.com")
        with self.assertRaises(NotFoundError):
            svc.find_bookings("someone@else.com", b["booking_id"])
        with self.assertRaises(RentalError):
            svc.find_bookings("")
        # email only -> all of this customer's bookings, newest first
        self.assertEqual([x["booking_id"] for x in svc.find_bookings("ALI@example.com")],
                         [second["booking_id"], b["booking_id"]])
        # email + id -> just that one
        found = svc.find_bookings("ali@example.com", b["booking_id"].lower())
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["can_cancel"])
        self.assertEqual(svc.cancel_by_customer(b["booking_id"], CUSTOMER["email"])["status"],
                         "cancelled")
        # a booking that starts today can no longer be cancelled online
        today_booking = book(svc, vehicle_id="V-102", start="2026-01-10", end="2026-01-11")
        with self.assertRaises(RentalError):
            svc.cancel_by_customer(today_booking["booking_id"], CUSTOMER["email"])


class AdminFleetTests(unittest.TestCase):
    def test_add_vehicle(self):
        svc = make_service()
        car = svc.add_vehicle({"type": "ElectricCar", "make": "Tesla", "model": "Model 3",
                               "year": "2024", "daily_rate": "20000", "seats": "5",
                               "range_km": "500"})
        self.assertEqual(car["vehicle_id"], "V-403")
        self.assertIn("500 km range", car["features"])
        with self.assertRaises(RentalError):
            svc.add_vehicle({"type": "Boat", "make": "X", "model": "Y", "year": 2024,
                             "daily_rate": 1, "seats": 4})
        with self.assertRaises(RentalError):
            svc.add_vehicle({"type": "SUV", "make": "X", "model": "Y", "year": "abc",
                             "daily_rate": 1, "seats": 4})

    def test_retire_and_rate_change(self):
        svc = make_service()
        svc.update_vehicle("V-101", daily_rate="5000", active=False)
        self.assertNotIn("V-101", [v["vehicle_id"] for v in svc.list_vehicles()])
        self.assertIn("V-101", [v["vehicle_id"] for v in svc.list_all_vehicles()])
        with self.assertRaises(NotFoundError):
            book(svc)
        with self.assertRaises(RentalError):
            svc.update_vehicle("V-102", daily_rate="-5")
        self.assertEqual(svc.list_vehicles()[0]["daily_rate"], 7000)  # V-102 unchanged


class AuthTests(unittest.TestCase):
    def test_login_logout(self):
        auth = AuthService("secret123")
        with self.assertRaises(AuthError):
            auth.login("wrong")
        token = auth.login("secret123")
        self.assertTrue(auth.is_valid(token))
        auth.logout(token)
        self.assertFalse(auth.is_valid(token))
        with self.assertRaises(AuthError):
            auth.require(None)

    def test_lockout_and_expiry(self):
        now = [1000.0]
        auth = AuthService("secret123", clock=lambda: now[0])
        for _ in range(AuthService.MAX_ATTEMPTS):
            with self.assertRaises(AuthError):
                auth.login("nope", "1.2.3.4")
        with self.assertRaises(TooManyAttemptsError):
            auth.login("secret123", "1.2.3.4")  # even the right password is blocked
        auth.login("secret123", "5.6.7.8")      # other clients are not affected
        now[0] += AuthService.LOCK_SECONDS + 1
        token = auth.login("secret123", "1.2.3.4")
        now[0] += AuthService.SESSION_HOURS * 3600 + 1
        self.assertFalse(auth.is_valid(token))


class SqliteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_survives_restart(self):
        svc = make_service(SqliteRepository(self.db))
        b = book(svc, vehicle_id="V-401", end="2026-01-14", method="wallet")
        svc.update_vehicle("V-402", active=False)
        car = svc.add_vehicle({"type": "SUV", "make": "Honda", "model": "BR-V", "year": 2023,
                               "daily_rate": 11000, "seats": 7, "four_wheel_drive": True})

        reloaded = make_service(SqliteRepository(self.db))
        self.assertEqual(reloaded.list_bookings()[0], b)
        self.assertFalse(next(v for v in reloaded.list_all_vehicles()
                              if v["vehicle_id"] == "V-402")["active"])
        self.assertIn(car["vehicle_id"], [v["vehicle_id"] for v in reloaded.list_vehicles()])
        nxt = book(reloaded, vehicle_id="V-101")
        self.assertNotEqual(nxt["booking_id"], b["booking_id"])
        self.assertEqual(reloaded.stats()["customers"], 1)

    def test_import_legacy_json(self):
        legacy = make_service()
        b = book(legacy)
        state = legacy._repo.load()
        state["bookings"] = [b]  # old JSON stored Booking.to_dict() (nested payment)
        state["customers"] = [c for c in legacy.list_customers()]
        path = Path(self.tmp.name) / "rental_data.json"
        path.write_text(json.dumps(state), encoding="utf-8")

        repo = SqliteRepository(self.db)
        self.assertTrue(import_legacy_json(path, repo))
        self.assertFalse(import_legacy_json(path, repo))  # only once
        self.assertEqual(make_service(repo).list_bookings()[0]["booking_id"], b["booking_id"])


if __name__ == "__main__":
    unittest.main()
