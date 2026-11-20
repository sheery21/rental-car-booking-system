"""RentalService – the business-logic layer.

It *owns* the fleet, customers and bookings (Composition) and talks to an
abstract `Repository` that is injected from outside (Dependency Injection).
Objects live in memory; every change is written to the database first, and
only then applied in memory, so the two never drift apart.
"""

import copy
import re
import threading
from datetime import date
from typing import Callable

from ..exceptions import NotFoundError, RentalError, VehicleNotAvailableError
from ..models import (Booking, BookingStatus, Customer, PaymentFactory, Vehicle,
                      VehicleFactory)
from ..storage import Repository
from .seed_data import DEFAULT_FLEET

MAX_RENTAL_DAYS = 60


class RentalService:
    """Facade over the whole rental domain."""

    def __init__(self, repository: Repository,
                 today: Callable[[], date] = date.today):
        self._repo = repository
        self._today = today
        self._lock = threading.RLock()  # the HTTP server is multi-threaded
        self._vehicles: dict[str, Vehicle] = {}
        self._customers: dict[str, Customer] = {}
        self._bookings: dict[str, Booking] = {}
        self._counters = {"customer": 0, "booking": 1000}
        self._load()

    # ================= persistence =================
    def _load(self) -> None:
        state = self._repo.load()
        if not state["vehicles"]:  # first run – fill the fleet
            self._repo.save(vehicles=copy.deepcopy(DEFAULT_FLEET))
            state = self._repo.load()

        for raw in state["vehicles"]:
            vehicle = VehicleFactory.create(raw)
            self._vehicles[vehicle.vehicle_id] = vehicle

        for raw in state["customers"]:
            customer = Customer.from_dict(raw)
            self._customers[customer.customer_id] = customer

        for raw in state["bookings"]:
            booking = Booking(
                raw["booking_id"],
                self._customers[raw["customer_id"]],
                self._vehicles[raw["vehicle_id"]],
                date.fromisoformat(raw["start_date"]),
                date.fromisoformat(raw["end_date"]),
                PaymentFactory.create(raw["payment_method"], raw["rent"]),
                BookingStatus(raw["status"]),
                raw.get("created_at"),
            )
            self._bookings[booking.booking_id] = booking

        self._counters.update(state["counters"])

    # ================= helpers =================
    def _parse_range(self, start: str, end: str) -> tuple[date, date]:
        try:
            start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
        except (TypeError, ValueError):
            raise RentalError("Dates must be in YYYY-MM-DD format.") from None
        if start_d < self._today():
            raise RentalError("Pick-up date cannot be in the past.")
        if end_d <= start_d:
            raise RentalError("Return date must be after the pick-up date.")
        if (end_d - start_d).days > MAX_RENTAL_DAYS:
            raise RentalError(f"Maximum rental period is {MAX_RENTAL_DAYS} days.")
        return start_d, end_d

    def _get_vehicle(self, vehicle_id: str, for_rent: bool = False) -> Vehicle:
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None or (for_rent and not vehicle.active):
            raise NotFoundError(f"Vehicle {vehicle_id} not found.")
        return vehicle

    def _get_booking(self, booking_id: str) -> Booking:
        booking = self._bookings.get((booking_id or "").strip().upper())
        if booking is None:
            raise NotFoundError(f"Booking {booking_id} not found.")
        return booking

    def is_available(self, vehicle: Vehicle, start: date, end: date) -> bool:
        return not any(b.vehicle is vehicle and b.overlaps(start, end)
                       for b in self._bookings.values())

    def _out_today(self, vehicle: Vehicle) -> bool:
        today = self._today()
        return any(b.vehicle is vehicle and b.is_ongoing(today) for b in self._bookings.values())

    # ================= public queries =================
    def categories(self) -> list[str]:
        return sorted({v.category for v in self._vehicles.values() if v.active})

    def list_vehicles(self, start: str | None = None, end: str | None = None,
                      category: str | None = None) -> list[dict]:
        """Fleet for customers – only active cars, with availability."""
        with self._lock:
            date_range = self._parse_range(start, end) if start and end else None
            result = []
            for vehicle in self._vehicles.values():
                if not vehicle.active:
                    continue
                if category and vehicle.category.lower() != category.lower():
                    continue
                data = vehicle.to_dict()
                data["available"] = (self.is_available(vehicle, *date_range) if date_range
                                     else not self._out_today(vehicle))
                result.append(data)
            return result

    def quote(self, vehicle_id: str, start: str, end: str,
              payment_method: str = "cash") -> dict:
        with self._lock:
            vehicle = self._get_vehicle(vehicle_id, for_rent=True)
            start_d, end_d = self._parse_range(start, end)
            days = (end_d - start_d).days
            try:
                payment = PaymentFactory.create(payment_method, vehicle.calculate_rent(days))
            except ValueError as exc:
                raise RentalError(str(exc)) from None
            return {
                "vehicle_id": vehicle_id,
                "days": days,
                "daily_rate": vehicle.daily_rate,
                "base": round(vehicle.daily_rate * days, 2),
                "rent": payment.amount,
                "fee": payment.fee(),
                "total": payment.total,
                "available": self.is_available(vehicle, start_d, end_d),
            }

    def _customer_view(self, booking: Booking) -> dict:
        data = booking.to_dict()
        data["can_cancel"] = booking.is_active and booking.start_date > self._today()
        return data

    def find_bookings(self, email: str, booking_id: str = "") -> list[dict]:
        """Customer looks up their bookings by email; the booking ID is optional."""
        if not (email or "").strip():
            raise RentalError("Please enter the email you booked with.")
        wanted = (booking_id or "").strip().upper()
        with self._lock:
            mine = [b for b in self._bookings.values() if b.belongs_to(email)
                    and (not wanted or b.booking_id == wanted)]
            if not mine:
                raise NotFoundError("No booking found with this ID and email." if wanted
                                    else "No bookings found for this email.")
            mine.sort(key=lambda b: b.booking_id, reverse=True)
            return [self._customer_view(b) for b in mine]

    # ================= customer commands =================
    def _customer_for(self, name, email, phone, license_no) -> tuple[Customer, bool]:
        """Existing customer for this email, or a new (not yet saved) one."""
        email_key = (email or "").strip().lower()
        for customer in self._customers.values():
            if customer.email == email_key:
                return customer, False
        new_id = f"C-{self._counters['customer'] + 1:04d}"
        try:
            return Customer(new_id, name, email, phone, license_no), True
        except ValueError as exc:
            raise RentalError(str(exc)) from None

    def create_booking(self, *, name: str, email: str, phone: str, license_no: str,
                       vehicle_id: str, start_date: str, end_date: str,
                       payment_method: str = "cash") -> dict:
        with self._lock:
            vehicle = self._get_vehicle(vehicle_id, for_rent=True)
            start_d, end_d = self._parse_range(start_date, end_date)
            if not self.is_available(vehicle, start_d, end_d):
                raise VehicleNotAvailableError(
                    f"{vehicle.name} is already booked for these dates.")
            if payment_method not in PaymentFactory.methods():
                raise RentalError(f"Unsupported payment method: {payment_method}")

            customer, is_new = self._customer_for(name, email, phone, license_no)
            rent = vehicle.calculate_rent((end_d - start_d).days)  # polymorphic call
            payment = PaymentFactory.create(payment_method, rent)
            counters = dict(self._counters, booking=self._counters["booking"] + 1)
            if is_new:
                counters["customer"] += 1
            booking = Booking(f"BK-{counters['booking']}", customer, vehicle,
                              start_d, end_d, payment)

            # database first (one transaction), memory second
            self._repo.save(customers=[customer.to_dict()] if is_new else [],
                            bookings=[booking.to_dict()], counters=counters)
            self._counters = counters
            if is_new:
                self._customers[customer.customer_id] = customer
            self._bookings[booking.booking_id] = booking
            return booking.to_dict()

    def cancel_by_customer(self, booking_id: str, email: str) -> dict:
        with self._lock:
            info = self.find_bookings(email, booking_id)[0]
            if not info["can_cancel"]:
                raise RentalError("This booking can no longer be cancelled online. "
                                  "Please contact us.")
            return self._change_status(info["booking_id"], BookingStatus.CANCELLED)

    # ================= admin queries =================
    def list_bookings(self, status: str | None = None) -> list[dict]:
        with self._lock:
            bookings = [b for b in self._bookings.values()
                        if not status or b.status.value == status]
            bookings.sort(key=lambda b: b.booking_id, reverse=True)
            return [b.to_dict() for b in bookings]

    def list_all_vehicles(self) -> list[dict]:
        """Fleet for the admin – includes retired cars and booking counts."""
        with self._lock:
            result = []
            for vehicle in self._vehicles.values():
                mine = [b for b in self._bookings.values() if b.vehicle is vehicle]
                data = vehicle.to_dict()
                data["bookings"] = len(mine)
                data["upcoming"] = sum(1 for b in mine if b.is_active)
                data["out_today"] = self._out_today(vehicle)
                data["revenue"] = round(sum(b.payment.total for b in mine
                                            if b.status != BookingStatus.CANCELLED), 2)
                result.append(data)
            return result

    def list_customers(self) -> list[dict]:
        with self._lock:
            result = []
            for customer in self._customers.values():
                mine = [b for b in self._bookings.values() if b.customer is customer]
                data = customer.to_dict()
                data["bookings"] = len(mine)
                data["spent"] = round(sum(b.payment.total for b in mine
                                          if b.status != BookingStatus.CANCELLED), 2)
                data["last_booking"] = max((b.booking_id for b in mine), default=None)
                result.append(data)
            result.sort(key=lambda c: c["customer_id"], reverse=True)
            return result

    def stats(self) -> dict:
        with self._lock:
            bookings = list(self._bookings.values())
            paid = [b for b in bookings if b.status != BookingStatus.CANCELLED]
            by_category: dict[str, float] = {}
            for b in paid:
                by_category[b.vehicle.category] = by_category.get(b.vehicle.category, 0) + b.payment.total
            active_fleet = [v for v in self._vehicles.values() if v.active]
            return {
                "total_vehicles": len(active_fleet),
                "retired_vehicles": len(self._vehicles) - len(active_fleet),
                "available_now": sum(1 for v in active_fleet if not self._out_today(v)),
                "active_bookings": sum(1 for b in bookings if b.is_active),
                "total_bookings": len(bookings),
                "by_status": {s.value: sum(1 for b in bookings if b.status == s)
                              for s in BookingStatus},
                "customers": len(self._customers),
                "revenue": round(sum(b.payment.total for b in paid), 2),
                "revenue_by_category": {k: round(v, 2) for k, v in sorted(by_category.items())},
            }

    # ================= admin commands =================
    def cancel_booking(self, booking_id: str) -> dict:
        return self._change_status(booking_id, BookingStatus.CANCELLED)

    def complete_booking(self, booking_id: str) -> dict:
        return self._change_status(booking_id, BookingStatus.COMPLETED)

    def _change_status(self, booking_id: str, new_status: BookingStatus) -> dict:
        with self._lock:
            booking = self._get_booking(booking_id)
            if not booking.is_active:
                raise RentalError(f"Booking is already {booking.status.value}.")
            row = booking.to_dict()
            row["status"] = new_status.value
            self._repo.save(bookings=[row])  # database first ...
            if new_status is BookingStatus.CANCELLED:  # ... then the object
                booking.cancel()
            else:
                booking.complete()
            return booking.to_dict()

    def add_vehicle(self, data: dict) -> dict:
        with self._lock:
            numbers = [int(m.group()) for v in self._vehicles
                       if (m := re.search(r"\d+", v))]
            record = {
                "vehicle_id": f"V-{max(numbers, default=100) + 1}",
                "type": str(data.get("type", "")),
                "make": data.get("make", ""),
                "model": data.get("model", ""),
                "transmission": data.get("transmission") or "Automatic",
                "active": True,
            }
            try:
                record["year"] = int(data.get("year"))
                record["daily_rate"] = float(data.get("daily_rate"))
                record["seats"] = int(data.get("seats"))
                if record["type"] == "ElectricCar":
                    record["range_km"] = int(data.get("range_km") or 300)
            except (TypeError, ValueError):
                raise RentalError("Year, seats, daily rate and range must be numbers.") from None
            if record["type"] == "SUV":
                record["four_wheel_drive"] = bool(data.get("four_wheel_drive"))
            elif record["type"] == "LuxuryCar":
                record["chauffeur"] = bool(data.get("chauffeur"))
            try:
                vehicle = VehicleFactory.create(record)  # the classes validate the rest
            except ValueError as exc:
                raise RentalError(str(exc)) from None
            self._repo.save(vehicles=[vehicle.to_dict()])
            self._vehicles[vehicle.vehicle_id] = vehicle
            return vehicle.to_dict()

    def update_vehicle(self, vehicle_id: str, daily_rate=None, active=None) -> dict:
        with self._lock:
            vehicle = self._get_vehicle(vehicle_id)
            old_rate, old_active = vehicle.daily_rate, vehicle.active
            try:
                rate = None if daily_rate in (None, "") else float(daily_rate)
            except (TypeError, ValueError):
                raise RentalError("Rate must be a number.") from None
            try:
                if rate is not None:
                    vehicle.daily_rate = rate  # validating setter
                if active is not None:
                    vehicle.active = bool(active)
                self._repo.save(vehicles=[vehicle.to_dict()])
            except Exception as exc:
                vehicle.daily_rate, vehicle.active = old_rate, old_active  # undo
                if isinstance(exc, ValueError):
                    raise RentalError(str(exc)) from None
                raise
            return vehicle.to_dict()
