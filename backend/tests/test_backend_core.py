from __future__ import annotations

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions import execute_outbound_action_plan, plan_outbound_actions
from app.auth import CurrentUser, RequestContext, current_user_from_session, resolve_single_workspace_context, resolve_workspace_context
from app.config import get_settings
from app.db import Base
from app.models import Message, Property, PropertyMedia, PropertyPlaybook, StageRun, SwingCandidate, Workspace, WorkspaceMember, WhatsappAccount
from app.pipeline import route_stored_conversation_after_inbound
from app.playbooks import list_property_playbooks, upsert_property_playbook
from app.schemas import BridgeInboundMessage, PropertyIn, PropertyMediaIn, PropertyPlaybookIn, SwingCandidateIn
from app.seed import seed_all
from app.services import (
    append_message,
    delete_properties,
    get_or_create_active_conversation,
    get_or_create_contact,
    handle_bridge_inbound,
    post_bridge_send_with_retry,
    update_config,
    upsert_property,
    upsert_property_media,
    upsert_swing_candidate,
)
from app.tenant import DEFAULT_WHATSAPP_ACCOUNT_ID, DEFAULT_WORKSPACE_ID, WorkspaceScope, reset_workspace_scope, set_current_workspace_scope, workspace_scope


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    token = set_current_workspace_scope(WorkspaceScope())
    try:
        with SessionLocal() as db:
            seed_all(db)
            yield db
    finally:
        reset_workspace_scope(token)
        engine.dispose()


def add_property(
    session,
    property_id: str = "RTF-001",
    *,
    property_name: str = "301C Punggol Central",
    status: str = "available",
    tenant_notes: str = "Take note of duplicate listings.",
) -> Property:
    return upsert_property(
        session,
        PropertyIn(
            property_id=property_id,
            property_name=property_name,
            status=status,
            property_type="HDB",
            bedrooms=3,
            bathrooms=2,
            asking_rent=3300,
            available_from="Immediate",
            full_address=f"{property_name} #03-752",
            property_url=f"https://example.com/{property_id}",
            landlord_profile_requirements="Max 4 pax",
            tenant_facing_caveats=tenant_notes,
        ),
    )


def add_fake_conversation(session, *, chat_jid: str = "fake-tenant@s.whatsapp.net", text: str = "Hi 301C"):
    contact = get_or_create_contact(session, chat_jid, "Tenant")
    conversation = get_or_create_active_conversation(session, contact, "fake_chat")
    append_message(
        session,
        conversation,
        chat_jid,
        f"inbound-{conversation.id}-1",
        text,
        1_000,
        "inbound",
        "fake_chat",
        chat_jid,
        "fake_text",
    )
    return conversation


def add_whatsapp_conversation(session, *, chat_jid: str = "tenant@s.whatsapp.net", text: str = "Hi 301C"):
    contact = get_or_create_contact(session, chat_jid, "Tenant")
    conversation = get_or_create_active_conversation(session, contact, "whatsapp")
    append_message(
        session,
        conversation,
        chat_jid,
        f"inbound-{conversation.id}-1",
        text,
        1_000,
        "inbound",
        "whatsapp",
        chat_jid,
        "text",
    )
    return conversation


def outbound_texts(session, conversation_id: int) -> list[str]:
    return [
        message.text
        for message in session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.direction == "outbound")
            .order_by(Message.timestamp_ms, Message.id)
        ).all()
    ]


def matched_unit_result(property_id: str = "RTF-001", *, profile_status: str = "no_profile_detected") -> dict:
    return {
        "unit_matching": {
            "match_status": "matched",
            "matched_property_status": "available",
            "profile_info_status": profile_status,
            "matched_properties": [{"property_id": property_id, "property_name": "301C Punggol Central"}],
            "reason": "matched test property",
        }
    }


def test_playbook_crud_and_workspace_scoping(session):
    add_property(session, "RTF-001")
    playbook = upsert_property_playbook(
        session,
        "RTF-001",
        PropertyPlaybookIn(
            initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}],
            enabled=True,
        ),
    )
    session.commit()

    assert playbook.property_id == "RTF-001"
    assert playbook.initial_reply_blocks == [{"type": "message", "text": "Hi {property_name}"}]
    assert "RTF-001" in {row.property_id for row in list_property_playbooks(session)}

    session.add(Workspace(id="other", slug="other", name="Other", status="active"))
    session.add(
        WhatsappAccount(
            id="other-whatsapp",
            workspace_id="other",
            account_key="other",
            display_name="Other WA",
            status="active",
        )
    )
    session.commit()
    with workspace_scope(WorkspaceScope("other", "other-whatsapp")):
        add_property(session, "RTF-002", property_name="Other Property")
        other_playbook = upsert_property_playbook(
            session,
            "RTF-002",
            PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Other"}]),
        )
        session.commit()
        assert [row.property_id for row in list_property_playbooks(session)] == ["RTF-002"]

    assert session.get(PropertyPlaybook, other_playbook.id).workspace_id == "other"
    assert "RTF-001" in {row.property_id for row in list_property_playbooks(session)}


