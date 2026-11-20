# Rental Car Booking System

A small car-rental web app with a landing page, a booking page and an admin panel. The backend is object-oriented Python. It uses SQLite locally and Turso when deployed to Vercel. The frontend is plain HTML, CSS and JavaScript (ES modules with classes).

## Run it

```bash
cd rental-car-booking-system
python main.py                 # then open http://127.0.0.1:8000
```

You need Python 3.10 or newer. Nothing else has to be installed.

| Page | URL | Who |
|---|---|---|
| Landing page | `/` | everyone |
| Book a car, my bookings | `/book` | customers |
| Admin panel | `/admin` | admin (password only) |

### Admin password

The admin logs in with a password only. There is no username.

```bash
# Windows (PowerShell)
$env:ADMIN_PASSWORD = "my-secret-pass"; python main.py
# macOS / Linux
ADMIN_PASSWORD=my-secret-pass python main.py
# or
python main.py --admin-password my-secret-pass
```

If you don't set one, the password is **`admin123`** and the server prints a warning. Change it before anyone else can reach the site.

- The password is kept only as a PBKDF2 hash, never as plain text.
- A successful login sets an `HttpOnly`, `SameSite=Strict` cookie that lasts 8 hours.
- After 5 wrong passwords from the same computer, login is blocked for 5 minutes.
- Every `/api/admin/...` endpoint (except login, logout and session) returns 401 without a valid session.

## Deploy to Vercel with Turso

The repository root is the Vercel project root. `pyproject.toml` points Vercel to the
Flask application in `app.py`; the existing `main.py` entry point remains available for
local development.

1. Import the GitHub repository into Vercel, keeping the root directory as `./`.
2. In the Vercel project, open **Storage**, install/connect Turso, and attach the database
   to this project.
3. Ensure these environment variables are available to the project:
   - `TURSO_DATABASE_URL`
   - `TURSO_AUTH_TOKEN`
   - `ADMIN_PASSWORD` (at least 6 characters; use a strong unique password)
4. Deploy or redeploy. On first API request, the application creates the tables and seeds
   the default fleet if the database is empty.

The Turso variables are read by `app.py`; when they are absent on Vercel, API requests
return an error rather than silently using an ephemeral SQLite file. The database schema
is also used for admin sessions, so login remains valid across serverless instances and
logout revokes the stored session.

Vercel local development can be run with:

```bash
python -m pip install -r requirements.txt
python -m flask --app app run
```

For local development without Turso, the Flask app uses `data/rental.db`.
Set `LOCAL_DATABASE_PATH` to use another local SQLite file; if it is empty, the legacy
`data/rental_data.json` is imported into it on first run.

## What it does

**Customers (`/book`)**
- See which cars are free for the chosen dates, filtered by category
- Get a live price quote. Each category prices differently, and each payment method adds its own fee
- Book a car and get a booking ID right away
- **My bookings**: enter your email to see all your bookings. The booking ID is optional and narrows the list to one booking. You can cancel online any booking whose pick-up day hasn't arrived yet

**Admin (`/admin`)**
- Overview: active bookings, revenue, cars free today, customers, bookings by status, revenue by category, latest bookings
- Bookings: filter by status, search, mark as returned, cancel
- Fleet: add a car, change its daily rate, retire or re-activate it (retired cars stay in the booking history but customers can't see them)
- Customers: every customer with their booking count and total spent

## Database

The app uses **SQLite**, which is built into Python (`sqlite3`). The database is the file `data/rental.db` and is created on first run with 9 cars.

| Table | Holds |
|---|---|
| `vehicles` | fleet, rate, active flag, type-specific extras |
| `customers` | name, email (unique), phone, licence |
| `bookings` | dates, rent, payment method, fee, total, status (foreign keys to customers and vehicles) |
| `counters` | the next booking / customer number |

Each action is saved in one transaction. If an old `data/rental_data.json` exists and the database is empty, it is imported once. The JSON file is left untouched.

Use `--db path/to/other.db` to point at another database file (handy for testing).

## Pricing rules (polymorphism)

| Class | `calculate_rent(days)` |
|---|---|
| `EconomyCar` | rate × days, 10% off for 7+ days |
| `SUV` | rate × days + Rs 3,000 insurance |
| `LuxuryCar` | (rate + Rs 2,500 insurance) × days |
| `ElectricCar` | rate × days, 5% green discount |

| Payment | `fee()` |
|---|---|
| `CashPayment` | 0 |
| `CardPayment` | 2% |
| `WalletPayment` | Rs 100 |

## Project structure

```
rental-car-booking-system/
├── main.py                  # composition root: SqliteRepository -> RentalService, AuthService -> server
├── app.py                   # Flask entry point for Vercel
├── backend/
│   ├── models/              # Vehicle (+4 types), Customer/Person, Booking, Payment (+3), factories
│   ├── services/            # RentalService, AuthService, seed data
│   ├── storage/             # Repository, SqliteRepository, TursoRepository, InMemoryRepository
│   ├── api/                 # Request, ApiResponse, Router, RentalAPI, RentalRequestHandler
│   └── exceptions.py        # RentalError, NotFoundError, VehicleNotAvailableError, AuthError ...
├── frontend/
│   ├── index.html           # landing page   (/)
│   ├── book.html            # booking page   (/book)
│   ├── admin.html           # admin panel    (/admin)
│   ├── css/                 # base.css + one file per page
│   └── js/                  # common.js, landing.js, book.js, admin.js (ES modules, classes)
├── data/                    # rental.db (created at runtime)
├── docs/
│   ├── generate_diagram.py  # builds the PDF from the source code
│   └── project_structure.pdf
└── tests/                   # unittest suite
```

## OOP concepts used

The last page of [docs/project_structure.pdf](docs/project_structure.pdf) maps each concept to the code. In short:

- **Encapsulation**: private `__daily_rate` and `__password_hash`, validated property setters. Booking status changes only through `cancel()` and `complete()`.
- **Abstraction**: `Vehicle`, `Payment`, `Repository` and `Serializable` are abstract base classes.
- **Inheritance**: 4 car types extend `Vehicle`, `Customer` extends `Person`, 3 payment types extend `Payment`, 2 repositories extend `Repository`, and the exceptions form their own hierarchy.
- **Polymorphism**: `calculate_rent()`, `fee()`, `features()`, `load()`/`save()` and `do_GET()`/`do_POST()` are overridden in subclasses.
- **Composition**: a `Booking` has a `Customer`, a `Vehicle` and a `Payment`. `RentalAPI` has a `Router`, which has `Route` objects.
- **Patterns**: Factory, Repository, Dependency Injection and Facade.

## Tests

```bash
python -m unittest discover tests
```

Tests cover pricing, validation, overlapping bookings, customer lookup and cancel, adding and retiring cars, admin login and lock-out, SQLite persistence, and the Vercel adapter.

## Rebuild the diagram PDF

```bash
pip install reportlab
python docs/generate_diagram.py
```

The script reads the source code: classes with Python `ast`, routes and pages from `server.py`, the database schema by running `SqliteRepository.SCHEMA`, the JS classes from `frontend/js/`, and the folder tree from disk. Run it again after you change the code and the PDF will match.
