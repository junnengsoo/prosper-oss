import asyncio
from datetime import datetime
from time import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .media_storage import describe_media_storage
from .database.models import AppConfig, Contact, Conversation, Message, Property, PropertyMedia, PropertyPlaybook, StageRun
from .normalize import extract_propertyguru_listing_id
from .schemas import BridgeInboundMessage, FakeInboundMessage, PropertyIn, PropertyMediaIn
from .app_config import get_config_value
MESSAGE_BREAK_MARKER = "<message_break>"
MEDIA_MARKER = "<media>"
BRIDGE_SEND_RETRY_DELAYS_SECONDS = (1.0, 2.0)


def now_ms() -> int:
    return int(time() * 1000)


def split_outbound_text(text: str) -> list[str]:
    return [part.strip() for part in text.split(MESSAGE_BREAK_MARKER) if part.strip()]


def split_outbound_parts(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for message_part in text.split(MESSAGE_BREAK_MARKER):
        remaining = message_part
        while MEDIA_MARKER in remaining:
            before, remaining = remaining.split(MEDIA_MARKER, 1)
            if before.strip():
                parts.append(("text", before.strip()))
            parts.append(("media", ""))
        if remaining.strip():
            parts.append(("text", remaining.strip()))
    return parts


def timestamp_to_datetime(timestamp_ms: int | None) -> datetime | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000)


def get_or_create_contact(
    session: Session,
    chat_jid: str,
    display_name: str | None = None,
    phone: str | None = None,
) -> Contact:
    contact = session.scalar(select(Contact).where(Contact.chat_jid == chat_jid))
    if contact:
        if display_name and not contact.display_name:
            contact.display_name = display_name
        if phone and not contact.phone:
            contact.phone = phone
        return contact

    contact = Contact(chat_jid=chat_jid, display_name=display_name, phone=phone, status="active")
    session.add(contact)
    session.flush()
    return contact


def get_active_conversation(session: Session, contact_id: int) -> Conversation | None:
    return session.scalar(
        select(Conversation).where(Conversation.contact_id == contact_id, Conversation.status == "active")
    )


def get_latest_conversation(session: Session, contact_id: int) -> Conversation | None:
    return session.scalar(
        select(Conversation).where(Conversation.contact_id == contact_id).order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )


def get_or_create_active_conversation(session: Session, contact: Contact, source: str) -> Conversation:
    conversation = get_active_conversation(session, contact.id)
    if conversation:
        return conversation

    conversation = Conversation(
        contact_id=contact.id,
        source=source,
        status="active",
        current_stage="rental_listing_matching",
    )
    session.add(conversation)
    session.flush()
    return conversation


def close_conversation(session: Session, conversation: Conversation) -> Conversation:
    conversation.status = "closed"
    conversation.current_stage = "end"
    return conversation


def start_new_enquiry(session: Session, conversation: Conversation) -> Conversation:
    contact = session.get(Contact, conversation.contact_id)
    if not contact:
        raise ValueError("Contact not found")
    close_conversation(session, conversation)
    session.flush()
    next_conversation = Conversation(
        contact_id=contact.id,
        source=conversation.source,
        status="active",
        current_stage="rental_listing_matching",
    )
    session.add(next_conversation)
    session.flush()
    return next_conversation


def resume_conversation_stage(
    session: Session,
    conversation: Conversation,
    stage: str,
    resume_contact: bool = True,
) -> Conversation:
    if conversation.status == "closed":
        raise ValueError("Cannot resume a closed conversation; start a new enquiry instead")

    conversation.status = "active"
    conversation.current_stage = stage

    if resume_contact:
        contact = session.get(Contact, conversation.contact_id)
        if not contact:
            raise ValueError("Contact not found")
        contact.status = "active"
        contact.status_reason = "resumed_from_dashboard"

    return conversation


def append_message(
    session: Session,
    conversation: Conversation,
    chat_jid: str,
    message_id: str,
    text: str,
    timestamp_ms: int,
    direction: str,
    source: str,
    sender_jid: str | None = None,
    raw_type: str | None = None,
) -> Message:
    existing = session.scalar(
        select(Message).where(Message.chat_jid == chat_jid, Message.message_id == message_id)
    )
    if existing:
        return existing

    message = Message(
        conversation_id=conversation.id,
        chat_jid=chat_jid,
        sender_jid=sender_jid,
        message_id=message_id,
        direction=direction,
        source=source,
        raw_type=raw_type,
        text=text,
        timestamp_ms=timestamp_ms,
    )
    session.add(message)
    conversation.latest_inbound_at = timestamp_to_datetime(timestamp_ms) if direction == "inbound" else conversation.latest_inbound_at
    conversation.latest_outbound_at = timestamp_to_datetime(timestamp_ms) if direction in {"outbound", "human"} else conversation.latest_outbound_at
    session.flush()
    return message


