"""Storage layer (Abstraction + Dependency Injection).

`RentalService` depends on the abstract `Repository`, never on a concrete database.
- SqliteRepository   : the real database (SQLite file data/rental.db, built into Python)
- InMemoryRepository : keeps everything in memory, used by the unit tests

Records passed to `save()` are the models' own `to_dict()` output.
"""

import copy
import hashlib
import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import Iterable

VEHICLE_EXTRAS = ("four_wheel_drive", "chauffeur", "range_km")


class Repository(ABC):
    """Abstract storage for vehicles, customers, bookings and id counters."""

    @abstractmethod
    def load(self) -> dict:
        """Return {"vehicles": [...], "customers": [...], "bookings": [...], "counters": {...}}.

        Booking rows are flat: booking_id, customer_id, vehicle_id, start_date,
        end_date, rent, payment_method, status, created_at.
        """

    @abstractmethod
    def save(self, *, vehicles: Iterable[dict] = (), customers: Iterable[dict] = (),
             bookings: Iterable[dict] = (), counters: dict | None = None) -> None:
        """Insert or update the given records in ONE atomic transaction."""

    def is_empty(self) -> bool:
        return not self.load()["vehicles"]

    @staticmethod
    def booking_row(data: dict) -> dict:
        """Flatten Booking.to_dict() into a storage row."""
        payment = data["payment"]
        return {
            "booking_id": data["booking_id"],
            "customer_id": data["customer_id"],
            "vehicle_id": data["vehicle_id"],
            "start_date": data["start_date"],
            "end_date": data["end_date"],
            "rent": payment["amount"],
            "payment_method": payment["method"],
            "fee": payment["fee"],
            "total": payment["total"],
            "status": data["status"],
            "created_at": data.get("created_at") or f"{data['start_date']}T00:00:00",
        }


