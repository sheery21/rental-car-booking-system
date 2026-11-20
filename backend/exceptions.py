"""Custom exceptions – a small class hierarchy of its own (Inheritance).

The HTTP layer catches RentalError and replies with its `status_code`.
"""


class RentalError(Exception):
    """Base error for the rental system. The API turns it into a 4xx response."""

    status_code = 400


class NotFoundError(RentalError):
    status_code = 404


class VehicleNotAvailableError(RentalError):
    status_code = 409


class AuthError(RentalError):
    """Wrong admin password or missing / expired admin session."""

    status_code = 401


class TooManyAttemptsError(AuthError):
    """Too many wrong passwords from the same client – temporarily locked."""

    status_code = 429
