"""Accounts, password hashing and session cookies (stage 4).

A5 in the requirement doc: open registration + Session-Cookie + a fixed admin
account created by an init script; admins only manage the word bank.

Local mode: this is a single-machine self-use app that already had data under
the placeholder user id=1 before accounts existed. Until the user registers,
requests from loopback are served by that account so no progress is lost.
Set LOCAL_MODE=0 in backend/.env to require a login for every request.
"""

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymysql import IntegrityError

from db import connect, execute, one, query, transaction

COOKIE = "ky_session"
SESSION_DAYS = 30
LOCAL_MODE = os.getenv("LOCAL_MODE", "1") != "0"
# The local-mode admin shortcut follows LOCAL_MODE by default so single-machine
# self-use keeps working. Set ALLOW_LOCAL_ADMIN=0 to disable it independently
# (required for any shared deployment, reverse proxy, or --host 0.0.0.0).
ALLOW_LOCAL_ADMIN = os.getenv("ALLOW_LOCAL_ADMIN", "1" if LOCAL_MODE else "0") != "0"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") != "0"
FORWARDED_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
                     "forwarded", "x-real-ip")
USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5-]{2,32}$")

# In-process login throttle: 5 failed attempts / 60s per (ip, username).
# Single-worker deployment is the documented constraint; a shared store is
# only needed if the app is ever run with multiple uvicorn workers.
_login_attempts = {}
_login_lock = threading.Lock()
MAX_FAILS = 5
FAIL_WINDOW = 60

router = APIRouter()


def _throttle_key(username: str, request: Request):
    host = request.client.host if request.client else "?"
    return (host, username)


def _check_throttle(key):
    with _login_lock:
        fails, first = _login_attempts.get(key, (0, 0))
        now = time.monotonic()
        if now - first > FAIL_WINDOW:
            fails, first = 0, now
        if fails >= MAX_FAILS:
            raise HTTPException(status_code=429, detail="尝试次数过多，请 60 秒后再试")
        return fails, first


def _record_failure(key, fails, first):
    with _login_lock:
        _login_attempts[key] = (fails + 1, first or time.monotonic())


def _clear_throttle(key):
    with _login_lock:
        _login_attempts.pop(key, None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    # OWASP (2023) recommends 600k iterations for PBKDF2-HMAC-SHA256.
    # Older hashes store their own rounds, so they still verify correctly.
    rounds = 600_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _local_allowed(request: Request) -> bool:
    # A reverse proxy on the same host makes every peer look like loopback.
    # Forwarded headers are a reliable signal that the request came through a
    # proxy, so such requests must never inherit the trusted "local" identity.
    for header in FORWARDED_HEADERS:
        if request.headers.get(header):
            return False
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1")


def resolve_user(request: Request):
    """Return the current identity. Never trust a user id sent by the browser."""
    token = request.cookies.get(COOKIE)
    if token:
        conn = connect()
        try:
            row = one(
                conn,
                """SELECT s.user_id, s.is_admin, u.username, u.current_book_id
                   FROM `user_session` s LEFT JOIN `user` u ON u.id=s.user_id
                   WHERE s.token=%s AND s.expires_at > NOW() AND s.is_admin=0""",
                (token,),
            )
            admin = one(
                conn,
                """SELECT s.user_id, a.username FROM `user_session` s
                   JOIN `admin` a ON a.id=s.user_id
                   WHERE s.token=%s AND s.expires_at > NOW() AND s.is_admin=1""",
                (token,),
            )
        finally:
            conn.close()
        if row:
            return {"id": row["user_id"], "username": row["username"],
                    "is_admin": 0, "local": False,
                    "book_id": int(row["current_book_id"] or 1)}
        if admin:
            return {"id": admin["user_id"], "username": admin["username"],
                    "is_admin": 1, "local": False, "book_id": 1}
    if LOCAL_MODE and _local_allowed(request):
        # The local placeholder is a real `user` row, so honour its book too.
        conn = connect()
        try:
            local_row = one(conn, "SELECT current_book_id FROM `user` WHERE id=1")
        finally:
            conn.close()
        return {"id": 1, "username": "local", "is_admin": 0, "local": True,
                "book_id": int((local_row or {}).get("current_book_id") or 1)}
    raise HTTPException(status_code=401, detail="请先登录")


def current_user(request: Request):
    return resolve_user(request)


def require_admin(user=Depends(current_user)):
    # Single-machine self-use mode is trusted only when explicitly allowed AND
    # the request is genuinely loopback (see _local_allowed). Real admin rows
    # always pass; local shortcut is off for any shared deployment.
    if not user["is_admin"] and not (ALLOW_LOCAL_ADMIN and user["local"]):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _issue_session(conn, identity_id: int, is_admin: int) -> str:
    execute(conn, "DELETE FROM `user_session` WHERE expires_at <= NOW()")
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(days=SESSION_DAYS)
    execute(
        conn,
        """INSERT INTO `user_session` (`token`,`user_id`,`is_admin`,`expires_at`)
           VALUES (%s,%s,%s,%s)""",
        (token, identity_id, is_admin, expires),
    )
    return token


def _set_cookie(response: Response, token: str):
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", secure=COOKIE_SECURE,
        max_age=SESSION_DAYS * 86400, path="/",
    )


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)


