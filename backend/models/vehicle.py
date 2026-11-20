"""Vehicle hierarchy.

OOP concepts used here:
- Abstraction    : `Vehicle` is an abstract base class (ABC) – it cannot be created directly.
- Encapsulation  : daily rate is a private attribute (`__daily_rate`) exposed through a
                   validated property.
- Inheritance    : EconomyCar, SUV, LuxuryCar and ElectricCar all extend `Vehicle`.
- Polymorphism   : every subclass overrides `calculate_rent()` with its own pricing rule.
- Factory pattern: `VehicleFactory` builds the right subclass from plain data.
"""

from abc import ABC, abstractmethod
from datetime import date

from .serializable import Serializable


class Vehicle(Serializable, ABC):
    """Abstract base class for every vehicle in the fleet."""

    TRANSMISSIONS = ("Automatic", "Manual")

    def __init__(self, vehicle_id: str, make: str, model: str, year: int,
                 daily_rate: float, seats: int, transmission: str = "Automatic",
                 active: bool = True):
        make, model = str(make or "").strip(), str(model or "").strip()
        if not make or not model:
            raise ValueError("Make and model are required.")
        if not 1990 <= int(year) <= date.today().year + 1:
            raise ValueError("Year looks wrong.")
        if not 2 <= int(seats) <= 15:
            raise ValueError("Seats must be between 2 and 15.")
        if transmission not in self.TRANSMISSIONS:
            raise ValueError("Transmission must be Automatic or Manual.")
        self._vehicle_id = vehicle_id
        self._make = make
        self._model = model
        self._year = int(year)
        self._seats = int(seats)
        self._transmission = transmission
        self._active = bool(active)
        self.__daily_rate = 0.0
        self.daily_rate = daily_rate  # goes through the validating setter

    # ---------- Encapsulation: read-only + validated properties ----------
    @property
    def vehicle_id(self) -> str:
        return self._vehicle_id

    @property
    def name(self) -> str:
        return f"{self._make} {self._model} ({self._year})"

    @property
    def daily_rate(self) -> float:
        return self.__daily_rate

    @daily_rate.setter
    def daily_rate(self, value: float) -> None:
        value = float(value)
        if value <= 0:
            raise ValueError("Daily rate must be greater than zero.")
        self.__daily_rate = value

    @property
    def seats(self) -> int:
        return self._seats

    @property
    def active(self) -> bool:
        """Retired cars (active = False) stay in history but cannot be booked."""
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        self._active = bool(value)

    # ---------- Abstraction: subclasses MUST implement these ----------
    @property
    @abstractmethod
    def category(self) -> str:
        """Human readable category name."""

    @abstractmethod
    def calculate_rent(self, days: int) -> float:
        """Total rent for the given number of days (polymorphic)."""

    def features(self) -> list[str]:
        """Default features – subclasses extend this list."""
        return [f"{self._seats} seats", self._transmission]

    # ---------- Serializable ----------
    def to_dict(self) -> dict:
        return {
            "vehicle_id": self._vehicle_id,
            "type": type(self).__name__,
            "category": self.category,
            "make": self._make,
            "model": self._model,
            "year": self._year,
            "name": self.name,
            "daily_rate": self.daily_rate,
            "seats": self._seats,
            "transmission": self._transmission,
            "features": self.features(),
            "active": self._active,
        }

    def __str__(self) -> str:
        return f"[{self.category}] {self.name} - Rs {self.daily_rate:,.0f}/day"


class EconomyCar(Vehicle):
    """Budget car – 10% discount on rentals of 7 days or more."""

    WEEKLY_DISCOUNT = 0.10

    @property
    def category(self) -> str:
        return "Economy"

    def calculate_rent(self, days: int) -> float:
        rent = self.daily_rate * days
        if days >= 7:
            rent -= rent * self.WEEKLY_DISCOUNT
        return round(rent, 2)

    def features(self) -> list[str]:
        return super().features() + ["Fuel efficient", "10% off on 7+ days"]


class SUV(Vehicle):
    """SUV – adds a fixed off-road insurance charge per booking."""

    INSURANCE_FEE = 3000

    def __init__(self, *args, four_wheel_drive: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._four_wheel_drive = four_wheel_drive

    @property
    def category(self) -> str:
        return "SUV"

    def calculate_rent(self, days: int) -> float:
        return round(self.daily_rate * days + self.INSURANCE_FEE, 2)

    def features(self) -> list[str]:
        extra = ["4x4 drive"] if self._four_wheel_drive else []
        return super().features() + extra + ["Off-road insurance included"]

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["four_wheel_drive"] = self._four_wheel_drive
        return data


class LuxuryCar(Vehicle):
    """Luxury car – daily premium insurance + chauffeur option."""

    PREMIUM_INSURANCE_PER_DAY = 2500

    def __init__(self, *args, chauffeur: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._chauffeur = chauffeur

    @property
    def category(self) -> str:
        return "Luxury"

    def calculate_rent(self, days: int) -> float:
        rent = (self.daily_rate + self.PREMIUM_INSURANCE_PER_DAY) * days
        return round(rent, 2)

    def features(self) -> list[str]:
        extra = ["Chauffeur available"] if self._chauffeur else []
        return super().features() + ["Premium insurance"] + extra

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["chauffeur"] = self._chauffeur
        return data


class ElectricCar(Vehicle):
    """Electric car – 5% green discount on every rental."""

    GREEN_DISCOUNT = 0.05

    def __init__(self, *args, range_km: int = 400, **kwargs):
        super().__init__(*args, **kwargs)
        self._range_km = int(range_km)

    @property
    def category(self) -> str:
        return "Electric"

    def calculate_rent(self, days: int) -> float:
        rent = self.daily_rate * days
        return round(rent - rent * self.GREEN_DISCOUNT, 2)

    def features(self) -> list[str]:
        return super().features() + [f"{self._range_km} km range", "5% green discount"]

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["range_km"] = self._range_km
        return data


class VehicleFactory:
    """Factory pattern: creates the correct Vehicle subclass from a dict."""

    _registry: dict[str, type[Vehicle]] = {
        "EconomyCar": EconomyCar,
        "SUV": SUV,
        "LuxuryCar": LuxuryCar,
        "ElectricCar": ElectricCar,
    }

    EXTRA_FIELDS = ("four_wheel_drive", "chauffeur", "range_km")

    @classmethod
    def create(cls, data: dict) -> Vehicle:
        vehicle_cls = cls._registry.get(data.get("type", ""))
        if vehicle_cls is None:
            raise ValueError(f"Unknown vehicle type: {data.get('type')}")
        extras = {k: v for k, v in data.items() if k in cls.EXTRA_FIELDS}
        return vehicle_cls(
            data["vehicle_id"], data["make"], data["model"], data["year"],
            data["daily_rate"], data["seats"], data.get("transmission", "Automatic"),
            active=data.get("active", True), **extras,
        )

    @classmethod
    def types(cls) -> list[str]:
        return list(cls._registry)