def test_auth_required_rejects_missing_session_cookie(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ACCESS_PASSWORD", "test-password")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as error:
            current_user_from_session(None)
    finally:
        get_settings.cache_clear()
    assert error.value.status_code == 401
    assert "Authentication required" in error.value.detail


def test_workspace_context_rejects_cross_workspace_access(session):
    session.add(Workspace(id="tenant-a", slug="tenant-a", name="Tenant A", status="active"))
    session.add(Workspace(id="tenant-b", slug="tenant-b", name="Tenant B", status="active"))
    session.add(
        WhatsappAccount(
            id="tenant-a-whatsapp",
            workspace_id="tenant-a",
            account_key="tenant-a",
            display_name="Tenant A WhatsApp",
            status="active",
        )
    )
    session.add(
        WhatsappAccount(
            id="tenant-b-whatsapp",
            workspace_id="tenant-b",
            account_key="tenant-b",
            display_name="Tenant B WhatsApp",
            status="active",
        )
    )
    session.add(
        WorkspaceMember(
            workspace_id="tenant-a",
            auth_user_id="user-a",
            email="a@example.com",
            role="owner",
            status="active",
        )
    )
    session.add(
        WorkspaceMember(
            workspace_id="tenant-b",
            auth_user_id="user-b",
            email="b@example.com",
            role="owner",
            status="active",
        )
    )
    session.commit()

    user_a = CurrentUser(auth_user_id="user-a", email="a@example.com")
    context = resolve_workspace_context(session, user_a, "tenant-a")

    assert context.scope.workspace_id == "tenant-a"
    assert context.scope.whatsapp_account_id == "tenant-a-whatsapp"

    with pytest.raises(HTTPException) as error:
        resolve_workspace_context(session, user_a, "tenant-b")
    assert error.value.status_code == 403
    assert "No active workspace membership" in error.value.detail


def test_workspace_context_requires_workspace_header_for_multi_workspace_user(session):
    for workspace_id in ("tenant-a", "tenant-b"):
        session.add(Workspace(id=workspace_id, slug=workspace_id, name=workspace_id, status="active"))
        session.add(
            WhatsappAccount(
                id=f"{workspace_id}-whatsapp",
                workspace_id=workspace_id,
                account_key=workspace_id,
                display_name=f"{workspace_id} WhatsApp",
                status="active",
            )
        )
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                auth_user_id="multi-user",
                email="multi@example.com",
                role="owner",
                status="active",
            )
        )
    session.commit()

    user = CurrentUser(auth_user_id="multi-user", email="multi@example.com")

    with pytest.raises(HTTPException) as error:
        resolve_single_workspace_context(session, user)
    assert error.value.status_code == 403
    assert "x-workspace-id is required" in error.value.detail

    context = resolve_workspace_context(session, user, "tenant-b")
    assert context.scope.workspace_id == "tenant-b"
    assert context.scope.whatsapp_account_id == "tenant-b-whatsapp"


def test_workspace_scoped_property_and_playbook_queries_do_not_leak(session):
    session.add(Workspace(id="tenant-a", slug="tenant-a", name="Tenant A", status="active"))
    session.add(Workspace(id="tenant-b", slug="tenant-b", name="Tenant B", status="active"))
    session.add(WhatsappAccount(id="tenant-a-whatsapp", workspace_id="tenant-a", account_key="a", display_name="A", status="active"))
    session.add(WhatsappAccount(id="tenant-b-whatsapp", workspace_id="tenant-b", account_key="b", display_name="B", status="active"))
    session.commit()

    with workspace_scope(WorkspaceScope("tenant-a", "tenant-a-whatsapp")):
        add_property(session, "RTF-A", property_name="Tenant A Property")
        upsert_property_playbook(session, "RTF-A", PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "A"}]))
    with workspace_scope(WorkspaceScope("tenant-b", "tenant-b-whatsapp")):
        add_property(session, "RTF-B", property_name="Tenant B Property")
        upsert_property_playbook(session, "RTF-B", PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "B"}]))
    session.commit()

    with workspace_scope(WorkspaceScope("tenant-a", "tenant-a-whatsapp")):
        property_ids = {
            row.property_id
            for row in session.scalars(select(Property).where(Property.workspace_id == "tenant-a")).all()
        }
        playbook_ids = {row.property_id for row in list_property_playbooks(session)}
    assert property_ids == {"RTF-A"}
    assert playbook_ids == {"RTF-A"}

    with workspace_scope(WorkspaceScope("tenant-b", "tenant-b-whatsapp")):
        property_ids = {
            row.property_id
            for row in session.scalars(select(Property).where(Property.workspace_id == "tenant-b")).all()
        }
        playbook_ids = {row.property_id for row in list_property_playbooks(session)}
    assert property_ids == {"RTF-B"}
    assert playbook_ids == {"RTF-B"}


