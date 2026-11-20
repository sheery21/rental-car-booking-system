"""Payment methods (Abstraction + Polymorphism).

The booking only knows it has *a* Payment. Which concrete class it is decides
the processing fee and the receipt text.
"""

from abc import ABC, abstractmethod

from .serializable import Serializable


class Payment(Serializable, ABC):
    """Abstract payment method."""

    def __init__(self, amount: float):
        if amount < 0:
            raise ValueError("Amount cannot be negative.")
        self._amount = round(float(amount), 2)

    @property
    def amount(self) -> float:
        return self._amount

    @property
    @abstractmethod
    def method(self) -> str:
        """Short machine name, e.g. 'card'."""

    @abstractmethod
    def fee(self) -> float:
        """Processing fee charged by this payment method."""

    @property
    def total(self) -> float:
        return round(self._amount + self.fee(), 2)

    def to_dict(self) -> dict:
        return {"method": self.method, "amount": self._amount,
                "fee": self.fee(), "total": self.total}


class CashPayment(Payment):
    """Pay at the counter – no extra fee."""

    @property
    def method(self) -> str:
        return "cash"

    def fee(self) -> float:
        return 0.0


class CardPayment(Payment):
    """Debit / credit card – 2% bank processing fee."""

    FEE_RATE = 0.02

    @property
    def method(self) -> str:
        return "card"

    def fee(self) -> float:
        return round(self._amount * self.FEE_RATE, 2)


class WalletPayment(Payment):
    """Mobile wallet (JazzCash / Easypaisa) – flat Rs 100 fee."""

    FLAT_FEE = 100.0

    @property
    def method(self) -> str:
        return "wallet"

    def fee(self) -> float:
        return self.FLAT_FEE


class PaymentFactory:
    """Factory: payment method name -> Payment object."""

    _methods: dict[str, type[Payment]] = {
        "cash": CashPayment,
        "card": CardPayment,
        "wallet": WalletPayment,
    }

    @classmethod
    def create(cls, method: str, amount: float) -> Payment:
        payment_cls = cls._methods.get((method or "").lower())
        if payment_cls is None:
            raise ValueError(f"Unsupported payment method: {method}")
        return payment_cls(amount)

    @classmethod
    def methods(cls) -> list[str]:
        return list(cls._methods)