def stored_message_exists(session: Session, chat_jid: str, message_id: str) -> bool:
    return (
        session.scalar(
            select(Message.id).where(
                Message.chat_jid == chat_jid,
                Message.message_id == message_id,
            )
        )
        is not None
    )


def pause_contact(session: Session, contact: Contact, reason: str) -> None:
    contact.status = "paused"
    contact.status_reason = reason


def ignore_contact(session: Session, contact: Contact, reason: str) -> None:
    contact.status = "ignored"
    contact.status_reason = reason


def cancel_contact(session: Session, contact: Contact, reason: str = "cancelled_from_dashboard") -> None:
    contact.status = "paused"
    contact.status_reason = reason


def is_reset_command(text: str) -> bool:
    return text.strip().lower() == "!reset"


def handle_bridge_inbound(session: Session, payload: BridgeInboundMessage) -> tuple[bool, str, dict]:
    contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
    if payload.from_me and stored_message_exists(session, payload.chat_jid, payload.message_id):
        return True, "duplicate_from_me_ignored", {"contact_id": contact.id}
    contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms)

    conversation = get_active_conversation(session, contact.id)

    if not payload.from_me and is_reset_command(payload.text):
        if conversation:
            close_conversation(session, conversation)
        contact.status = "active"
        contact.status_reason = "reset_by_whatsapp_command"
        new_conversation = get_or_create_active_conversation(session, contact, "whatsapp")
        return True, "contact_reset_by_command", {
            "contact_id": contact.id,
            "conversation_id": new_conversation.id,
            "closed_conversation_id": conversation.id if conversation else None,
            "message_id": None,
        }

    if payload.from_me:
        if conversation:
            append_message(
                session,
                conversation,
                payload.chat_jid,
                payload.message_id,
                payload.text,
                payload.timestamp_ms,
                "human",
                "whatsapp",
                payload.sender_jid,
                payload.raw_type,
            )
        pause_contact(session, contact, "human_replied")
        return True, "human_reply_paused_contact", {"contact_id": contact.id, "conversation_id": conversation.id if conversation else None}

    if contact.status == "ignored":
        return True, "contact_ignored", {"contact_id": contact.id}
    if contact.status == "paused":
        conversation = conversation or get_latest_conversation(session, contact.id)
        message = None
        if conversation:
            message = append_message(
                session,
                conversation,
                payload.chat_jid,
                payload.message_id,
                payload.text,
                payload.timestamp_ms,
                "inbound",
                "whatsapp",
                payload.sender_jid,
                payload.raw_type,
            )
        return True, "contact_paused", {
            "contact_id": contact.id,
            "conversation_id": conversation.id if conversation else None,
            "message_id": message.id if message else None,
        }

    conversation = get_or_create_active_conversation(session, contact, "whatsapp")
    message = append_message(
        session,
        conversation,
        payload.chat_jid,
        payload.message_id,
        payload.text,
        payload.timestamp_ms,
        "inbound",
        "whatsapp",
        payload.sender_jid,
        payload.raw_type,
    )
    return True, "stored_inbound_message", {"contact_id": contact.id, "conversation_id": conversation.id, "message_id": message.id}


def handle_fake_inbound(session: Session, payload: FakeInboundMessage) -> Message:
    timestamp_ms = payload.timestamp_ms or now_ms()
    message_id = payload.message_id or f"fake-{timestamp_ms}"
    contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
    contact.last_message_at = timestamp_to_datetime(timestamp_ms)
    conversation = get_or_create_active_conversation(session, contact, "fake_chat")
    return append_message(
        session,
        conversation,
        payload.chat_jid,
        message_id,
        payload.text,
        timestamp_ms,
        "inbound",
        "fake_chat",
        payload.chat_jid,
        "fake_text",
    )


def reset_fake_chat_data(session: Session) -> dict[str, int]:
    fake_conversations = session.scalars(select(Conversation).where(Conversation.source == "fake_chat")).all()
    fake_conversation_ids = [conversation.id for conversation in fake_conversations]
    fake_contact_ids = {conversation.contact_id for conversation in fake_conversations}
    fake_contact_ids.update(session.scalars(select(Contact.id).where(Contact.chat_jid.like("fake%"))).all())

    if fake_conversation_ids:
        stage_runs = session.scalars(select(StageRun).where(StageRun.conversation_id.in_(fake_conversation_ids))).all()
        messages = session.scalars(select(Message).where(Message.conversation_id.in_(fake_conversation_ids))).all()
    else:
        stage_runs = []
        messages = []

    for item in [*stage_runs, *messages, *fake_conversations]:
        session.delete(item)
    session.flush()

    if fake_contact_ids:
        protected_contact_ids = set(
            session.scalars(select(Conversation.contact_id).where(Conversation.contact_id.in_(fake_contact_ids))).all()
        )
        deletable_contact_ids = fake_contact_ids - protected_contact_ids
        contacts = session.scalars(select(Contact).where(Contact.id.in_(deletable_contact_ids))).all() if deletable_contact_ids else []
    else:
        contacts = []

    for contact in contacts:
        session.delete(contact)
    session.flush()

    return {
        "contacts_deleted": len(contacts),
        "conversations_deleted": len(fake_conversations),
        "messages_deleted": len(messages),
        "stage_runs_deleted": len(stage_runs),
    }