def test_dashboard_api_auth_and_workspace_scoping(session, monkeypatch):
    from fastapi import Header
    from fastapi.testclient import TestClient
    import app.auth as auth_module
    import app.main as main_module

    session.add(Workspace(id="tenant-a", slug="tenant-a", name="Tenant A", status="active"))
    session.add(Workspace(id="tenant-b", slug="tenant-b", name="Tenant B", status="active"))
    session.add(WhatsappAccount(id="tenant-a-whatsapp", workspace_id="tenant-a", account_key="a", display_name="A", status="active"))
    session.add(WhatsappAccount(id="tenant-b-whatsapp", workspace_id="tenant-b", account_key="b", display_name="B", status="active"))
    session.add(WorkspaceMember(workspace_id="tenant-a", auth_user_id="user-a", email="a@example.com", role="owner", status="active"))
    session.add(WorkspaceMember(workspace_id="tenant-b", auth_user_id="user-b", email="b@example.com", role="owner", status="active"))
    session.commit()

    with workspace_scope(WorkspaceScope("tenant-a", "tenant-a-whatsapp")):
        add_property(session, "RTF-A", property_name="Tenant A Property")
    with workspace_scope(WorkspaceScope("tenant-b", "tenant-b-whatsapp")):
        add_property(session, "RTF-B", property_name="Tenant B Property")
    session.commit()

    def override_session():
        yield session

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    async def override_dashboard_context(
        authorization: str | None = Header(default=None),
        x_workspace_id: str | None = Header(default=None),
    ):
        if not authorization:
            raise HTTPException(status_code=401, detail="Authentication required")
        token = authorization.removeprefix("Bearer ")
        user_map = {
            "user-a-token": CurrentUser(auth_user_id="user-a", email="a@example.com"),
            "user-b-token": CurrentUser(auth_user_id="user-b", email="b@example.com"),
        }
        user = user_map.get(token, CurrentUser(auth_user_id="unknown", email="unknown@example.com"))
        context = auth_module.resolve_workspace_context(session, user, x_workspace_id)
        token = set_current_workspace_scope(context.scope)
        try:
            yield context
        finally:
            reset_workspace_scope(token)

    main_module.app.dependency_overrides[main_module.get_session] = override_session
    main_module.app.dependency_overrides[main_module.dashboard_context_dependency] = override_dashboard_context
    try:
        client = TestClient(main_module.app)

        missing = client.get("/api/me")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Authentication required"

        allowed = client.get(
            "/api/properties",
            headers={"Authorization": "Bearer user-a-token", "x-workspace-id": "tenant-a"},
        )
        assert allowed.status_code == 200
        assert [row["property_id"] for row in allowed.json()] == ["RTF-A"]

        forbidden = client.get(
            "/api/properties",
            headers={"Authorization": "Bearer user-a-token", "x-workspace-id": "tenant-b"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "No active workspace membership"

        other_user = client.get(
            "/api/properties",
            headers={"Authorization": "Bearer user-b-token", "x-workspace-id": "tenant-b"},
        )
        assert other_user.status_code == 200
        assert [row["property_id"] for row in other_user.json()] == ["RTF-B"]
    finally:
        main_module.app.dependency_overrides.pop(main_module.get_session, None)
        main_module.app.dependency_overrides.pop(main_module.dashboard_context_dependency, None)
        get_settings.cache_clear()


def test_delete_property_removes_config_and_preserves_history(session):
    property_ = add_property(session, "RTF-001")
    other = add_property(session, "RTF-002", property_name="185D Rivervale Crescent")
    upsert_property_media(session, property_.property_id, PropertyMediaIn(media_type="photo", file_path="/tmp/301c.jpg"))
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    upsert_swing_candidate(
        session,
        SwingCandidateIn(source_property_id=property_.property_id, candidate_property_id=other.property_id),
    )
    upsert_swing_candidate(
        session,
        SwingCandidateIn(source_property_id=other.property_id, candidate_property_id=property_.property_id),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = property_.property_id
    stage_run = StageRun(
        conversation_id=conversation.id,
        stage="unit_matching",
        input_snapshot="input",
        output_json='{"matched": true}',
        status="success",
    )
    session.add(stage_run)
    session.commit()
    message_id = session.scalar(select(Message.id).where(Message.conversation_id == conversation.id))
    stage_run_id = stage_run.id

    summary = delete_properties(session, [property_.property_id])
    session.commit()

    assert summary == {
        "deleted_property_ids": ["RTF-001"],
        "deleted_counts": {
            "properties": 1,
            "media": 1,
            "playbooks": 1,
            "swing_candidates": 2,
        },
    }
    assert session.scalar(select(Property).where(Property.property_id == property_.property_id)) is None
    assert session.scalar(select(PropertyMedia).where(PropertyMedia.property_id == property_.property_id)) is None
    assert session.scalar(select(PropertyPlaybook).where(PropertyPlaybook.property_id == property_.property_id)) is None
    assert session.scalars(select(SwingCandidate)).all() == []
    assert session.get(Message, message_id) is not None
    assert session.get(StageRun, stage_run_id) is not None
    assert session.get(type(conversation), conversation.id).matched_property_id == property_.property_id


def test_bulk_delete_properties_is_all_or_nothing(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_media(session, property_.property_id, PropertyMediaIn(media_type="photo", file_path="/tmp/301c.jpg"))
    session.commit()

    with pytest.raises(ValueError, match="Property not found: RTF-MISSING"):
        delete_properties(session, [property_.property_id, "RTF-MISSING"])
    session.rollback()

    assert session.scalar(select(Property).where(Property.property_id == property_.property_id)) is not None
    assert session.scalar(select(PropertyMedia).where(PropertyMedia.property_id == property_.property_id)) is not None


def test_delete_property_is_workspace_scoped(session):
    session.add(Workspace(id="other", slug="other", name="Other", status="active"))
    session.add(WhatsappAccount(id="other-whatsapp", workspace_id="other", account_key="other", display_name="Other", status="active"))
    session.commit()

    add_property(session, "RTF-001")
    with workspace_scope(WorkspaceScope("other", "other-whatsapp")):
        add_property(session, "RTF-001", property_name="Other Workspace Property")
    session.commit()

    delete_properties(session, ["RTF-001"])
    session.commit()

    assert session.scalar(select(Property).where(Property.workspace_id == DEFAULT_WORKSPACE_ID, Property.property_id == "RTF-001")) is None
    assert session.scalar(select(Property).where(Property.workspace_id == "other", Property.property_id == "RTF-001")) is not None

    with workspace_scope(WorkspaceScope("other", "other-whatsapp")):
        with pytest.raises(ValueError, match="Property not found: RTF-MISSING"):
            delete_properties(session, ["RTF-MISSING"])


def test_delete_property_routes_single_bulk_and_cross_workspace(session, monkeypatch):
    from fastapi import Header
    from fastapi.testclient import TestClient
    import app.auth as auth_module
    import app.main as main_module

    session.add(Workspace(id="tenant-a", slug="tenant-a", name="Tenant A", status="active"))
    session.add(Workspace(id="tenant-b", slug="tenant-b", name="Tenant B", status="active"))
    session.add(WhatsappAccount(id="tenant-a-whatsapp", workspace_id="tenant-a", account_key="a", display_name="A", status="active"))
    session.add(WhatsappAccount(id="tenant-b-whatsapp", workspace_id="tenant-b", account_key="b", display_name="B", status="active"))
    session.add(WorkspaceMember(workspace_id="tenant-a", auth_user_id="user-a", email="a@example.com", role="owner", status="active"))
    session.add(WorkspaceMember(workspace_id="tenant-b", auth_user_id="user-b", email="b@example.com", role="owner", status="active"))
    session.commit()

    with workspace_scope(WorkspaceScope("tenant-a", "tenant-a-whatsapp")):
        add_property(session, "RTF-A1", property_name="Tenant A One")
        add_property(session, "RTF-A2", property_name="Tenant A Two")
    with workspace_scope(WorkspaceScope("tenant-b", "tenant-b-whatsapp")):
        add_property(session, "RTF-B1", property_name="Tenant B One")
    session.commit()

    def override_session():
        yield session

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    async def override_dashboard_context(
        authorization: str | None = Header(default=None),
        x_workspace_id: str | None = Header(default=None),
    ):
        if not authorization:
            raise HTTPException(status_code=401, detail="Authentication required")
        user = CurrentUser(auth_user_id="user-a", email="a@example.com")
        context = auth_module.resolve_workspace_context(session, user, x_workspace_id)
        token = set_current_workspace_scope(context.scope)
        try:
            yield context
        finally:
            reset_workspace_scope(token)

    main_module.app.dependency_overrides[main_module.get_session] = override_session
    main_module.app.dependency_overrides[main_module.dashboard_context_dependency] = override_dashboard_context
    try:
        client = TestClient(main_module.app)
        headers = {"Authorization": "Bearer user-a-token", "x-workspace-id": "tenant-a"}

        missing = client.post("/api/properties/bulk-delete", json={"property_ids": ["RTF-A1", "RTF-B1"]}, headers=headers)
        assert missing.status_code == 404
        assert "RTF-B1" in missing.json()["detail"]
        assert session.scalar(select(Property).where(Property.workspace_id == "tenant-a", Property.property_id == "RTF-A1")) is not None

        bulk = client.post("/api/properties/bulk-delete", json={"property_ids": ["RTF-A1", "RTF-A2"]}, headers=headers)
        assert bulk.status_code == 200
        assert bulk.json()["deleted_property_ids"] == ["RTF-A1", "RTF-A2"]
        assert bulk.json()["deleted_counts"]["properties"] == 2

        forbidden = client.delete("/api/properties/RTF-B1", headers=headers)
        assert forbidden.status_code == 404

        assert session.scalar(select(Property).where(Property.workspace_id == "tenant-b", Property.property_id == "RTF-B1")) is not None
    finally:
        main_module.app.dependency_overrides.pop(main_module.get_session, None)
        main_module.app.dependency_overrides.pop(main_module.dashboard_context_dependency, None)
        get_settings.cache_clear()


def test_whatsapp_connection_proxy_states_and_reconnect(session, monkeypatch):
    import app.main as main_module

    context = RequestContext(
        user=CurrentUser(auth_user_id="dev-user", email="dev@local.test"),
        scope=WorkspaceScope(),
        role="owner",
    )

    async def fake_connected_status(bridge_base_url=None):
        return {"available": True, "ok": True, "connection": "open", "last_connection_event_at": "2026-07-19T09:00:00Z"}

    monkeypatch.setattr(main_module, "fetch_bridge_status", fake_connected_status)
    connected = asyncio.run(main_module.whatsapp_connection(session=session, context=context))
    assert connected["state"] == "connected"

    async def fake_offline_status(bridge_base_url=None):
        return {"available": False, "ok": False, "status": "bridge_offline", "detail": "connection refused"}

    monkeypatch.setattr(main_module, "fetch_bridge_status", fake_offline_status)
    offline = asyncio.run(main_module.whatsapp_connection(session=session, context=context))
    assert offline["state"] == "bridge_offline"

    async def fake_qr(bridge_base_url=None):
        return {"available": True, "ok": True, "status": "qr_available", "qr_data_url": "data:image/png;base64,abc"}

    monkeypatch.setattr(main_module, "fetch_bridge_pairing_qr", fake_qr)
    qr = asyncio.run(main_module.whatsapp_pairing_qr(session=session, context=context))
    assert qr["state"] == "qr_available"
    assert qr["qr_data_url"] == "data:image/png;base64,abc"

    reconnect_calls: list[bool] = []

    async def fake_needs_reauth_status(bridge_base_url=None):
        return {"available": True, "ok": True, "connection": "close", "last_disconnect_requires_reauth": True}

    async def fake_reconnect(bridge_base_url=None, clear_auth=False):
        reconnect_calls.append(clear_auth)
        return {"available": True, "ok": True, "status": "reconnecting"}

    monkeypatch.setattr(main_module, "fetch_bridge_status", fake_needs_reauth_status)
    monkeypatch.setattr(main_module, "request_bridge_reconnect", fake_reconnect)
    reconnect = asyncio.run(main_module.whatsapp_reconnect(session=session, context=context))
    assert reconnect["state"] == "reconnecting"
    assert reconnect["clear_auth"] is True
    assert reconnect_calls == [True]


@pytest.mark.parametrize(
    ("payload", "error_text"),
    [
        ({"initial_reply_blocks": [{"type": "message", "text": "Hi {unknown_placeholder}"}]}, "unsupported placeholder"),
        ({"initial_reply_blocks": [{"type": "message", "text": "   "}]}, "must not be blank"),
        ({"initial_reply_blocks": [{"type": "delay", "seconds": -1}]}, "between 0 and"),
        ({"initial_reply_blocks": [{"type": "delay", "seconds": 31}]}, "between 0 and"),
        ({"initial_reply_blocks": [{"type": "gallery"}]}, "gallery mode"),
    ],
)
def test_playbook_validation_rejects_invalid_blocks(session, payload, error_text):
    add_property(session, "RTF-001")

    with pytest.raises(ValueError, match=error_text):
        upsert_property_playbook(session, "RTF-001", PropertyPlaybookIn.model_validate(payload))


def test_playbook_routes_get_put_validate_and_export(session):
    from fastapi import HTTPException
    from app.main import export_config, get_property_playbook_route, put_property_playbook_route

    property_ = add_property(session, "RTF-001")
    session.commit()

    effective = get_property_playbook_route(property_.property_id, session=session)
    assert effective["property_id"] == property_.property_id
    assert effective["id"] is None
    assert effective["enabled"] is False
    assert effective["initial_reply_blocks"] == []
    assert effective["qualification_suitable_blocks"] == []
    assert effective["qualification_not_suitable_blocks"] == []
    assert effective["swing_suggestion_blocks"] == []

    updated = put_property_playbook_route(
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hello {property_name} {tenant_facing_caveats} {property_guru_listing}"}]),
        session=session,
    )
    assert updated.initial_reply_blocks == [{"type": "message", "text": "Hello {property_name} {tenant_facing_caveats} {property_guru_listing}"}]

    with pytest.raises(HTTPException) as error:
        put_property_playbook_route(
            property_.property_id,
            PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hello {unknown}"}]),
            session=session,
        )
    assert error.value.status_code == 400
    assert "unsupported placeholder" in error.value.detail

    exported = export_config(session=session)
    assert any(playbook.property_id == property_.property_id for playbook in exported.playbooks)


def test_initial_reply_uses_property_playbook_override_and_gallery(session):
    property_ = add_property(session, "RTF-001", tenant_notes="Duplicate listings warning.")
    upsert_property_media(
        session,
        property_.property_id,
        PropertyMediaIn(media_type="video", file_path="/tmp/301c-tour.mp4", caption="tour", enabled=True),
    )
    upsert_property_media(
        session,
        property_.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/disabled.jpg", caption="disabled", enabled=False),
    )
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "Hi yes available, my unit is {unit_info}."},
                {"type": "delay", "seconds": 1},
                {"type": "message", "text": "{tenant_notes}"},
                {"type": "profile_form"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
            ]
        ),
    )
    conversation = add_fake_conversation(session)
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi yes available, my unit is 301C Punggol Central #03-752."
    assert texts[1] == "Duplicate listings warning."
    assert "Budget:" in texts[2]
    assert texts[3] == "[video] /tmp/301c-tour.mp4"
    assert all("disabled" not in text for text in texts)


def test_sale_property_initial_reply_can_use_sale_specific_playbook_without_rental_profile_form(session):
    property_ = upsert_property(
        session,
        PropertyIn(
            property_id="SALE-001",
            property_name="One Pearl Bank",
            status="available",
            property_type="sale",
            bedrooms=2,
            bathrooms=2,
            asking_rent=1_800_000,
            available_from="Immediate",
            full_address="One Pearl Bank, 1 Pearl Bank",
            property_url="https://example.com/listings/sale-001",
            propertyguru_listing_id="9000001",
            tenant_facing_caveats="Sale viewing by appointment only.",
        ),
    )
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "Hi, yes {property_name} is available for sale."},
                {"type": "message", "text": "{tenant_notes}"},
                {"type": "message", "text": "Can I check your budget and preferred viewing time?"},
            ]
        ),
    )
    conversation = add_fake_conversation(session, text="Hi, is One Pearl Bank still available for sale?")
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))
    session.commit()

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts == [
        "Hi, yes One Pearl Bank is available for sale.",
        "Sale viewing by appointment only.",
        "Can I check your budget and preferred viewing time?",
    ]
    combined = "\n".join(texts)
    assert "No. of people staying" not in combined
    assert "Type of Pass" not in combined
    assert "Lease" not in combined


