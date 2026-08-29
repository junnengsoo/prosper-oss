from __future__ import annotations

from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
import app.auth as auth_module
from app.database.connection import Base, get_session
from app.main import app
from app.database.seed import seed_all


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with SessionLocal() as db:
            seed_all(db)
            yield db
    finally:
        engine.dispose()


def test_single_user_cookie_auth_flow(session, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-password")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "3600")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)

        missing = client.get("/api/me")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Authentication required"

        incorrect = client.post("/api/auth/login", json={"password": "wrong-password"})
        assert incorrect.status_code == 401

        logged_in = client.post("/api/auth/login", json={"password": "correct-password"})
        assert logged_in.status_code == 200
        assert logged_in.json() == {"authenticated": True, "email": None}
        assert "HttpOnly" in logged_in.headers["set-cookie"]
        assert "SameSite=lax" in logged_in.headers["set-cookie"]

        session_status = client.get("/api/auth/session")
        assert session_status.status_code == 200
        assert session_status.json()["authenticated"] is True

        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["auth_user_id"] == "prosper-owner"
        assert me.json() == {"auth_user_id": "prosper-owner", "email": None}

        logged_out = client.post("/api/auth/logout")
        assert logged_out.status_code == 200
        assert logged_out.json()["authenticated"] is False
        assert client.get("/api/me").status_code == 401
    finally:
        app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("GET", "/demo/conversations/1"),
        ("POST", "/demo/conversations/1/close"),
        ("POST", "/demo/contacts/1/unpause"),
        ("GET", "/api/playbooks"),
        ("POST", "/api/contacts/1/cancel"),
        ("GET", "/api/config/export"),
        ("POST", "/api/conversations/1/close"),
        ("POST", "/api/conversations/1/start-new-enquiry"),
        ("PATCH", "/api/conversations/1/stage"),
        ("POST", "/api/conversations/1/run-initial-pipeline"),
        ("POST", "/api/conversations/1/run-next"),
        ("POST", "/api/conversations/1/run-rental-listing-matching"),
    ],
)
def test_removed_operator_surfaces_are_not_routed(session, monkeypatch, method, path):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)

        response = client.request(method, path)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        ("/api/bridge/chat-state?chat_jid=tenant@s.whatsapp.net", "GET", None),
        (
            "/api/bridge/inbound",
            "POST",
            {
                "chat_jid": "tenant@s.whatsapp.net",
                "sender_jid": "tenant@s.whatsapp.net",
                "message_id": "message-1",
                "timestamp_ms": 1_700_000_000_000,
                "from_me": False,
                "text": "Hi is this available?",
                "raw_type": "conversation",
            },
        ),
        ("/api/bridge/inbound-batch", "POST", {"messages": []}),
    ],
)
def test_bridge_callback_routes_require_configured_bridge_token(session, monkeypatch, path, method, body):
    monkeypatch.setenv("PROSPER_BRIDGE_TOKEN", "backend-secret")
    monkeypatch.delenv("WHATSAPP_PA_BRIDGE_TOKEN", raising=False)
    get_settings.cache_clear()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)

        missing = client.request(method, path, json=body)
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Invalid bridge token"

        invalid = client.request(method, path, headers={"x-whatsapp-bridge-token": "wrong-token"}, json=body)
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "Invalid bridge token"

        valid = client.request(method, path, headers={"x-whatsapp-bridge-token": "backend-secret"}, json=body)
        assert valid.status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()


def test_session_tokens_reject_tampering_and_expiration(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-password")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "60")
    get_settings.cache_clear()
    try:
        token = auth_module.create_session_token()
        payload, signature = token.rsplit(".", 1)
        assert auth_module.verify_session_token(f"{payload}.tampered-{signature}") is False
        now = int(time.time())
        monkeypatch.setattr(auth_module, "time", SimpleNamespace(time=lambda: now + 61))
        assert auth_module.verify_session_token(token) is False
    finally:
        get_settings.cache_clear()
