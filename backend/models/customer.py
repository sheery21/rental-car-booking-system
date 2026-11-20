"""Person -> Customer hierarchy (Inheritance + Encapsulation)."""

import re

from .serializable import Serializable

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Person(Serializable):
    """Base class for any person in the system."""

    def __init__(self, name: str, email: str, phone: str):
        name = (name or "").strip()
        email = (email or "").strip().lower()
        if len(name) < 2:
            raise ValueError("Name must have at least 2 characters.")
        if not EMAIL_PATTERN.match(email):
            raise ValueError("Please enter a valid email address.")
        self._name = name
        self._email = email
        self._phone = (phone or "").strip()

    @property
    def name(self) -> str:
        return self._name

    @property
    def email(self) -> str:
        return self._email

    @property
    def phone(self) -> str:
        return self._phone

    def role(self) -> str:
        return "Person"

    def to_dict(self) -> dict:
        return {"name": self._name, "email": self._email,
                "phone": self._phone, "role": self.role()}


class Customer(Person):
    """A customer who can rent cars. Extends Person with licence + id."""

    def __init__(self, customer_id: str, name: str, email: str, phone: str,
                 license_no: str):
        super().__init__(name, email, phone)
        license_no = (license_no or "").strip().upper()
        if len(license_no) < 4:
            raise ValueError("Driving licence number is required.")
        self._customer_id = customer_id
        self._license_no = license_no

    @property
    def customer_id(self) -> str:
        return self._customer_id

    @property
    def license_no(self) -> str:
        return self._license_no

    def role(self) -> str:  # method overriding
        return "Customer"

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({"customer_id": self._customer_id, "license_no": self._license_no})
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Customer":
        return cls(data["customer_id"], data["name"], data["email"],
                   data.get("phone", ""), data["license_no"])