def upsert_property(session: Session, payload: PropertyIn) -> Property:
    property_ = session.scalar(select(Property).where(Property.property_id == payload.property_id))
    values = payload.model_dump()
    if not values.get("propertyguru_listing_id"):
        values["propertyguru_listing_id"] = extract_propertyguru_listing_id(values.get("property_url"))
    if property_:
        for key, value in values.items():
            setattr(property_, key, value)
        return property_

    property_ = Property(**values)
    session.add(property_)
    session.flush()
    return property_


def delete_properties(session: Session, property_ids: list[str]) -> dict[str, object]:
    normalized_property_ids = list(dict.fromkeys(property_id.strip() for property_id in property_ids if property_id.strip()))
    if not normalized_property_ids:
        raise ValueError("At least one property ID is required")

    properties = list(
        session.scalars(
            select(Property).where(
                Property.property_id.in_(normalized_property_ids),
            )
        ).all()
    )
    found_property_ids = {property_.property_id for property_ in properties}
    missing_property_ids = [property_id for property_id in normalized_property_ids if property_id not in found_property_ids]
    if missing_property_ids:
        raise ValueError("Property not found: " + ", ".join(missing_property_ids))

    media = list(
        session.scalars(
            select(PropertyMedia).where(
                PropertyMedia.property_id.in_(normalized_property_ids),
            )
        ).all()
    )
    playbooks = list(
        session.scalars(
            select(PropertyPlaybook).where(
                PropertyPlaybook.property_id.in_(normalized_property_ids),
            )
        ).all()
    )
    for item in [*media, *playbooks, *properties]:
        session.delete(item)
    session.flush()

    return {
        "deleted_property_ids": normalized_property_ids,
        "deleted_counts": {
            "properties": len(properties),
            "media": len(media),
            "playbooks": len(playbooks),
        },
    }


def delete_property(session: Session, property_id: str) -> dict[str, object]:
    return delete_properties(session, [property_id])


def list_property_media(session: Session, property_id: str, include_disabled: bool = False) -> list[PropertyMedia]:
    query = select(PropertyMedia).where(PropertyMedia.property_id == property_id)
    if not include_disabled:
        query = query.where(PropertyMedia.enabled.is_(True))
    return list(session.scalars(query.order_by(PropertyMedia.sort_order, PropertyMedia.id)).all())


def upsert_property_media(session: Session, property_id: str, payload: PropertyMediaIn) -> PropertyMedia:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise ValueError("Property not found")

    media = session.scalar(
        select(PropertyMedia).where(
            PropertyMedia.property_id == property_id,
            PropertyMedia.file_path == payload.file_path,
        )
    )
    values = payload.model_dump()
    if media:
        for key, value in values.items():
            setattr(media, key, value)
        return media

    media = PropertyMedia(property_id=property_id, **values)
    session.add(media)
    session.flush()
    return media


def delete_property_media(session: Session, media_id: int) -> PropertyMedia:
    media = session.get(PropertyMedia, media_id)
    if not media:
        raise ValueError("Property media not found")
    session.delete(media)
    session.flush()
    return media


def get_all_config(session: Session) -> dict[str, str]:
    return {item.key: item.value for item in session.scalars(select(AppConfig)).all()}


def is_ai_paused(session: Session) -> bool:
    return get_config_value(session, "pause_ai", "false").lower() == "true"


BOOLEAN_CONFIG_KEYS = {"pause_ai", "send_lock"}
REQUIRED_NONBLANK_CONFIG_KEYS: set[str] = set()