class SqliteRepository(Repository):
    """SQLite database – one file, no server, ships with Python (`sqlite3`)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS vehicles (
        vehicle_id   TEXT PRIMARY KEY,
        type         TEXT    NOT NULL,
        make         TEXT    NOT NULL,
        model        TEXT    NOT NULL,
        year         INTEGER NOT NULL,
        daily_rate   REAL    NOT NULL CHECK (daily_rate > 0),
        seats        INTEGER NOT NULL,
        transmission TEXT    NOT NULL,
        extras       TEXT    NOT NULL DEFAULT '{}',
        active       INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        email       TEXT NOT NULL UNIQUE,
        phone       TEXT NOT NULL DEFAULT '',
        license_no  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id     TEXT PRIMARY KEY,
        customer_id    TEXT NOT NULL REFERENCES customers(customer_id),
        vehicle_id     TEXT NOT NULL REFERENCES vehicles(vehicle_id),
        start_date     TEXT NOT NULL,
        end_date       TEXT NOT NULL,
        rent           REAL NOT NULL,
        payment_method TEXT NOT NULL,
        fee            REAL NOT NULL,
        total          REAL NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled', 'completed')),
        created_at     TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_vehicle ON bookings (vehicle_id);
    CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings (customer_id);
    CREATE TABLE IF NOT EXISTS counters (
        name  TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS admin_sessions (
        token_hash TEXT PRIMARY KEY,
        expires_at INTEGER NOT NULL
    );
    """

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(self.SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _rows_as_dicts(cursor) -> list[dict]:
        columns = [column[0] for column in cursor.description or ()]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def load(self) -> dict:
        with closing(self._connect()) as conn:
            vehicles = []
            for data in self._rows_as_dicts(
                    conn.execute("SELECT * FROM vehicles ORDER BY vehicle_id")):
                data.update(json.loads(data.pop("extras") or "{}"))
                data["active"] = bool(data["active"])
                vehicles.append(data)
            customers = self._rows_as_dicts(
                conn.execute("SELECT * FROM customers ORDER BY customer_id"))
            bookings = self._rows_as_dicts(
                conn.execute("SELECT * FROM bookings ORDER BY booking_id"))
            counters = {
                row["name"]: row["value"]
                for row in self._rows_as_dicts(conn.execute("SELECT * FROM counters"))
            }
        return {"vehicles": vehicles, "customers": customers, "bookings": bookings,
                "counters": counters}

    def save(self, *, vehicles=(), customers=(), bookings=(), counters=None) -> None:
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN")
                self._save_records(conn, vehicles, customers, bookings, counters)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def store_admin_session(self, token: str, expires_at: int) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?",
                             (int(time.time()),))
                conn.execute(
                    """INSERT INTO admin_sessions (token_hash, expires_at) VALUES (?, ?)
                       ON CONFLICT (token_hash) DO UPDATE SET expires_at = excluded.expires_at""",
                    (token_hash, expires_at))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def is_admin_session_valid(self, token: str, now: int) -> bool:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT expires_at FROM admin_sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            return bool(row and row[0] >= now)

    def delete_admin_session(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(self._connect()) as conn:
            try:
                conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _save_records(self, conn, vehicles, customers, bookings, counters) -> None:
        for v in vehicles:
            extras = {k: v[k] for k in VEHICLE_EXTRAS if k in v}
            conn.execute(
                """INSERT INTO vehicles (vehicle_id, type, make, model, year, daily_rate,
                                         seats, transmission, extras, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (vehicle_id) DO UPDATE SET
                     type = excluded.type, make = excluded.make, model = excluded.model,
                     year = excluded.year, daily_rate = excluded.daily_rate,
                     seats = excluded.seats, transmission = excluded.transmission,
                     extras = excluded.extras, active = excluded.active""",
                (v["vehicle_id"], v["type"], v["make"], v["model"], v["year"],
                 v["daily_rate"], v["seats"], v["transmission"], json.dumps(extras),
                 int(v.get("active", True))))
        for c in customers:
            conn.execute(
                """INSERT INTO customers (customer_id, name, email, phone, license_no)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (customer_id) DO UPDATE SET
                     name = excluded.name, email = excluded.email,
                     phone = excluded.phone, license_no = excluded.license_no""",
                (c["customer_id"], c["name"], c["email"], c.get("phone", ""), c["license_no"]))
        for b in bookings:
            r = self.booking_row(b)
            conn.execute(
                """INSERT INTO bookings (booking_id, customer_id, vehicle_id, start_date,
                                         end_date, rent, payment_method, fee, total,
                                         status, created_at)
                   VALUES (:booking_id, :customer_id, :vehicle_id, :start_date, :end_date,
                           :rent, :payment_method, :fee, :total, :status, :created_at)
                   ON CONFLICT (booking_id) DO UPDATE SET status = excluded.status""", r)
        for name, value in (counters or {}).items():
            conn.execute("""INSERT INTO counters (name, value) VALUES (?, ?)
                            ON CONFLICT (name) DO UPDATE SET value = excluded.value""",
                         (name, value))


class TursoRepository(SqliteRepository):
    """Remote Turso database accessed over HTTP for serverless deployments."""

    _initialized_databases: set[str] = set()
    _initialization_lock = threading.Lock()

    def __init__(self, database_url: str, auth_token: str):
        if not database_url or not auth_token:
            raise ValueError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required.")
        self._database_url = database_url
        self._auth_token = auth_token
        with self._initialization_lock:
            if database_url not in self._initialized_databases:
                with closing(self._connect()) as conn:
                    try:
                        for statement in self.SCHEMA.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                self._initialized_databases.add(database_url)

    def _connect(self):
        import turso_serverless

        return turso_serverless.connect(self._database_url, auth_token=self._auth_token)


class InMemoryRepository(Repository):
    """Keeps the state in memory only – used by unit tests."""

    def __init__(self):
        self._vehicles: dict[str, dict] = {}
        self._customers: dict[str, dict] = {}
        self._bookings: dict[str, dict] = {}
        self._counters: dict[str, int] = {}

    def load(self) -> dict:
        return copy.deepcopy({"vehicles": list(self._vehicles.values()),
                              "customers": list(self._customers.values()),
                              "bookings": list(self._bookings.values()),
                              "counters": self._counters})

    def save(self, *, vehicles=(), customers=(), bookings=(), counters=None) -> None:
        for v in vehicles:
            self._vehicles[v["vehicle_id"]] = copy.deepcopy(v)
        for c in customers:
            self._customers[c["customer_id"]] = copy.deepcopy(c)
        for b in bookings:
            self._bookings[b["booking_id"]] = self.booking_row(b)
        self._counters.update(counters or {})


def import_legacy_json(json_path: Path, repository: Repository) -> bool:
    """One-time move of the old data/rental_data.json into the database.

    Runs only when the database is still empty. The JSON file is left untouched.
    """
    if not json_path.exists() or not repository.is_empty():
        return False
    with json_path.open(encoding="utf-8") as fh:
        state = json.load(fh)
    repository.save(vehicles=state.get("vehicles", []), customers=state.get("customers", []),
                    bookings=state.get("bookings", []), counters=state.get("counters", {}))
    return True
