"""Entry point – run:  python main.py   then open http://127.0.0.1:8000

Pages:  /       landing page
        /book   book a car / find my booking
        /admin  admin panel (password login)

Admin password: set the ADMIN_PASSWORD environment variable (or --admin-password).
If neither is set, the default "admin123" is used and a warning is printed.
"""

import argparse
import os
from pathlib import Path

from backend.api import create_server
from backend.services import AuthService, RentalService
from backend.storage import SqliteRepository, import_legacy_json

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ADMIN_PASSWORD = "admin123"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rental Car Booking System")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(BASE_DIR / "data" / "rental.db"),
                        help="path of the SQLite database file")
    parser.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD"))
    args = parser.parse_args()

    # ---- composition root: build the objects and inject them into each other
    repository = SqliteRepository(args.db)
    if import_legacy_json(BASE_DIR / "data" / "rental_data.json", repository):
        print("Imported old data/rental_data.json into the SQLite database.")
    service = RentalService(repository)
    auth = AuthService(args.admin_password or DEFAULT_ADMIN_PASSWORD)
    server = create_server(service, auth, BASE_DIR / "frontend", args.host, args.port)

    url = f"http://{args.host}:{args.port}"
    print(f"Rental Car Booking System running at {url}")
    print(f"  Landing page : {url}/")
    print(f"  Book a car   : {url}/book")
    print(f"  Admin panel  : {url}/admin")
    print(f"  Database     : {repository.path}")
    if not args.admin_password:
        print(f"  WARNING: using the default admin password '{DEFAULT_ADMIN_PASSWORD}'. "
              "Set ADMIN_PASSWORD to change it.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
