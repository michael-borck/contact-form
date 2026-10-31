import os
import tempfile

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["TURNSTILE_SECRET"] = ""
os.environ["RATE_LIMIT_PER_HOUR"] = "100"

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_missing_fields():
    with TestClient(app) as client:
        response = client.post("/contact", data={})
        assert response.status_code == 422


def test_honeypot_redirects_without_sending():
    with TestClient(app) as client:
        response = client.post(
            "/contact",
            data={
                "name": "Bot",
                "email": "bot@example.com",
                "situation": "spam",
                "website": "https://spam.example",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "https://borck.consulting/thanks/"


def test_invalid_email():
    with TestClient(app) as client:
        response = client.post(
            "/contact",
            data={"name": "Test", "email": "not-an-email", "situation": "Hello"},
        )
        assert response.status_code == 400