def test_initial_reply_sends_nothing_when_no_explicit_playbook(session):
    property_ = add_property(session, "RTF-001")
    conversation = add_fake_conversation(session)
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"] == {"status": "not_attempted", "reason": "no_planned_actions"}
    assert outbound_texts(session, conversation.id) == []


def test_default_auto_greeting_is_ignored_even_when_configured_text_differs(session):
    contact = get_or_create_contact(session, "tenant@s.whatsapp.net", "Tenant")
    get_or_create_active_conversation(session, contact, "whatsapp")
    session.commit()

    accepted, reason, data = handle_bridge_inbound(
        session,
        BridgeInboundMessage(
            chat_jid="tenant@s.whatsapp.net",
            sender_jid="me@s.whatsapp.net",
            message_id="default-greeting-1",
            timestamp_ms=1_000,
            from_me=True,
            text="Thank you for contacting the property assistant. Please let us know how we can help you.",
            raw_type="conversation",
        ),
    )

    session.refresh(contact)
    assert accepted is True
    assert reason == "whatsapp_auto_greeting_ignored"
    assert data["contact_id"] == contact.id
    assert contact.status == "active"
    assert outbound_texts(session, contact.conversations[0].id) == []


def test_auto_reply_only_sends_once_per_conversation(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi, yes this unit is still available."}]),
    )
    conversation = add_fake_conversation(session)
    session.commit()

    first = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))
    second = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert first["send_result"]["status"] == "sent"
    assert second["send_result"] == {"status": "skipped", "reason": "auto_reply_already_sent"}
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi, yes this unit is still available."
    assert len([text for text in texts if text == "Hi, yes this unit is still available."]) == 1


