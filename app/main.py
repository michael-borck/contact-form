import hashlib
import html
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import aiosqlite
import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger("contact-form")

DB_PATH = Path(os.getenv("DATABASE_PATH", "data/contact.db"))
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
TO_EMAIL = os.getenv("TO_EMAIL", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "")
SUCCESS_REDIRECT = os.getenv("SUCCESS_REDIRECT", "https://borck.consulting/thanks/")
IP_HASH_SALT = os.getenv("IP_HASH_SALT", "change-me")
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "5"))
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "5000"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hits: dict[str, deque[float]] = defaultdict(deque)
_hits_lock = Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                firm TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                ip_hash TEXT NOT NULL,
                user_agent TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        await db.commit()
    yield


app = FastAPI(title="borck.consulting contact form", lifespan=lifespan)


def client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def hash_ip(ip: str) -> str:
    return hashlib.sha256((IP_HASH_SALT + ip).encode()).hexdigest()[:16]


def allowed(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_HOUR:
            return False
        q.append(now)
        if len(_hits) > 10000:
            for key in list(_hits)[:5000]:
                _hits.pop(key, None)
        return True


def clean_header(value: str, limit: int = 200) -> str:
    return " ".join(value.split())[:limit]


async def verify_turnstile(token: str, ip: str) -> bool:
    if not TURNSTILE_SECRET:
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip},
            )
            response.raise_for_status()
            return bool(response.json().get("success"))
    except Exception:
        logger.exception("turnstile verification failed")
        return False


async def send_email(name: str, firm: str, email: str, message: str) -> None:
    if not RESEND_API_KEY or not TO_EMAIL or not FROM_EMAIL:
        raise RuntimeError("email is not configured")

    subject = f"Contact form: {clean_header(name)}"
    if firm:
        subject += f" ({clean_header(firm)})"

    text = f"Name: {name}\nFirm: {firm}\nEmail: {email}\n\n{message}"
    body = f"""<!doctype html>
<html>
  <body style="font-family: system-ui, sans-serif; line-height: 1.6;">
    <h2>New contact form submission</h2>
    <p><strong>Name:</strong> {html.escape(name)}</p>
    <p><strong>Firm:</strong> {html.escape(firm) or "Not provided"}</p>
    <p><strong>Email:</strong> {html.escape(email)}</p>
    <p><strong>Message:</strong></p>
    <p style="white-space: pre-wrap;">{html.escape(message)}</p>
  </body>
</html>"""

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": FROM_EMAIL,
                "to": [TO_EMAIL],
                "reply_to": email,
                "subject": subject,
                "text": text,
                "html": body,
            },
        )
        response.raise_for_status()


async def record(
    name: str,
    firm: str,
    email: str,
    message: str,
    ip_hash: str,
    user_agent: str,
    status: str,
) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO submissions
                (created_at, name, firm, email, message, ip_hash, user_agent, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                name[:200],
                firm[:200],
                email[:320],
                message[:MAX_MESSAGE_LENGTH],
                ip_hash,
                user_agent[:500],
                status,
            ),
        )
        await db.commit()


def error_page(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Contact form error</title>
  </head>
  <body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 0 1.5rem; line-height: 1.6;">
    <h1>That did not send</h1>
    <p>{html.escape(message)}</p>
    <p>
      <a href="mailto:michael@borck.dev">Email michael@borck.dev</a>
      or <a href="https://borck.consulting/contact/">go back to the contact page</a>.
    </p>
  </body>
</html>""",
        status_code=status_code,
    )


@app.get("/")
async def root():
    return JSONResponse({"service": "borck.consulting contact form"})


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/contact", response_model=None)
async def contact(
    request: Request,
    name: str = Form(..., min_length=1, max_length=200),
    firm: str = Form("", max_length=200),
    email: str = Form(..., min_length=3, max_length=320),
    situation: str = Form("", max_length=MAX_MESSAGE_LENGTH),
    website: str = Form(""),
    turnstile_token: str = Form("", alias="cf-turnstile-response"),
):
    ip = client_ip(request)
    ip_hash = hash_ip(ip)
    user_agent = request.headers.get("user-agent", "")[:500]

    if not allowed(ip):
        return error_page("Too many submissions from this address. Try again later.", 429)

    if website:
        await record(name, firm, email, situation, ip_hash, user_agent, "spam")
        return RedirectResponse(SUCCESS_REDIRECT, status_code=303)

    if not EMAIL_RE.match(email):
        return error_page("Please check the email address and try again.", 400)

    if not await verify_turnstile(turnstile_token, ip):
        return error_page("The verification check failed. Please try again.", 400)

    try:
        await send_email(name, firm, email, situation)
    except Exception:
        logger.exception("failed to send contact form email")
        await record(name, firm, email, situation, ip_hash, user_agent, "error")
        return error_page("The form could not send. Please email instead.", 502)

    await record(name, firm, email, situation, ip_hash, user_agent, "sent")
    return RedirectResponse(SUCCESS_REDIRECT, status_code=303)
