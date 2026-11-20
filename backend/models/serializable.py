"""Serializable interface.

Python has no `interface` keyword, so an abstract class with only abstract
methods plays that role. Every model that is saved to JSON implements it.
"""

from abc import ABC, abstractmethod


class Serializable(ABC):
    """Interface: anything that can be converted to a JSON-friendly dict."""

    @abstractmethod
    def to_dict(self) -> dict:
        """Return a plain dict representation of the object."""