def test_legacy_stock_playbook_uses_current_mvp_reply(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_media(
        session,
        property_.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/301c-main.jpg", caption="living room", enabled=True),
    )
    session.add(
        PropertyPlaybook(
            workspace_id=DEFAULT_WORKSPACE_ID,
            property_id=property_.property_id,
            enabled=True,
            initial_reply_blocks=[
                {"type": "message", "text": "Hi, thanks for enquiring about {unit_info}. I'm the listing agent."},
                {"type": "message", "text": "To help me check suitability and arrange viewings, please fill this quick profile form: {profile_form}"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
                {"type": "message", "text": "Here are some photos of the unit."},
            ],
        )
    )
    conversation = add_fake_conversation(session)
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi, yes this unit is still available."
    assert "姓名 Name" in texts[1]
    assert "Budget:" not in texts[0]
    assert texts[2] == "[photo] /tmp/301c-main.jpg"
    assert len(texts) == 3


def test_text_only_playbook_does_not_validate_broken_gallery_media(session, monkeypatch):
    property_ = add_property(session, "RTF-001")
    upsert_property_media(
        session,
        property_.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/definitely-missing-playbook-media.jpg", enabled=True),
    )
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_whatsapp_conversation(session)
    session.commit()

    async def fake_send(chat_jid: str, text: str, bridge_base_url: str | None = None) -> str:
        return f"bridge-{chat_jid}-{len(text)}"

    monkeypatch.setattr("app.actions.send_via_bridge", fake_send)

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["Hi 301C Punggol Central"]


def test_expired_supabase_gallery_url_is_refreshed_before_send(session, monkeypatch):
    property_ = add_property(session, "RTF-001")
    media = PropertyMedia(
        workspace_id=DEFAULT_WORKSPACE_ID,
        property_id=property_.property_id,
        media_type="video",
        file_path="/tmp/no-longer-present.mp4",
        storage_provider="supabase",
        storage_bucket="property-media",
        storage_object_path="RTF-001/tour.mp4",
        signed_url="https://example.supabase.co/expired",
        signed_url_expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        caption="tour",
        sort_order=1,
        enabled=True,
    )
    session.add(media)
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "Hi {property_name}"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
            ]
        ),
    )
    conversation = add_whatsapp_conversation(session)
    session.commit()

    async def fake_send(chat_jid: str, text: str, bridge_base_url: str | None = None) -> str:
        return f"text-{len(text)}"

    async def fake_send_media(chat_jid: str, media: PropertyMedia, bridge_base_url: str | None = None) -> str:
        assert media.signed_url == "https://example.supabase.co/fresh"
        return "media-1"

    monkeypatch.setattr("app.actions.send_via_bridge", fake_send)
    monkeypatch.setattr("app.actions.send_property_media_via_bridge", fake_send_media)
    monkeypatch.setattr(
        "app.actions.create_signed_url_for_supabase_object",
        lambda object_path, config=None: ("https://example.supabase.co/fresh", datetime(2099, 7, 27, tzinfo=timezone.utc)),
    )

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert media.signed_url == "https://example.supabase.co/fresh"
    assert outbound_texts(session, conversation.id) == ["Hi 301C Punggol Central", "[video] supabase://property-media/RTF-001/tour.mp4"]


def test_playbook_uses_only_explicit_delay_blocks(session, monkeypatch):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "First"},
                {"type": "delay", "seconds": 2},
                {"type": "message", "text": "Second"},
            ]
        ),
    )
    conversation = add_whatsapp_conversation(session)
    session.commit()
    sleeps: list[float] = []

    async def fake_send(chat_jid: str, text: str, bridge_base_url: str | None = None) -> str:
        return f"bridge-{text}"

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.actions.send_via_bridge", fake_send)
    monkeypatch.setattr("app.actions.asyncio.sleep", fake_sleep)

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["First", "Second"]
    assert sleeps == [2]


def test_qualification_match_uses_suitable_playbook_blocks(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(qualification_suitable_blocks=[{"type": "message", "text": "Thanks, checking with landlord."}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = property_.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
        session,
        conversation.id,
        {"qualification": {"qualification_status": "match", "reason": "profile likely fits"}},
        )
    )

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id)[-1] == "Thanks, checking with landlord."


