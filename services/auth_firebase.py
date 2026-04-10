import os
from functools import wraps
from typing import Any, Callable, Optional

from flask import g, jsonify, request

try:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token
except ImportError:  # pragma: no cover
    google_requests = None
    google_id_token = None


def _auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED", "false").lower().strip() in ("1", "true", "yes")


def _dev_user_id() -> str:
    return os.getenv("DEV_USER_ID", "dev-local")


def verify_firebase_id_token(id_token_str: str) -> Optional[str]:
    """
    Verify Firebase ID token; return Firebase uid or None.
    Requires GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_PROJECT_ID + web client.
    Uses google.oauth2.id_token.verify_firebase_token.
    """
    if not id_token_str or not google_id_token or not google_requests:
        return None
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    if not project_id:
        return None
    try:
        req = google_requests.Request()
        decoded = google_id_token.verify_firebase_token(id_token_str, req, audience=project_id)
        uid = decoded.get("uid") or decoded.get("sub")
        return str(uid) if uid else None
    except Exception:
        return None


def get_bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth[7:].strip() or None


def resolve_user_id() -> Optional[str]:
    if _auth_disabled():
        return _dev_user_id()
    tok = get_bearer_token()
    if not tok:
        return None
    uid = verify_firebase_id_token(tok)
    return uid


def require_auth(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any):
        uid = resolve_user_id()
        if not uid:
            return jsonify({"error": "Unauthorized", "code": "AUTH_REQUIRED"}), 401
        g.user_id = uid
        return f(*args, **kwargs)

    return wrapper


def register_auth_context(app) -> None:
    @app.before_request
    def _attach_user():
        g.user_id = None
        path = request.path or ""
        if request.method == "OPTIONS":
            return
        if path in ("/health",):
            return
        if path.startswith("/static"):
            return
        if _auth_disabled():
            g.user_id = _dev_user_id()
            return
        tok = get_bearer_token()
        if not tok:
            return jsonify({"error": "Unauthorized", "code": "AUTH_REQUIRED"}), 401
        uid = verify_firebase_id_token(tok)
        if not uid:
            return jsonify({"error": "Invalid or expired token", "code": "AUTH_INVALID"}), 401
        g.user_id = uid
