from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions import execute_outbound_action_plan, plan_outbound_actions
from app.auth import CurrentUser, RequestContext, current_user_from_session
from app.config import get_settings
from app.database.connection import Base, get_session
from app.database.models import Message, Property, PropertyMedia, PropertyPlaybook, StageRun
from app.pipeline import route_stored_conversation_after_inbound
from app.playbooks import list_property_playbooks, upsert_property_playbook
from app.schemas import BridgeInboundMessage, ConversationStageUpdate, PlaybookBlock, PropertyIn, PropertyMediaIn, PropertyPlaybookIn
from app.database.seed import seed_all
import app.pipeline as pipeline_module
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
)


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


@pytest.fixture
def api_client(session, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()

    def override_session():
        yield session

    main_module.app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()


def matched_listing_result(property_id: str = "RTF-001") -> dict:
    return {
        "rental_listing_matching": {
            "match_status": "matched",
            "matched_property_status": "available",
            "matched_properties": [{"property_id": property_id, "property_name": "301C Punggol Central"}],
            "reason": "matched test property",
        }
    }


def test_api_valid_pipeline_sends_only_after_validated_matching(api_client, session, monkeypatch):
    import app.routers.conversations as conversations_router

    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_fake_conversation(session, text="Hi, is 301C still available?")
    session.commit()

    async def generator(messages):
        return {
            "match_status": "matched",
            "mentioned_property_raw": "301C",
            "mentioned_listing_url": "",
            "extracted_listing_id": "",
            "matched_by": "property_name",
            "matched_properties": [{"property_id": property_.property_id, "property_name": property_.property_name, "reason": "301C"}],
            "reason": "single configured listing match",
        }

    async def route_with_generator(session_arg, conversation_id):
        return await pipeline_module.route_stored_conversation_after_inbound(session_arg, conversation_id, generator)

    monkeypatch.setattr(conversations_router, "route_stored_conversation_after_inbound", route_with_generator)

    response = api_client.post(f"/api/conversations/{conversation.id}/run-next")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["Hi 301C Punggol Central"]


def test_api_malformed_matching_output_records_manual_review_without_outbound(api_client, session, monkeypatch):
    import app.routers.conversations as conversations_router

    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_fake_conversation(session, text="Hi, is 301C still available?")
    session.commit()

    async def generator(messages):
        raise json.JSONDecodeError("bad json", "not json", 0)

    async def route_with_generator(session_arg, conversation_id):
        return await pipeline_module.route_stored_conversation_after_inbound(session_arg, conversation_id, generator)

    monkeypatch.setattr(conversations_router, "route_stored_conversation_after_inbound", route_with_generator)

    response = api_client.post(f"/api/conversations/{conversation.id}/run-next")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["rental_listing_matching"]["match_status"] == "manual_review"
    assert payload["result"]["send_result"]["status"] == "manual_review"
    assert outbound_texts(session, conversation.id) == []
    session.refresh(conversation)
    assert conversation.current_stage == "manual_review"
    assert conversation.status == "active"


def test_api_schema_invalid_matching_output_records_manual_review_without_outbound(api_client, session, monkeypatch):
    import app.routers.conversations as conversations_router

    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_fake_conversation(session, text="Hi, is 301C still available?")
    session.commit()

    async def generator(messages):
        return {
            "match_status": "matched",
            "mentioned_property_raw": "301C",
            "matched_by": "property_name",
            "matched_properties": [],
            "reason": "claims a match without a property",
        }

    async def route_with_generator(session_arg, conversation_id):
        return await pipeline_module.route_stored_conversation_after_inbound(session_arg, conversation_id, generator)

    monkeypatch.setattr(conversations_router, "route_stored_conversation_after_inbound", route_with_generator)

    response = api_client.post(f"/api/conversations/{conversation.id}/run-next")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["rental_listing_matching"]["match_status"] == "manual_review"
    assert payload["result"]["send_result"]["status"] == "manual_review"
    assert outbound_texts(session, conversation.id) == []


def test_api_unavailable_matched_listing_records_manual_review_without_bridge_request(api_client, session, monkeypatch):
    import app.routers.conversations as conversations_router

    property_ = add_property(session, "RTF-001", status="unavailable")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_whatsapp_conversation(session, text="Hi, is 301C still available?")
    session.commit()

    async def generator(messages):
        return {
            "match_status": "matched",
            "mentioned_property_raw": "301C",
            "mentioned_listing_url": "",
            "extracted_listing_id": "",
            "matched_by": "property_name",
            "matched_properties": [{"property_id": property_.property_id, "property_name": property_.property_name, "reason": "301C"}],
            "reason": "single configured listing match",
        }

    async def route_with_generator(session_arg, conversation_id):
        return await pipeline_module.route_stored_conversation_after_inbound(session_arg, conversation_id, generator)

    async def fail_if_bridge_called(chat_jid: str, text: str, bridge_base_url: str | None = None) -> str:
        raise AssertionError("bridge delivery must not be attempted for unavailable listings")

    monkeypatch.setattr(conversations_router, "route_stored_conversation_after_inbound", route_with_generator)
    monkeypatch.setattr("app.actions.send_via_bridge", fail_if_bridge_called)

    response = api_client.post(f"/api/conversations/{conversation.id}/run-next")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["rental_listing_matching"]["match_status"] == "manual_review"
    assert payload["result"]["send_result"]["status"] == "manual_review"
    assert outbound_texts(session, conversation.id) == []
    session.refresh(conversation)
    assert conversation.matched_property_id == property_.property_id
    assert conversation.current_stage == "manual_review"
    assert conversation.status == "active"


def test_api_schema_invalid_triage_records_manual_review_without_matching_or_outbound(api_client, session, monkeypatch):
    import app.routers.simulator as simulator_router

    async def triage_with_invalid_schema(session_arg, thread, generator=..., conversation_id=None, persist_input_snapshot=True):
        async def generator(messages):
            return {"is_initial_rental_enquiry": True, "confidence": "maybe", "reason": "invalid confidence"}

        return await pipeline_module.run_triage_text(
            session_arg,
            thread,
            generator,
            conversation_id=conversation_id,
            persist_input_snapshot=persist_input_snapshot,
        )

    async def fail_if_matching_runs(session_arg, conversation_id):
        raise AssertionError("listing matching must not run after invalid triage")

    monkeypatch.setattr(simulator_router, "run_triage_text", triage_with_invalid_schema)
    monkeypatch.setattr(simulator_router, "run_rental_listing_matching_pipeline", fail_if_matching_runs)

    response = api_client.post(
        "/api/fake-chat/inbound-and-run",
        json={"chat_jid": "triage-invalid@s.whatsapp.net", "text": "Hi 301C?", "display_name": "Invalid Triage"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["triage"]["stage_status"] == "manual_review"
    assert payload["result"]["send_result"]["status"] == "manual_review"
    assert payload["conversation_id"] is not None
    assert outbound_texts(session, payload["conversation_id"]) == []


def test_playbook_crud(session):
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


def test_delete_property_removes_config_and_preserves_history(session):
    property_ = add_property(session, "RTF-001")
    add_property(session, "RTF-002", property_name="185D Rivervale Crescent")
    upsert_property_media(session, property_.property_id, PropertyMediaIn(media_type="photo", file_path="/tmp/301c.jpg"))
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi {property_name}"}]),
    )
    conversation = add_fake_conversation(session)
    conversation.matched_property_id = property_.property_id
    stage_run = StageRun(
        conversation_id=conversation.id,
        stage="rental_listing_matching",
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
        },
    }
    assert session.scalar(select(Property).where(Property.property_id == property_.property_id)) is None
    assert session.scalar(select(PropertyMedia).where(PropertyMedia.property_id == property_.property_id)) is None
    assert session.scalar(select(PropertyPlaybook).where(PropertyPlaybook.property_id == property_.property_id)) is None
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


def test_media_upload_route_stores_and_serves_runtime_file(session, monkeypatch, tmp_path):
    import app.main as main_module
    from app.database.models import PropertyMedia
    from fastapi.testclient import TestClient

    property_ = add_property(session, "RTF-MEDIA")
    session.commit()
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()

    def override_session():
        yield session

    main_module.app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(main_module.app)
        uploaded = client.post(
            f"/api/properties/{property_.property_id}/media/upload",
            data={"media_type": "photo", "caption": "living room", "sort_order": "1", "enabled": "true"},
            files={"file": ("living room.jpg", b"image-bytes", "image/jpeg")},
        )
        assert uploaded.status_code == 200
        media = uploaded.json()
        media_path = Path(media["file_path"])
        assert media_path.is_file()

        content = client.get(f"/api/property-media/{media['id']}/content")
        assert content.status_code == 200
        assert content.content == b"image-bytes"

        deleted = client.delete(f"/api/property-media/{media['id']}")
        assert deleted.status_code == 200
        assert not media_path.exists()
        assert session.get(PropertyMedia, media["id"]) is None
    finally:
        main_module.app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()


def test_whatsapp_connection_proxy_states_and_reconnect(session, monkeypatch):
    import app.routers.config_runtime as config_runtime_router

    context = RequestContext(user=CurrentUser(auth_user_id="dev-user", email="dev@local.test"), role="owner")

    async def fake_connected_status(bridge_base_url=None):
        return {"available": True, "ok": True, "connection": "open", "last_connection_event_at": "2026-07-19T09:00:00Z"}

    monkeypatch.setattr(config_runtime_router, "fetch_bridge_status", fake_connected_status)
    connected = asyncio.run(config_runtime_router.whatsapp_connection(_context=context))
    assert connected["state"] == "connected"

    async def fake_offline_status(bridge_base_url=None):
        return {"available": False, "ok": False, "status": "bridge_offline", "detail": "connection refused"}

    monkeypatch.setattr(config_runtime_router, "fetch_bridge_status", fake_offline_status)
    offline = asyncio.run(config_runtime_router.whatsapp_connection(_context=context))
    assert offline["state"] == "bridge_offline"

    async def fake_qr(bridge_base_url=None):
        return {"available": True, "ok": True, "status": "qr_available", "qr_data_url": "data:image/png;base64,abc"}

    monkeypatch.setattr(config_runtime_router, "fetch_bridge_pairing_qr", fake_qr)
    qr = asyncio.run(config_runtime_router.whatsapp_pairing_qr(session=session, context=context))
    assert qr["state"] == "qr_available"
    assert qr["qr_data_url"] == "data:image/png;base64,abc"

    reconnect_calls: list[bool] = []

    async def fake_needs_reauth_status(bridge_base_url=None):
        return {"available": True, "ok": True, "connection": "close", "last_disconnect_requires_reauth": True}

    async def fake_reconnect(bridge_base_url=None, clear_auth=False):
        reconnect_calls.append(clear_auth)
        return {"available": True, "ok": True, "status": "reconnecting"}

    monkeypatch.setattr(config_runtime_router, "fetch_bridge_status", fake_needs_reauth_status)
    monkeypatch.setattr(config_runtime_router, "request_bridge_reconnect", fake_reconnect)
    reconnect = asyncio.run(config_runtime_router.whatsapp_reconnect(session=session, context=context))
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


def test_removed_extra_contract_shapes_are_rejected():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        PropertyIn.model_validate(
            {
                "property_id": "RTF-OLD",
                "property_name": "Old Listing",
                "unexpected_field": "screening requirement",
            }
        )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        PropertyPlaybookIn.model_validate({"removed_blocks": []})

    with pytest.raises(ValueError):
        PlaybookBlock.model_validate({"type": "unsupported_block"})

    with pytest.raises(ValueError):
        ConversationStageUpdate.model_validate({"stage": "removed_stage"})


def test_playbook_routes_get_put_validate_and_export(session):
    from fastapi import HTTPException
    from app.routers.config_runtime import export_config
    from app.routers.listings import get_property_playbook_route, put_property_playbook_route

    property_ = add_property(session, "RTF-001")
    session.commit()

    effective = get_property_playbook_route(property_.property_id, session=session)
    assert effective["property_id"] == property_.property_id
    assert effective["id"] is None
    assert effective["enabled"] is False
    assert effective["initial_reply_blocks"] == []

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
                {"type": "gallery", "mode": "enabled_property_gallery"},
            ]
        ),
    )
    conversation = add_fake_conversation(session)
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi yes available, my unit is 301C Punggol Central #03-752."
    assert texts[1] == "Duplicate listings warning."
    assert texts[2] == "[video] /tmp/301c-tour.mp4"
    assert all("disabled" not in text for text in texts)