def _claim_legacy_data(conn, new_user_id: int):
    """First real registration inherits the pre-account local progress.

    Only runs once, and only while user id=1 is still the untouched placeholder.
    Moving the rows keeps the user's in-flight 10-word plan intact.
    """
    # Lock the placeholder row first: two concurrent registrations must not
    # both see "no real user yet" and move the same legacy rows twice.
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM `user` WHERE id=1 FOR UPDATE")
    legacy = one(
        conn,
        "SELECT id FROM `user` WHERE id=1 AND username='local' AND password_hash=''",
    )
    # Exclude the account just inserted: it is the FIRST real user iff nobody
    # else has a password yet. FOR UPDATE makes this a locking/current read, so
    # the second concurrent registration sees the first one's committed row
    # even though its own transaction snapshot predates that commit.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM `user` WHERE password_hash<>'' AND id<>%s FOR UPDATE",
            (new_user_id,),
        )
        real = cur.fetchone()["c"]
    if not legacy or real != 0:
        return False
    # user_session is NOT included: an admin row may also have id=1, and the
    # placeholder account never had a session in the first place.
    for table in ("daily_plan", "progress", "answer_log", "article",
                  "article_word", "question"):
        execute(conn, f"UPDATE `{table}` SET user_id=%s WHERE user_id=1",
                (new_user_id,))
    return True


@router.get("/api/me")
def me(user=Depends(current_user)):
    return {
        "user": {
            "id": user["id"], "username": user["username"],
            "is_admin": bool(user["is_admin"]), "local": user["local"],
        },
        "local_mode": LOCAL_MODE,
    }


@router.post("/api/auth/register")
def register(req: Credentials, response: Response, request: Request):
    if not USERNAME_RE.fullmatch(req.username):
        raise HTTPException(status_code=400, detail="用户名只支持中英文、数字、下划线和连字符")
    key = _throttle_key(req.username, request)
    fails, first = _check_throttle(key)
    # 200k-round PBKDF2 is CPU-heavy: compute it BEFORE opening the transaction
    # so the write lock is held as briefly as possible.
    hashed = hash_password(req.password)
    try:
        with transaction() as conn:
            if one(conn, "SELECT id FROM `user` WHERE username=%s", (req.username,)):
                raise HTTPException(status_code=409, detail="这个用户名已被注册")
            try:
                execute(
                    conn,
                    "INSERT INTO `user` (`username`,`password_hash`) VALUES (%s,%s)",
                    (req.username, hashed),
                )
            except IntegrityError:
                # Concurrent registration of the same name: the unique index is
                # the real guard. Translate the DB error into the same 409.
                raise HTTPException(status_code=409, detail="这个用户名已被注册")
            uid = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
            inherited = _claim_legacy_data(conn, uid)
            token = _issue_session(conn, uid, 0)
    except HTTPException:
        _record_failure(key, fails, first)
        raise
    _clear_throttle(key)
    _set_cookie(response, token)
    return {"ok": True, "user": {"id": uid, "username": req.username,
                                  "is_admin": False, "local": False},
            "inherited_local_progress": inherited}


@router.post("/api/auth/login")
def login(req: Credentials, response: Response, request: Request):
    key = _throttle_key(req.username, request)
    fails, first = _check_throttle(key)
    with transaction() as conn:
        row = one(conn, "SELECT * FROM `user` WHERE username=%s", (req.username,))
        if not row or not row["password_hash"] or not verify_password(req.password, row["password_hash"]):
            _record_failure(key, fails, first)
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        token = _issue_session(conn, row["id"], 0)
    _clear_throttle(key)
    _set_cookie(response, token)
    return {"ok": True, "user": {"id": row["id"], "username": row["username"],
                                  "is_admin": False, "local": False}}


@router.post("/api/auth/admin-login")
def admin_login(req: Credentials, response: Response, request: Request):
    key = _throttle_key("admin:" + req.username, request)
    fails, first = _check_throttle(key)
    with transaction() as conn:
        row = one(conn, "SELECT * FROM `admin` WHERE username=%s", (req.username,))
        if not row or not verify_password(req.password, row["password_hash"]):
            _record_failure(key, fails, first)
            raise HTTPException(status_code=401, detail="管理员账号或密码不正确")
        token = _issue_session(conn, row["id"], 1)
    _clear_throttle(key)
    _set_cookie(response, token)
    return {"ok": True, "user": {"id": row["id"], "username": row["username"],
                                  "is_admin": True, "local": False}}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with transaction() as conn:
            execute(conn, "DELETE FROM `user_session` WHERE token=%s", (token,))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/users")
def list_users(user=Depends(require_admin)):
    """Admin-only: enumerate usernames. Never exposed to anonymous callers."""
    conn = connect()
    try:
        rows = query(conn, "SELECT id, username FROM `user` ORDER BY id")
        return {"users": rows}
    finally:
        conn.close()