def test_qualification_not_match_uses_not_suitable_playbook_when_no_swing(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(qualification_not_suitable_blocks=[{"type": "message", "text": "Sorry, not suitable for this unit."}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = property_.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
        session,
        conversation.id,
        {"qualification": {"qualification_status": "not_match", "reason": "clear mismatch"}},
        )
    )

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id)[-1] == "Sorry, not suitable for this unit."


def test_swing_suggestion_uses_host_playbook_and_suggested_property_placeholders(session):
    host = add_property(session, "RTF-001", property_name="301C Punggol Central")
    suggested = add_property(
        session,
        "RTF-002",
        property_name="185D Rivervale Crescent",
        tenant_notes="New fridge before move-in.",
    )
    upsert_swing_candidate(
        session,
        SwingCandidateIn(source_property_id=host.property_id, candidate_property_id=suggested.property_id, sort_order=1, enabled=True),
    )
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(
            swing_suggestion_blocks=[
                {
                    "type": "message",
                    "text": "I have another unit at {suggested_property_name}: {swing_unit_url}",
                },
                {"type": "message", "text": "{suggested_tenant_notes}"},
            ]
        ),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
        session,
        conversation.id,
        {
            "qualification": {"qualification_status": "not_match", "reason": "tenant does not fit host"},
            "swinging": {
                "swing_status": "suggest_alternative",
                "current_suggested_property": {"property_id": suggested.property_id, "property_name": suggested.property_name},
                "reason": "candidate is plausible",
            },
        },
        )
    )

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts[-2] == "I have another unit at 185D Rivervale Crescent: https://example.com/RTF-002"
    assert texts[-1] == "New fridge before move-in."
    assert session.scalar(select(SwingCandidate).where(SwingCandidate.source_property_id == "RTF-001")).candidate_property_id == "RTF-002"


def test_available_swing_property_can_send_playbook(session):
    host = add_property(session, "RTF-101", property_name="Host Unit")
    suggested = add_property(session, "RTF-102", property_name="Available Swing Unit", status="available")
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(swing_suggestion_blocks=[{"type": "message", "text": "Try {suggested_property_name}: {swing_unit_url}"}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
            session,
            conversation.id,
            {
                "swinging": {
                    "swing_status": "suggest_alternative",
                    "current_suggested_property": {"property_id": suggested.property_id, "property_name": suggested.property_name},
                    "reason": "configured candidate",
                }
            },
        )
    )

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id)[-1] == "Try Available Swing Unit: https://example.com/RTF-102"


def test_unavailable_swing_property_without_fallback_marks_needs_review_and_leaks_no_content(session):
    host = add_property(session, "RTF-111", property_name="Host Unit")
    suggested = add_property(session, "RTF-112", property_name="Unavailable Secret Swing", status="unavailable")
    upsert_property_media(
        session,
        suggested.property_id,
        PropertyMediaIn(media_type="video", file_path="/tmp/secret-swing.mp4", caption="secret", enabled=True),
    )
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(
            swing_suggestion_blocks=[
                {"type": "message", "text": "Try {suggested_property_name}: {swing_unit_url}"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
            ]
        ),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
            session,
            conversation.id,
            {
                "swinging": {
                    "swing_status": "suggest_alternative",
                    "current_suggested_property": {"property_id": suggested.property_id, "property_name": suggested.property_name},
                    "reason": "stale configured candidate",
                }
            },
        )
    )
    texts = outbound_texts(session, conversation.id)

    assert result["send_result"]["status"] == "needs_review"
    assert result["send_result"]["reason"] == "configured_swing_property_unavailable"
    assert result["send_result"]["diagnostic"] == {
        "reason": "configured_swing_property_unavailable",
        "configured_swing_property_id": "RTF-112",
        "current_status": "unavailable",
    }
    assert conversation.status == "needs_review"
    assert texts == []


def test_deleted_swing_property_without_fallback_marks_missing_needs_review(session):
    host = add_property(session, "RTF-121", property_name="Host Unit")
    suggested = add_property(session, "RTF-122", property_name="Deleted Swing Unit", status="available")
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(swing_suggestion_blocks=[{"type": "message", "text": "Try {suggested_property_name}: {swing_unit_url}"}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.delete(suggested)
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
            session,
            conversation.id,
            {
                "swinging": {
                    "swing_status": "suggest_alternative",
                    "current_suggested_property": {"property_id": "RTF-122", "property_name": "Deleted Swing Unit"},
                    "reason": "stale configured candidate",
                }
            },
        )
    )

    assert result["send_result"]["status"] == "needs_review"
    assert result["send_result"]["diagnostic"]["configured_swing_property_id"] == "RTF-122"
    assert result["send_result"]["diagnostic"]["current_status"] == "missing"
    assert outbound_texts(session, conversation.id) == []