def test_initial_reply_sends_nothing_when_no_explicit_playbook(session):
    property_ = add_property(session, "RTF-001")
    conversation = add_fake_conversation(session)
    session.commit()

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

    assert result["send_result"] == {"status": "not_attempted", "reason": "no_planned_actions"}
    assert outbound_texts(session, conversation.id) == []


def test_auto_reply_only_sends_once_per_conversation(session):
    property_ = add_property(session, "RTF-001")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(initial_reply_blocks=[{"type": "message", "text": "Hi, yes this unit is still available."}]),
    )
    conversation = add_fake_conversation(session)
    session.commit()

    first = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))
    second = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

    assert first["send_result"]["status"] == "sent"
    assert second["send_result"] == {"status": "skipped", "reason": "auto_reply_already_sent"}
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi, yes this unit is still available."
    assert len([text for text in texts if text == "Hi, yes this unit is still available."]) == 1


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

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["Hi 301C Punggol Central"]


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

    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

    assert result["send_result"]["status"] == "sent"
    assert outbound_texts(session, conversation.id) == ["First", "Second"]
    assert sleeps == [2]


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
    locked_result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))
    assert locked_result["send_result"] == {"status": "blocked", "reason": "send_lock_enabled"}
    assert outbound_texts(session, conversation.id) == []

    update_config(session, {"send_lock": "false", "pause_ai": "true"})
    session.commit()
    paused_result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))
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
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, matched_listing_result(property_.property_id)))

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

        async def post(self, url, json, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                return httpx.Response(503, request=request, text='{"error":"not_connected","connection":"close"}')
            return httpx.Response(200, request=request, json={"message_id": "retry-ok"})

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.services.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.asyncio.sleep", fake_sleep)
    monkeypatch.setenv("WHATSAPP_PA_BRIDGE_TOKEN", "backend-secret")
    get_settings.cache_clear()

    try:
        response = asyncio.run(post_bridge_send_with_retry({"chat_jid": "tenant@lid", "text": "Hi"}, timeout=30, bridge_base_url="http://bridge.test"))
    finally:
        get_settings.cache_clear()

    assert response.json()["message_id"] == "retry-ok"
    assert len(calls) == 2
    assert calls[0]["headers"]["x-whatsapp-bridge-token"] == "backend-secret"
    assert calls[1]["headers"]["x-whatsapp-bridge-token"] == "backend-secret"


def test_backend_bridge_proxy_requests_attach_configured_token(monkeypatch):
    from app.services import fetch_bridge_pairing_qr, fetch_bridge_status, request_bridge_reconnect

    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            calls.append({"method": "GET", "url": url, "headers": headers})
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"ok": True})

        async def post(self, url, json, headers=None):
            calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            return httpx.Response(202, request=request, json={"ok": True, "status": "reconnecting"})

    monkeypatch.setattr("app.services.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("WHATSAPP_PA_BRIDGE_TOKEN", "backend-secret")
    get_settings.cache_clear()
    try:
        assert asyncio.run(fetch_bridge_status("http://bridge.test"))["ok"] is True
        assert asyncio.run(fetch_bridge_pairing_qr("http://bridge.test"))["ok"] is True
        assert asyncio.run(request_bridge_reconnect("http://bridge.test", clear_auth=True))["ok"] is True
    finally:
        get_settings.cache_clear()

    assert [call["url"] for call in calls] == [
        "http://bridge.test/status",
        "http://bridge.test/pairing/qr",
        "http://bridge.test/pairing/reconnect",
    ]
    assert all(call["headers"]["x-whatsapp-bridge-token"] == "backend-secret" for call in calls)


def test_available_pipeline_sends_listing_reply_and_media(session):
    property_ = add_property(session, "RTF-001", property_name="301C Punggol Central")
    upsert_property_playbook(
        session,
        property_.property_id,
        PropertyPlaybookIn(
            initial_reply_blocks=[
                {"type": "message", "text": "Hi, yes this unit is still available."},
                {"type": "message", "text": "Please share your preferred viewing time."},
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
        if "match the enquiry to one rental listing" in system:
            calls.append("rental_listing_matching")
            return {
                "match_status": "matched",
                "matched_properties": [{"property_id": property_.property_id, "property_name": property_.property_name}],
                "reason": "deterministic unit match",
            }
        raise AssertionError(system[:200])

    result = asyncio.run(route_stored_conversation_after_inbound(session, conversation.id, generator))
    result = asyncio.run(execute_outbound_action_plan(session, conversation.id, result))
    session.commit()

    assert result["send_result"]["status"] == "sent"
    assert conversation.current_stage == "end"
    texts = outbound_texts(session, conversation.id)
    assert texts[0] == "Hi, yes this unit is still available."
    assert texts[1] == "Please share your preferred viewing time."
    assert texts[2] == "[photo] /tmp/301c-main.jpg"
    assert calls == ["rental_listing_matching"]


def test_triage_enquiry_key_accepts_property_and_legacy_rental_outputs():
    from app.router_support import triage_is_initial_enquiry

    assert triage_is_initial_enquiry({"is_initial_rental_enquiry": True}) is True
    assert triage_is_initial_enquiry({"stage_status": "manual_review"}) is False


def test_triage_eval_cases_and_prompt_cover_rental_enquiries_only():
    from app.pipeline import build_triage_messages

    root = Path(__file__).resolve().parents[2]
    cases = json.loads((root / "evals" / "triage_cases.json").read_text())
    assert len(cases) >= 10
    assert any(case["expected_is_initial_rental_enquiry"] is True and "rental" in case["id"] for case in cases)
    assert any(case["expected_is_initial_rental_enquiry"] is False for case in cases)

    system_prompt = build_triage_messages(cases[0]["thread"])[0]["content"]
    assert "initial rental enquiry" in system_prompt
    assert "purchase or asking-price enquiry" in system_prompt
    assert "is_initial_rental_enquiry" in system_prompt


def test_rental_listing_matching_eval_cases_and_prompt_cover_rental_properties():
    from app.prompts import get_prompt

    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "evals" / "rental_listing_matching_cases.json").read_text())
    properties = payload["property_list"]
    cases = payload["cases"]
    property_jsonl = "\n".join(json.dumps(property_, ensure_ascii=False) for property_ in properties)

    assert any(property_["property_id"].startswith("RTF-") for property_ in properties)
    assert any(case["expected_match_status"] == "matched" and str(case["expected_property_id"]).startswith("RTF-") for case in cases)
    assert any(case["expected_match_status"] == "ambiguous_multiple_matches" for case in cases)
    assert any(case["expected_match_status"] == "unmatched_property" for case in cases)
    assert any(case["id"] == "listing_id_mismatch_does_not_fallback_to_name" for case in cases)

    system_prompt = get_prompt("rental_listing_matching").render(property_list=property_jsonl)
    assert "rental enquiry" in system_prompt
    assert "propertyguru_listing_id" in system_prompt
    assert "Do not fall back to property_name or full_address" in system_prompt
    assert "Do not extract or judge tenant profile information" in system_prompt
