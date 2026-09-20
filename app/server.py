"""Flask routes.

Staff drafting is gated here rather than in the browser. The frontend may hide
the form, but this is what actually refuses.
"""

import os

from flask import Flask, jsonify, render_template, request, session

from app import auth
from app.data_store import synthetic_kb
from app.drafting import Drafter

# Origins allowed to call the API with cookies — the dev servers by default.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "TICKET_MAKER_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ).split(",")
    if origin.strip()
]


def create_app(drafter="auto"):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    # Keep the ticket fields in the order they are written, not alphabetical.
    app.json.sort_keys = False
    app.secret_key = auth.secret_key()
    app.config["DRAFTER"] = Drafter(drafter=drafter)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,  # the cookie is not readable from JavaScript
        SESSION_COOKIE_SAMESITE="Lax",
    )

    @app.after_request
    def allow_frontend(response):
        """CORS with credentials, so a separate frontend can hold the session."""
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.route("/api/<path:_unused>", methods=["OPTIONS"])
    def preflight(_unused):
        return ("", 204)

    @app.route("/")
    def index():
        return render_template("index.html", sample_data=synthetic_kb())

    # ------------------------------------------------------------------ auth

    @app.get("/api/session")
    def session_state():
        """What the frontend needs to decide what to show — never the password."""
        return jsonify(
            {
                "staff_authenticated": bool(session.get(auth.SESSION_FLAG)) or not auth.configured(),
                "password_required": auth.configured(),
            }
        )

    @app.post("/api/login")
    def login():
        payload = request.get_json(silent=True) or {}
        if not auth.configured():
            return jsonify({"error": "No desk password is set on this server."}), 400
        if not auth.verify(payload.get("password")):
            return jsonify({"error": "That password was not recognised."}), 401
        session[auth.SESSION_FLAG] = True
        return jsonify({"staff_authenticated": True, "password_required": True})

    @app.post("/api/logout")
    def logout():
        session.pop(auth.SESSION_FLAG, None)
        return jsonify({"staff_authenticated": False, "password_required": auth.configured()})

    # --------------------------------------------------------------- drafting

    @app.post("/api/draft")
    def draft():
        payload = request.get_json(silent=True) or {}
        mode = "student" if payload.get("mode") == "student" else "staff"

        if mode == "staff" and auth.configured() and not session.get(auth.SESSION_FLAG):
            return jsonify({"error": "Staff drafting requires the desk password."}), 401

        text = (payload.get("text") or "").strip()
        if len(text) < 10:
            return jsonify({"error": "Describe the problem in a sentence or two."}), 400

        result = app.config["DRAFTER"].draft(
            text=text,
            notes=(payload.get("notes") or "").strip(),
            mode=mode,
        )
        return jsonify(result)

    return app