def validate_config_update(values: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Config key must not be blank")
        normalized_key = key.strip()
        normalized_value = str(value)
        if normalized_key in BOOLEAN_CONFIG_KEYS:
            boolean_value = normalized_value.strip().lower()
            if boolean_value not in {"true", "false"}:
                raise ValueError(f"{normalized_key} must be true or false")
            normalized[normalized_key] = boolean_value
            continue
        if normalized_key in REQUIRED_NONBLANK_CONFIG_KEYS and not normalized_value.strip():
            raise ValueError(f"{normalized_key} must not be blank")
        normalized[normalized_key] = normalized_value
    return normalized


def update_config(session: Session, values: dict[str, str]) -> dict[str, str]:
    normalized_values = validate_config_update(values)
    for key, value in normalized_values.items():
        item = session.scalar(select(AppConfig).where(AppConfig.key == key))
        if item:
            item.value = value
        else:
            session.add(AppConfig(key=key, value=value))
    session.flush()
    return get_all_config(session)


def bridge_base_url_for_conversation(session: Session, conversation: Conversation) -> str:
    return get_settings().bridge_base_url


async def send_via_bridge(chat_jid: str, text: str, bridge_base_url: str | None = None) -> str:
    response = await post_bridge_send_with_retry({"chat_jid": chat_jid, "text": text}, timeout=30, bridge_base_url=bridge_base_url)
    body = response.json()
    return str(body.get("message_id") or "")


async def send_media_payload_via_bridge(
    chat_jid: str,
    *,
    media_type: str,
    media_reference,
    bridge_base_url: str | None = None,
) -> str:
    media = describe_media_storage(media_reference)
    payload = {
        "chat_jid": chat_jid,
        "media_type": media_type,
        "file_path": media.send_url,
    }
    try:
        response = await post_bridge_send_with_retry(payload, timeout=60, bridge_base_url=bridge_base_url)
    except TypeError:
        response = await post_bridge_send_with_retry(payload, timeout=60)
    body = response.json()
    return str(body.get("message_id") or "")


async def send_property_media_via_bridge(chat_jid: str, media: PropertyMedia, bridge_base_url: str | None = None) -> str:
    return await send_media_payload_via_bridge(
        chat_jid,
        media_type=media.media_type,
        media_reference=media,
        bridge_base_url=bridge_base_url,
    )


async def post_bridge_send_with_retry(payload: dict[str, object], timeout: int, bridge_base_url: str | None = None) -> httpx.Response:
    last_error: Exception | None = None
    base_url = (bridge_base_url or get_settings().bridge_base_url).rstrip("/")
    for attempt in range(len(BRIDGE_SEND_RETRY_DELAYS_SECONDS) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url}/send", json=payload)
                raise_bridge_status(response)
                return response
        except Exception as error:
            last_error = error
            if attempt >= len(BRIDGE_SEND_RETRY_DELAYS_SECONDS) or not is_retryable_bridge_send_error(error):
                raise
            await asyncio.sleep(BRIDGE_SEND_RETRY_DELAYS_SECONDS[attempt])
    if last_error:
        raise last_error
    raise RuntimeError("Bridge send failed")


def is_retryable_bridge_send_error(error: Exception) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    message = str(error)
    return (
        "Connection Closed" in message
        or "send_failed" in message
        or "not_connected" in message
        or "HTTP 500" in message
        or "HTTP 502" in message
        or "HTTP 503" in message
        or "HTTP 504" in message
    )


def raise_bridge_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = response.text[:1200]
        raise RuntimeError(f"Bridge send failed with HTTP {response.status_code}: {detail}") from error


async def fetch_bridge_status(bridge_base_url: str | None = None) -> dict[str, object]:
    base_url = (bridge_base_url or get_settings().bridge_base_url).rstrip("/")
    url = f"{base_url}/status"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(url)
            response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            return {"available": True, **body}
        return {"available": True, "ok": True, "raw": body}
    except Exception as error:
        return {
            "available": False,
            "ok": False,
            "error": error.__class__.__name__,
            "detail": str(error),
            "url": url,
        }


async def fetch_bridge_pairing_qr(bridge_base_url: str | None = None) -> dict[str, object]:
    base_url = (bridge_base_url or get_settings().bridge_base_url).rstrip("/")
    url = f"{base_url}/pairing/qr"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
        body = response.json()
        if isinstance(body, dict):
            return {"available": True, "http_status": response.status_code, **body}
        return {"available": True, "http_status": response.status_code, "ok": response.is_success, "raw": body}
    except Exception as error:
        return {
            "available": False,
            "ok": False,
            "status": "bridge_offline",
            "error": error.__class__.__name__,
            "detail": str(error),
            "url": url,
        }


async def request_bridge_reconnect(bridge_base_url: str | None = None, clear_auth: bool = False) -> dict[str, object]:
    base_url = (bridge_base_url or get_settings().bridge_base_url).rstrip("/")
    url = f"{base_url}/pairing/reconnect"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"clear_auth": clear_auth})
        body = response.json()
        if isinstance(body, dict):
            return {"available": True, "http_status": response.status_code, **body}
        return {"available": True, "http_status": response.status_code, "ok": response.is_success, "raw": body}
    except Exception as error:
        return {
            "available": False,
            "ok": False,
            "status": "bridge_offline",
            "error": error.__class__.__name__,
            "detail": str(error),
            "url": url,
        }
