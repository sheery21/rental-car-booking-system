"""HTTP layer – a small JSON API + page/static server built on the standard library.

Classes:
- Request / ApiResponse : plain data objects passed in and out of every endpoint.
- Route / Router        : maps (HTTP method, URL pattern) -> handler; marks admin routes.
- RentalAPI             : registers every endpoint and calls RentalService / AuthService.
- RentalRequestHandler  : subclass of BaseHTTPRequestHandler (Inheritance) that
                          overrides do_GET / do_POST (Method overriding).
"""

import json
import re
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..exceptions import RentalError
from ..models import PaymentFactory, VehicleFactory
from ..services import AuthService, RentalService

SESSION_COOKIE = "admin_session"
PAGES = {"/": "index.html", "/book": "book.html", "/admin": "admin.html"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@dataclass
class Request:
    method: str
    path: str
    params: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    client: str = "local"


@dataclass
class ApiResponse:
    status: int
    body: object
    headers: list[tuple[str, str]] = field(default_factory=list)


Handler = Callable[[Request], ApiResponse]


@dataclass
class Route:
    method: str
    regex: re.Pattern
    handler: Handler
    admin: bool = False


class Router:
    """Very small regex based router."""

    def __init__(self):
        self._routes: list[Route] = []

    def add(self, method: str, pattern: str, handler: Handler, admin: bool = False) -> None:
        self._routes.append(Route(method, re.compile(f"^{pattern}$"), handler, admin))

    def resolve(self, method: str, path: str) -> tuple[Route, dict] | None:
        for route in self._routes:
            match = route.regex.match(path)
            if match and route.method == method:
                return route, match.groupdict()
        return None


class RentalAPI:
    """All REST endpoints. Admin endpoints need a valid session cookie."""

    ID = r"(?P<id>[\w-]+)"

    def __init__(self, service: RentalService, auth: AuthService):
        self._service = service
        self._auth = auth
        self.router = Router()
        r, ID = self.router, self.ID
        # ---- public (customers)
        r.add("GET", r"/api/meta", self.meta)
        r.add("GET", r"/api/vehicles", self.vehicles)
        r.add("GET", r"/api/quote", self.quote)
        r.add("POST", r"/api/bookings", self.create_booking)
        r.add("POST", r"/api/bookings/lookup", self.lookup_booking)
        r.add("POST", rf"/api/bookings/{ID}/cancel", self.customer_cancel)
        # ---- admin auth
        r.add("POST", r"/api/admin/login", self.login)
        r.add("POST", r"/api/admin/logout", self.logout)
        r.add("GET", r"/api/admin/session", self.session)
        # ---- admin only
        r.add("GET", r"/api/admin/stats", self.stats, admin=True)
        r.add("GET", r"/api/admin/bookings", self.bookings, admin=True)
        r.add("POST", rf"/api/admin/bookings/{ID}/cancel", self.admin_cancel, admin=True)
        r.add("POST", rf"/api/admin/bookings/{ID}/return", self.admin_return, admin=True)
        r.add("GET", r"/api/admin/vehicles", self.all_vehicles, admin=True)
        r.add("POST", r"/api/admin/vehicles", self.add_vehicle, admin=True)
        r.add("POST", rf"/api/admin/vehicles/{ID}", self.update_vehicle, admin=True)
        r.add("GET", r"/api/admin/customers", self.customers, admin=True)

    def check_admin(self, req: Request) -> None:
        self._auth.require(req.cookies.get(SESSION_COOKIE))

    # ---------------- public ----------------
    def meta(self, req):
        return ApiResponse(HTTPStatus.OK, {"categories": self._service.categories(),
                                           "payment_methods": PaymentFactory.methods(),
                                           "vehicle_types": VehicleFactory.types()})

    def vehicles(self, req):
        q = req.query
        return ApiResponse(HTTPStatus.OK, self._service.list_vehicles(
            q.get("start"), q.get("end"), q.get("category")))

    def quote(self, req):
        q = req.query
        return ApiResponse(HTTPStatus.OK, self._service.quote(
            q.get("vehicle_id", ""), q.get("start", ""), q.get("end", ""),
            q.get("payment_method", "cash")))

    def create_booking(self, req):
        fields = ["name", "email", "phone", "license_no", "vehicle_id",
                  "start_date", "end_date", "payment_method"]
        data = {f: str(req.body.get(f, "")).strip() for f in fields}
        return ApiResponse(HTTPStatus.CREATED, self._service.create_booking(**data))

    def lookup_booking(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.find_bookings(
            str(req.body.get("email", "")), str(req.body.get("booking_id", ""))))

    def customer_cancel(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.cancel_by_customer(
            req.params["id"], str(req.body.get("email", ""))))

    # ---------------- admin auth ----------------
    def login(self, req):
        token = self._auth.login(str(req.body.get("password", "")), req.client)
        cookie = (f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
                  f"Max-Age={AuthService.SESSION_HOURS * 3600}")
        return ApiResponse(HTTPStatus.OK, {"authenticated": True}, [("Set-Cookie", cookie)])

    def logout(self, req):
        self._auth.logout(req.cookies.get(SESSION_COOKIE))
        cookie = f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        return ApiResponse(HTTPStatus.OK, {"authenticated": False}, [("Set-Cookie", cookie)])

    def session(self, req):
        return ApiResponse(HTTPStatus.OK,
                           {"authenticated": self._auth.is_valid(req.cookies.get(SESSION_COOKIE))})

    # ---------------- admin only ----------------
    def stats(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.stats())

    def bookings(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.list_bookings(req.query.get("status")))

    def admin_cancel(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.cancel_booking(req.params["id"]))

    def admin_return(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.complete_booking(req.params["id"]))

    def all_vehicles(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.list_all_vehicles())

    def add_vehicle(self, req):
        return ApiResponse(HTTPStatus.CREATED, self._service.add_vehicle(req.body))

    def update_vehicle(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.update_vehicle(
            req.params["id"], req.body.get("daily_rate"), req.body.get("active")))

    def customers(self, req):
        return ApiResponse(HTTPStatus.OK, self._service.list_customers())


class RentalRequestHandler(BaseHTTPRequestHandler):
    """Turns raw HTTP requests into RentalAPI calls, pages or static files."""

    MAX_BODY = 64 * 1024
    SECURITY_HEADERS = [("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                        ("Referrer-Policy", "same-origin")]

    def __init__(self, *args, api: RentalAPI, static_dir: Path, **kwargs):
        self.api = api
        self.static_dir = static_dir
        super().__init__(*args, **kwargs)

    # ----- method overriding -----
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, fmt, *args):  # quieter console output
        print(f"  {self.command} {urlparse(self.path).path} -> {args[1] if len(args) > 1 else ''}")

    # ----- internals -----
    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method == "GET":
                return self._serve_static(url.path)
            return self._send_json(ApiResponse(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Not allowed"}))

        resolved = self.api.router.resolve(method, url.path)
        if resolved is None:
            return self._send_json(ApiResponse(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"}))
        route, params = resolved
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            req = Request(method, url.path, params,
                          {k: v[0] for k, v in parse_qs(url.query).items()},
                          self._read_body() if method == "POST" else {},
                          {k: m.value for k, m in cookies.items()},
                          self.client_address[0])
            if route.admin:
                self.api.check_admin(req)
            self._send_json(route.handler(req))
        except RentalError as exc:
            self._send_json(ApiResponse(exc.status_code, {"error": str(exc)}))
        except Exception as exc:  # never crash the server on one bad request
            print(f"  !! unexpected error: {exc!r}")
            self._send_json(ApiResponse(HTTPStatus.INTERNAL_SERVER_ERROR,
                                        {"error": "Something went wrong on the server."}))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.MAX_BODY:
            raise RentalError("Request body too large.")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RentalError("Request body must be valid JSON.") from None
        if not isinstance(data, dict):
            raise RentalError("Request body must be a JSON object.")
        return data

    def _send(self, status: int, raw: bytes, content_type: str,
              headers: list[tuple[str, str]] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for name, value in [*self.SECURITY_HEADERS, *headers]:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_json(self, resp: ApiResponse) -> None:
        self._send(resp.status, json.dumps(resp.body).encode("utf-8"),
                   "application/json; charset=utf-8",
                   [("Cache-Control", "no-store"), *resp.headers])

    def _serve_static(self, path: str) -> None:
        relative = PAGES.get(path.rstrip("/") or "/", path.lstrip("/"))
        file_path = (self.static_dir / relative).resolve()
        if (not file_path.is_relative_to(self.static_dir) or not file_path.is_file()
                or file_path.suffix not in CONTENT_TYPES):
            page = self.static_dir / "404.html"
            body = page.read_bytes() if page.exists() else b"Not found"
            return self._send(HTTPStatus.NOT_FOUND, body, CONTENT_TYPES[".html"])
        self._send(HTTPStatus.OK, file_path.read_bytes(), CONTENT_TYPES[file_path.suffix],
                   [("Cache-Control", "no-cache")])


def create_server(service: RentalService, auth: AuthService, static_dir: Path,
                  host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = partial(RentalRequestHandler, api=RentalAPI(service, auth),
                      static_dir=Path(static_dir).resolve())
    return ThreadingHTTPServer((host, port), handler)


__all__ = ["Request", "ApiResponse", "Router", "RentalAPI", "RentalRequestHandler",
           "create_server"]
