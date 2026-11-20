"""Booking – ties a Customer, a Vehicle and a Payment together (Composition)."""

from datetime import date, datetime
from enum import Enum

from .customer import Customer
from .payment import Payment
from .serializable import Serializable
from .vehicle import Vehicle


class BookingStatus(Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class Booking(Serializable):
    """A single rental of one vehicle by one customer for a date range."""

    def __init__(self, booking_id: str, customer: Customer, vehicle: Vehicle,
                 start_date: date, end_date: date, payment: Payment,
                 status: BookingStatus = BookingStatus.CONFIRMED,
                 created_at: str | None = None):
        if end_date <= start_date:
            raise ValueError("Return date must be after the pick-up date.")
        self._booking_id = booking_id
        self._customer = customer
        self._vehicle = vehicle
        self._start_date = start_date
        self._end_date = end_date
        self._payment = payment
        self._status = status
        self._created_at = created_at or datetime.now().isoformat(timespec="seconds")

    # ---------- read-only properties ----------
    @property
    def booking_id(self) -> str:
        return self._booking_id

    @property
    def customer(self) -> Customer:
        return self._customer

    @property
    def vehicle(self) -> Vehicle:
        return self._vehicle

    @property
    def payment(self) -> Payment:
        return self._payment

    @property
    def start_date(self) -> date:
        return self._start_date

    @property
    def status(self) -> BookingStatus:
        return self._status

    @property
    def days(self) -> int:
        return (self._end_date - self._start_date).days

    @property
    def is_active(self) -> bool:
        return self._status == BookingStatus.CONFIRMED

    # ---------- behaviour ----------
    def overlaps(self, start: date, end: date) -> bool:
        """True if this active booking clashes with the given date range."""
        return self.is_active and start < self._end_date and end > self._start_date

    def is_ongoing(self, on: date) -> bool:
        """True if the car is out with the customer on the given day."""
        return self.is_active and self._start_date <= on < self._end_date

    def belongs_to(self, email: str) -> bool:
        return self._customer.email == (email or "").strip().lower()

    def cancel(self) -> None:
        if self._status != BookingStatus.CONFIRMED:
            raise ValueError(f"Booking is already {self._status.value}.")
        self._status = BookingStatus.CANCELLED

    def complete(self) -> None:
        if self._status != BookingStatus.CONFIRMED:
            raise ValueError(f"Booking is already {self._status.value}.")
        self._status = BookingStatus.COMPLETED

    def to_dict(self) -> dict:
        return {
            "booking_id": self._booking_id,
            "customer_id": self._customer.customer_id,
            "customer_name": self._customer.name,
            "customer_email": self._customer.email,
            "vehicle_id": self._vehicle.vehicle_id,
            "vehicle_name": self._vehicle.name,
            "vehicle_category": self._vehicle.category,
            "start_date": self._start_date.isoformat(),
            "end_date": self._end_date.isoformat(),
            "days": self.days,
            "rent": self._payment.amount,
            "payment": self._payment.to_dict(),
            "status": self._status.value,
            "created_at": self._created_at,
        }