def test_swing_property_becoming_unavailable_after_playbook_loaded_does_not_send(session):
    host = add_property(session, "RTF-131", property_name="Host Unit")
    suggested = add_property(session, "RTF-132", property_name="Freshly Unavailable Swing", status="available")
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(swing_suggestion_blocks=[{"type": "message", "text": "Try {suggested_property_name}: {swing_unit_url}"}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.commit()

    suggested.status = "unavailable"
    session.commit()
    result = asyncio.run(
        execute_outbound_action_plan(
            session,
            conversation.id,
            {
                "swinging": {
                    "swing_status": "suggest_alternative",
                    "current_suggested_property": {"property_id": suggested.property_id, "property_name": suggested.property_name},
                    "reason": "candidate became stale after UI loaded",
                }
            },
        )
    )

    assert result["send_result"]["status"] == "needs_review"
    assert result["send_result"]["diagnostic"]["current_status"] == "unavailable"
    assert outbound_texts(session, conversation.id) == []


def test_stale_swing_property_uses_generic_fallback_when_configured(session):
    host = add_property(session, "RTF-141", property_name="Host Unit")
    suggested = add_property(session, "RTF-142", property_name="Unavailable Secret Swing", status="rented")
    update_config(session, {"stale_swing_property_fallback_reply": "Sorry, I need to check the latest available units and update you."})
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(swing_suggestion_blocks=[{"type": "message", "text": "Try {suggested_property_name}: {swing_unit_url}"}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = host.property_id
    conversation.host_property_id = host.property_id
    session.commit()

    result = asyncio.run(
        execute_outbound_action_plan(
            session,
            conversation.id,
            {
                "swinging": {
                    "swing_status": "suggest_alternative",
                    "current_suggested_property": {"property_id": suggested.property_id, "property_name": suggested.property_name},
                    "reason": "stale configured candidate",
                }
            },
        )
    )

    assert result["send_result"]["status"] == "sent"
    assert result["planned_actions"][0]["diagnostic"]["current_status"] == "rented"
    assert outbound_texts(session, conversation.id)[-1] == "Sorry, I need to check the latest available units and update you."
    assert all("Unavailable Secret Swing" not in text for text in outbound_texts(session, conversation.id))


def test_swing_candidate_api_exposes_current_validity_for_available_unavailable_and_deleted(session):
    from app.main import list_swing_candidates

    host = add_property(session, "RTF-151", property_name="Host Unit")
    available = add_property(session, "RTF-152", property_name="Available Swing Unit", status="available")
    unavailable = add_property(session, "RTF-153", property_name="Unavailable Swing Unit", status="reserved")
    deleted = add_property(session, "RTF-154", property_name="Deleted Swing Unit", status="available")
    for index, property_ in enumerate([available, unavailable, deleted], start=1):
        upsert_swing_candidate(
            session,
            SwingCandidateIn(
                source_property_id=host.property_id,
                candidate_property_id=property_.property_id,
                sort_order=index,
                enabled=True,
            ),
        )
    session.delete(deleted)
    session.commit()

    context = RequestContext(CurrentUser("test-user", is_dev_fallback=True), WorkspaceScope())
    rows = {row["candidate_property_id"]: row for row in list_swing_candidates(session=session, context=context)}

    assert rows["RTF-152"]["candidate_property_available"] is True
    assert rows["RTF-152"]["candidate_property_status"] == "available"
    assert rows["RTF-153"]["candidate_property_available"] is False
    assert rows["RTF-153"]["candidate_property_status"] == "reserved"
    assert rows["RTF-153"]["validity_reason"] == "configured_swing_property_unavailable"
    assert rows["RTF-154"]["candidate_property_available"] is False
    assert rows["RTF-154"]["candidate_property_status"] == "missing"


def test_send_lock_and_pause_ai_block_direct_outbound_execution(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_whatsapp_conversation(session)
    session.commit()

    update_config(session, {"send_lock": "true"})
    session.commit()
    locked_result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))
    assert locked_result["send_result"] == {"status": "blocked", "reason": "send_lock_enabled"}
    assert outbound_texts(session, conversation.id) == []

    update_config(session, {"send_lock": "false", "pause_ai": "true"})
    session.commit()
    paused_result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))
    assert paused_result["send_result"] == {"status": "blocked", "reason": "ai_pause_enabled"}
    assert outbound_texts(session, conversation.id) == []


def test_send_lock_does_not_block_fake_chat_simulator(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_fake_conversation(session)
    session.commit()

    update_config(session, {"send_lock": "true"})
    session.commit()
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_unit_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["Hi 301C Punggol Central"]


def test_bridge_send_retries_when_bridge_temporarily_not_connected(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            calls.append({"url": url, "json": json})
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                return httpx.Response(503, request=request, text='{"error":"not_connected","connection":"close"}')
            return httpx.Response(200, request=request, json={"message_id": "retry-ok"})

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.services.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.asyncio.sleep", fake_sleep)

    response = asyncio.run(post_bridge_send_with_retry({"chat_jid": "tenant@lid", "text": "Hi"}, timeout=30, bridge_base_url="http://bridge.test"))

    assert response.json()["message_id"] == "retry-ok"
    assert len(calls) == 2


def test_mvp_available_pipeline_sends_profile_form_and_media_without_qualification(session):
    property_ = add_property(session, "RTF-001", property_name="301C Punggol Central")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "Hi, yes this unit is still available."},
                {"type": "profile_form"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
            ]
        ),
    )
    upsert_property_media(
        session,
        property_.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/301c-main.jpg", caption="living room", enabled=True),
    )
    conversation = add_fake_conversation(session, text="Hi, 301C still available? Budget 3300, 4 pax, immediate.")
    session.commit()

    calls: list[str] = []

    async def generator(messages):
        system = messages[0]["content"]
        if "match the enquiry to one property" in system:
            calls.append("unit_matching")
            return {
                "match_status": "matched",
                "profile_info_status": "profile_present",
                "matched_properties": [{"property_id": property_.property_id, "property_name": property_.property_name}],
                "reason": "deterministic unit match",
            }
        raise AssertionError(system[:200])

    result = asyncio.run(route_stored_conversation_after_inbound(session, conversation.id, generator))
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, result))
    session.commit()

    assert result["send_result"]["status"] == "sent"
    assert conversation.current_stage == "end"
    assert "qualification" not in result
    assert "swinging" not in result
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi, yes this unit is still available."
    assert "No. of people staying:" in texts[1]
    assert texts[2] == "[photo] /tmp/301c-main.jpg"
    assert calls == ["unit_matching"]


def test_mvp_unavailable_pipeline_swings_to_first_configured_fallback_and_media(session):
    host = add_property(session, "RTF-001", property_name="301C Punggol Central", status="unavailable")
    swing = add_property(
        session,
        "RTF-002",
        property_name="185D Rivervale Crescent",
        tenant_notes="Landlord just bought new fridge.",
    )
    upsert_property_media(
        session,
        swing.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/rivervale-main.jpg", caption="living room", enabled=True),
    )
    upsert_swing_candidate(
        session,
        SwingCandidateIn(source_property_id=host.property_id, candidate_property_id=swing.property_id, sort_order=1, enabled=True),
    )
    upsert_property_playbook(
        session,
        host.property_id,
        PropertyPlaybookIn(
            swing_suggestion_blocks=[
                {"type": "message", "text": "Swing option: {suggested_property_name}"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
                {"type": "message", "text": "{suggested_tenant_notes}"},
            ],
        ),
    )
    conversation = add_fake_conversation(session, text="Hi, 301C still available?")
    session.commit()

    calls: list[str] = []

    async def generator(messages):
        system = messages[0]["content"]
        if "match the enquiry to one property" in system:
            calls.append("unit_matching")
            return {
                "match_status": "matched",
                "profile_info_status": "no_profile_detected",
                "matched_properties": [{"property_id": host.property_id, "property_name": host.property_name}],
                "reason": "deterministic unit match",
            }
        raise AssertionError(system[:200])

    result = asyncio.run(route_stored_conversation_after_inbound(session, conversation.id, generator))
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, result))
    session.commit()

    assert result["send_result"]["status"] == "sent"
    assert result["swinging"]["deterministic"] is True
    assert result["swinging"]["swing_status"] == "suggest_alternative"
    assert conversation.current_stage == "end"
    assert conversation.current_suggested_property_id == swing.property_id
    assert conversation.matched_property_id == host.property_id
    assert outbound_texts(session, conversation.id)[-3:] == [
        "Swing option: 185D Rivervale Crescent",
        "[photo] /tmp/rivervale-main.jpg",
        "Landlord just bought new fridge.",
    ]
    assert calls == ["unit_matching"]


