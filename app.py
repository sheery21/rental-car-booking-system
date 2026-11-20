"""Vercel/Flask entry point for the rental booking application."""

import json
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import NotFound, RequestEntityTooLarge

from backend.api.server import ApiResponse, RentalAPI, Request
from backend.exceptions import RentalError
from backend.services import AuthService, RentalService
from backend.storage import SqliteRepository, TursoRepository, import_legacy_json

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
PAGES = {"/": "index.html", "/book": "book.html", "/admin": "admin.html"}
MAX_BODY = 64 * 1024


class _DatabaseAuthService(AuthService):
    """Persist admin sessions so any serverless instance can validate them."""

    def __init__(self, password: str, repository):
        super().__init__(password)
        self._repository = repository

    def login(self, password: str, client: str = "local") -> str:
        token = super().login(password, client)
        try:
            self._repository.store_admin_session(
                token, int(self._clock() + self.SESSION_HOURS * 3600))
        except Exception:
            super().logout(token)
            raise
        return token

    def is_valid(self, token: str | None) -> bool:
        return bool(token and self._repository.is_admin_session_valid(token, int(self._clock())))

    def logout(self, token: str | None) -> None:
        if token:
            self._repository.delete_admin_session(token)
        super().logout(token)


def _build_repository():
    database_url = os.getenv("TURSO_DATABASE_URL")
    auth_token = os.getenv("TURSO_AUTH_TOKEN")

    if database_url or auth_token:
        if not database_url or not auth_token:
            raise RuntimeError("Both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set.")
        return TursoRepository(database_url, auth_token)

    if os.getenv("VERCEL"):
        raise RuntimeError(
            "Turso is not connected. Add TURSO_DATABASE_URL and TURSO_AUTH_TOKEN "
            "to the Vercel project environment variables."
        )

    local_database = os.getenv("LOCAL_DATABASE_PATH") or BASE_DIR / "data" / "rental.db"
    repository = SqliteRepository(local_database)
    import_legacy_json(BASE_DIR / "data" / "rental_data.json", repository)
    return repository


def _build_auth_service(repository):
    password = os.getenv("ADMIN_PASSWORD")
    if os.getenv("VERCEL"):
        if not password:
            raise RuntimeError("Set ADMIN_PASSWORD in the Vercel project environment variables.")
        return _DatabaseAuthService(password, repository)
    return AuthService(password or "admin123")


def create_app(repository_factory=None, auth_factory=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    make_repository = repository_factory or _build_repository
    make_auth = auth_factory or _build_auth_service
    auth_service = None
    auth_lock = threading.Lock()

    def get_auth_service(repository):
        nonlocal auth_service
        if auth_service is None:
            with auth_lock:
                if auth_service is None:
                    auth_service = make_auth(repository)
        return auth_service

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif response.status_code == 200:
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_request(_error):
        return jsonify({"error": "Request body too large."}), 413

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def dispatch(path: str):
        route_path = "/" + path if path else "/"
        if not route_path.startswith("/api/"):
            if request.method != "GET":
                return jsonify({"error": "Not allowed"}), 405
            relative = PAGES.get(route_path.rstrip("/") or "/", route_path.lstrip("/"))
            try:
                return send_from_directory(FRONTEND_DIR, relative)
            except NotFound:
                try:
                    return send_from_directory(FRONTEND_DIR, "404.html"), 404
                except NotFound:
                    return "Not found", 404

        try:
            if request.content_length and request.content_length > MAX_BODY:
                raise RentalError("Request body too large.")

            repository = make_repository()
            api = RentalAPI(RentalService(repository), get_auth_service(repository))
            resolved = api.router.resolve(request.method, route_path)
            if resolved is None:
                response = jsonify({"error": "Endpoint not found"})
                response.status_code = 404
                return response

            route, params = resolved
            body = {}
            if request.method == "POST":
                raw_body = request.get_data(cache=False)
                if raw_body:
                    try:
                        body = json.loads(raw_body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise RentalError("Request body must be valid JSON.") from None
                    if not isinstance(body, dict):
                        raise RentalError("Request body must be a JSON object.")

            req = Request(
                request.method,
                route_path,
                params,
                request.args.to_dict(),
                body,
                dict(request.cookies),
                request.remote_addr or "unknown",
            )
            if route.admin:
                api.check_admin(req)

            api_response: ApiResponse = route.handler(req)
            response = jsonify(api_response.body)
            response.status_code = api_response.status
            for name, value in api_response.headers:
                if name.lower() == "set-cookie" and request.headers.get(
                    "X-Forwarded-Proto", ""
                ) == "https":
                    value += "; Secure"
                response.headers.add(name, value)
            return response
        except RentalError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        except Exception:
            app.logger.exception("Request failed")
            return jsonify({"error": "Something went wrong on the server."}), 500

    return app


app = create_app()
