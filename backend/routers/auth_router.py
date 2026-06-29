"""Auth routes: register, email verification, login, refresh, logout.

Mounted under /auth (see main.py). These routes are deliberately excluded from
the app-level require_auth gate so unauthenticated users can sign in.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth
import database as db

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


class Credentials(BaseModel):
    email: str
    password: str


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", status_code=201)
async def register(body: Credentials, db=Depends(auth.get_db)):
    email = _normalize_email(body.email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
        )
    if db.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = db.create_user(email, auth.hash_password(body.password))

    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=24)
    ).strftime("%Y-%m-%d %H:%M:%S")
    db.create_email_verification(user_id, token, expires_at)

    await auth.send_verification_email(email, token)
    return {"message": "Check your email to verify your account"}


@router.get("/verify-email")
async def verify_email(token: str, db=Depends(auth.get_db)):
    row = db.get_valid_email_verification(token)
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    db.mark_email_verification_used(row["id"])
    db.mark_user_verified(row["user_id"])
    return {"message": "Email verified. You can now log in."}


# One generic failure message so we never leak which of wrong-email / wrong-password
# / unverified happened.
_INVALID = HTTPException(
    status_code=401, detail="Invalid credentials or unverified account"
)


@router.post("/login")
async def login(body: Credentials, response: Response, db=Depends(auth.get_db)):
    email = _normalize_email(body.email)
    user = db.get_user_by_email(email)
    if user is None:
        raise _INVALID
    if not auth.verify_password(body.password, user["hashed_password"]):
        raise _INVALID
    if not user["is_verified"]:
        raise _INVALID

    access = auth.create_access_token(user["id"])
    refresh = auth.create_refresh_token(user["id"])
    auth.set_access_cookie(response, access)
    auth.set_refresh_cookie(response, refresh)
    return {"message": "Logged in"}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db=Depends(auth.get_db)):
    raw = request.cookies.get(auth.REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    row = db.get_valid_refresh_token(auth._hash_token(raw))
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    access = auth.create_access_token(row["user_id"])
    auth.set_access_cookie(response, access)
    return {"message": "Token refreshed"}


@router.post("/logout")
async def logout(request: Request, response: Response, db=Depends(auth.get_db)):
    raw = request.cookies.get(auth.REFRESH_COOKIE)
    if raw:
        db.revoke_refresh_token(auth._hash_token(raw))
    auth.clear_auth_cookies(response)
    return {"message": "Logged out"}