def test_legacy_stock_swing_playbook_uses_current_mvp_reply(session):
    host = add_property(session, "RTF-001", property_name="301C Punggol Central", status="unavailable")
    swing = add_property(session, "RTF-002", property_name="185D Rivervale Crescent", tenant_notes="Do not leak this note.")
    upsert_property_media(
        session,
        swing.property_id,
        PropertyMediaIn(media_type="photo", file_path="/tmp/rivervale-main.jpg", caption="living room", enabled=True),
    )
    upsert_swing_candidate(
        session,
        SwingCandidateIn(source_property_id=host.property_id, candidate_property_id=swing.property_id, sort_order=1, enabled=True),
    )
    session.add(
        PropertyPlaybook(
            workspace_id=DEFAULT_WORKSPACE_ID,
            property_id=host.property_id,
            enabled=True,
            initial_reply_blocks=[],
            qualification_suitable_blocks=[],
            qualification_not_suitable_blocks=[],
            swing_suggestion_blocks=[
                {"type": "message", "text": "This unit may no longer be available, but I can recommend this similar unit: {unit_info}"},
                {"type": "gallery", "mode": "enabled_property_gallery"},
                {"type": "message", "text": "Here are some photos of the alternative unit."},
            ],
        )
    )
    conversation = add_fake_conversation(session, text="Hi, 301C still available?")
    session.commit()

    async def generator(messages):
        system = messages[0]["content"]
        if "match the enquiry to one property" in system:
            return {
                "match_status": "matched",
                "profile_info_status": "no_profile_detected",
                "matched_properties": [{"property_id": host.property_id, "property_name": host.property_name}],
                "reason": "deterministic unit match",
            }
        raise AssertionError(system[:200])

    result = asyncio.run(route_stored_conversation_after_inbound(session, conversation.id, generator))
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, result))
    session.commit()

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts[-2] == "Sorry this unit is not available.\n\nI have another unit nearby would you be keen? https://example.com/RTF-002"
    assert texts[-1] == "[photo] /tmp/rivervale-main.jpg"
    assert all("Do not leak this note." not in text for text in texts)


def test_mvp_unavailable_pipeline_without_swing_sends_nothing(session):
    host = add_property(session, "RTF-001", property_name="301C Punggol Central", status="unavailable")
    conversation = add_fake_conversation(session, text="Hi, 301C still available?")
    session.commit()

    calls: list[str] = []

    async def generator(messages):
        system = messages[0]["content"]
        if "match the enquiry to one property" in system:
            calls.append("unit_matching")
            return {
                "match_status": "matched",
                "profile_info_status": "no_profile_detected",
                "matched_properties": [{"property_id": host.property_id, "property_name": host.property_name}],
                "reason": "deterministic unit match",
            }
        raise AssertionError(system[:200])

    result = asyncio.run(route_stored_conversation_after_inbound(session, conversation.id, generator))
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, result))
    session.commit()

    assert result["send_result"] == {"status": "not_attempted", "reason": "no_planned_actions"}
    assert result["swinging"]["swing_status"] == "no_configured_candidate"
    assert conversation.current_suggested_property_id is None
    assert conversation.current_stage == "end"
    assert outbound_texts(session, conversation.id) == []
    assert calls == ["unit_matching"]


def test_triage_enquiry_key_accepts_property_and_legacy_rental_outputs():
    from app.main import triage_is_initial_enquiry

    assert triage_is_initial_enquiry({"is_initial_property_enquiry": True}) is True
    assert triage_is_initial_enquiry({"is_initial_rental_enquiry": True}) is True
    assert triage_is_initial_enquiry({"is_initial_property_enquiry": False}) is False
    assert triage_is_initial_enquiry({"stage_status": "manual_review"}) is False


def test_triage_eval_cases_and_prompt_cover_sale_and_rental_enquiries():
    from app.pipeline import build_triage_messages

    root = Path(__file__).resolve().parents[2]
    cases = json.loads((root / "evals" / "triage_cases.json").read_text())
    assert len(cases) >= 10
    assert any(case["expected_is_initial_property_enquiry"] is True and "sale" in case["id"] for case in cases)
    assert any(case["expected_is_initial_property_enquiry"] is True and "rental" in case["id"] for case in cases)
    assert any(case["expected_is_initial_property_enquiry"] is False for case in cases)

    system_prompt = build_triage_messages(cases[0]["thread"])[0]["content"]
    assert "initial property enquiry" in system_prompt
    assert "sale price" in system_prompt
    assert "purchase interest" in system_prompt
    assert "is_initial_property_enquiry" in system_prompt


def test_unit_matching_eval_cases_and_prompt_cover_sale_and_rental_properties():
    from app.prompts import get_prompt

    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "evals" / "unit_matching_cases.json").read_text())
    properties = payload["property_list"]
    cases = payload["cases"]
    property_jsonl = "\n".join(json.dumps(property_, ensure_ascii=False) for property_ in properties)

    assert any(property_["property_id"].startswith("SALE-") for property_ in properties)
    assert any(property_["property_id"].startswith("RTF-") for property_ in properties)
    assert any(case["expected_match_status"] == "matched" and str(case["expected_property_id"]).startswith("SALE-") for case in cases)
    assert any(case["expected_match_status"] == "matched" and str(case["expected_property_id"]).startswith("RTF-") for case in cases)
    assert any(case["expected_match_status"] == "ambiguous_multiple_matches" for case in cases)
    assert any(case["expected_match_status"] == "unmatched_property" for case in cases)
    assert any(case["id"] == "listing_id_mismatch_does_not_fallback_to_name" for case in cases)

    system_prompt = get_prompt("unit_matching").render(property_list=property_jsonl)
    assert "property enquiry" in system_prompt
    assert "propertyguru_listing_id" in system_prompt
    assert "Do not fall back to property_name or full_address" in system_prompt
    assert "Do not extract or judge tenant/buyer profile information" in system_prompt
