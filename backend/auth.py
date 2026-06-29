"""JWT auth + email verification for the Stock Tracker API.

Cookie-based auth: a short-lived access token (15 min) and a long-lived refresh
token (7 days) are both stored as HttpOnly cookies. The access token is a signed
JWT; the refresh token is an opaque random string whose SHA-256 hash is persisted
(so a DB leak can't be replayed as a live token).

Required environment variables (set in backend/.env locally and in Railway):

    JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
    RESEND_API_KEY=...        # Resend API key for transactional email
    RESEND_DOMAIN=...         # the domain verified with Resend (from = noreply@<domain>)
    FRONTEND_URL=...          # e.g. https://your-app.vercel.app — used in the verify link
    COOKIE_SECURE=true        # optional; default true. Set false only for plain-HTTP local dev.

Cookies are issued with SameSite=None; Secure because the frontend (Vercel) and the
API (Railway) live on different sites — Lax cookies would never be sent on a
cross-site fetch. That also requires CORS allow_credentials=True (see main.py).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from passlib.context import CryptContext

import database as db

# ── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. Generate one with:\n"
        '    python -c "import secrets; print(secrets.token_hex(32))"'
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Cross-site cookies (Vercel frontend → Railway API) require SameSite=None + Secure.
# For plain-HTTP local dev set COOKIE_SECURE=false: browsers reject SameSite=None
# without Secure, so we fall back to Lax — which is fine locally because the dev
# frontend (localhost:3000) and API (localhost:8000) are same-site.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"
COOKIE_SAMESITE = "none" if COOKIE_SECURE else "lax"
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Password hashing ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


# ── Tokens ───────────────────────────────────────────────────────────────────
def create_access_token(user_id: int) -> str:
    """Short-lived HS256 JWT with sub=str(user_id), type='access'."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token(user_id: int) -> str:
    """Generate an opaque refresh token, persist its SHA-256 hash (7-day expiry),
    and return the raw token (to be set as a cookie — only the hash is stored)."""
    raw = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    db.store_refresh_token(user_id, _hash_token(raw), expires_at)
    return raw


# ── Cookie helpers ───────────────────────────────────────────────────────────
def set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for key in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.set_cookie(
            key=key,
            value="",
            max_age=0,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            path="/",
        )


# ── Dependencies ─────────────────────────────────────────────────────────────
def get_db():
    """Inject the database module (all SQL lives in database.py)."""
    return db


def get_current_user(request: Request, db=Depends(get_db)):
    """Resolve the authenticated user from the access_token cookie.

    Raises 401 if the cookie is missing, malformed, expired, not an access token,
    or points at a user that no longer exists.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.get_user_by_id(int(sub))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


# Paths that must remain reachable without authentication.
_PUBLIC_PREFIXES = ("/auth/",)
_PUBLIC_EXACT = {"/health"}


def require_auth(request: Request, db=Depends(get_db)):
    """Global gate applied to every request (FastAPI app-level dependency).

    Lets CORS preflight, /health and the /auth/* routes through untouched; every
    other endpoint requires a valid access token via get_current_user.
    """
    if request.method == "OPTIONS":
        return None
    path = request.url.path
    if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES):
        return None
    return get_current_user(request, db)


# ── Email ────────────────────────────────────────────────────────────────────
async def send_verification_email(email: str, token: str) -> None:
    """Send the verification link via the Resend API.

    No-ops (logs) if RESEND_API_KEY is unset so local dev doesn't require email —
    the token is also printed so you can verify by hand.
    """
    api_key = os.getenv("RESEND_API_KEY")
    domain = os.getenv("RESEND_DOMAIN")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    verify_link = f"{frontend_url}/verify-email?token={token}"

    if not api_key or not domain:
        print(
            f"[auth] RESEND not configured — verification link for {email}: {verify_link}"
        )
        return

    html = (
        "<div style=\"font-family:system-ui,sans-serif;line-height:1.5\">"
        "<h2>Verify your email</h2>"
        "<p>Welcome to Stock Tracker. Confirm your address to activate your account:</p>"
        f'<p><a href="{verify_link}" '
        'style="display:inline-block;padding:10px 18px;background:#18181b;color:#fff;'
        'border-radius:8px;text-decoration:none">Verify email</a></p>'
        f'<p style="color:#71717a;font-size:13px">Or paste this link: {verify_link}</p>'
        "</div>"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": f"noreply@{domain}",
                "to": [email],
                "subject": "Verify your email",
                "html": html,
            },
        )
        if resp.status_code >= 400:
            print(f"[auth] Resend error {resp.status_code}: {resp.text}")
            raise HTTPException(status_code=502, detail="Failed to send verification email")
