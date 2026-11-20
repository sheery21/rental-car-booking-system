"""AuthService – password-only admin login with server-side sessions.

- The password is never stored in plain text: it is hashed with PBKDF2 + random salt.
- A successful login returns a random session token (sent to the browser as an
  HttpOnly cookie). Tokens expire after SESSION_HOURS.
- After MAX_ATTEMPTS wrong passwords a client is locked out for LOCK_SECONDS.
"""

import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable

from ..exceptions import AuthError, TooManyAttemptsError


class AuthService:
    SESSION_HOURS = 8
    MAX_ATTEMPTS = 5
    LOCK_SECONDS = 300
    _ITERATIONS = 200_000

    def __init__(self, password: str, clock: Callable[[], float] = time.time):
        if len(password or "") < 6:
            raise ValueError("Admin password must have at least 6 characters.")
        self._clock = clock
        self._salt = secrets.token_bytes(16)
        self.__password_hash = self._hash(password)  # private – never exposed
        self._sessions: dict[str, float] = {}         # token -> expiry timestamp
        self._failures: dict[str, tuple[int, float]] = {}  # client -> (count, locked_until)
        self._lock = threading.Lock()

    def _hash(self, password: str) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self._salt,
                                   self._ITERATIONS)

    def login(self, password: str, client: str = "local") -> str:
        """Return a new session token, or raise AuthError / TooManyAttemptsError."""
        now = self._clock()
        with self._lock:
            count, locked_until = self._failures.get(client, (0, 0.0))
            if locked_until > now:
                wait = int(locked_until - now) + 1
                raise TooManyAttemptsError(f"Too many wrong attempts. Try again in {wait} seconds.")

        ok = hmac.compare_digest(self._hash(password or ""), self.__password_hash)

        with self._lock:
            if not ok:
                count = 0 if locked_until else count  # lock expired -> start counting again
                count += 1
                locked = now + self.LOCK_SECONDS if count >= self.MAX_ATTEMPTS else 0.0
                self._failures[client] = (count, locked)
                raise AuthError("Wrong password.")
            self._failures.pop(client, None)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + self.SESSION_HOURS * 3600
            return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expiry = self._sessions.get(token)
            if expiry is None:
                return False
            if expiry < self._clock():
                del self._sessions[token]
                return False
            return True

    def require(self, token: str | None) -> None:
        if not self.is_valid(token):
            raise AuthError("Please log in as admin.")

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)
