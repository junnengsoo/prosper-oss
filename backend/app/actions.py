from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .media_storage import describe_media_storage
from .database.models import Contact, Conversation, Message, Property, PropertyMedia
from .playbooks import (
    RenderedPlaybookPart,
    enabled_blocks_for_stage,
    get_property_playbook,
    render_playbook_blocks,
)
from .services import (
    append_message,
    bridge_base_url_for_conversation,
    list_property_media,
    send_property_media_via_bridge,
    send_via_bridge,
    split_outbound_parts,
)


@dataclass(frozen=True)
class OutboundAction:
    """A deterministic send instruction derived from AI stage output."""

    action_type: str
    stage: str
    reason: str = ""
    property_id: str | None = None
    playbook_property_id: str | None = None
    message: str = ""
    blocks: list[dict[str, Any]] | None = None
    diagnostic: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a compact JSON-friendly representation for API inspection."""
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None and value != "" and value is not False
        }


@dataclass(frozen=True)
class SentActionResult:
    """Result of executing one outbound action immediately."""

    status: str
    action: dict[str, Any]
    reason: str = ""
    bridge_message_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"status": self.status, "action": self.action, "reason": self.reason}
        if self.bridge_message_ids is not None:
            payload["bridge_message_ids"] = self.bridge_message_ids
        return payload


def _as_dict(value: Any) -> dict[str, Any]:
    """Return dictionaries unchanged and normalize all other values to an empty dict."""
    return value if isinstance(value, dict) else {}


def _listing_matching_result(pipeline_result: dict[str, Any]) -> dict[str, Any]:
    """Extract rental listing matching output from either direct or nested pipeline results."""
    if "match_status" in pipeline_result:
        return pipeline_result
    return _as_dict(pipeline_result.get("rental_listing_matching"))


def _matched_property_id(listing_matching: dict[str, Any], conversation: Conversation) -> str | None:
    """Resolve the property to use for listing follow-up actions."""
    matched = listing_matching.get("matched_properties") or []
    if isinstance(matched, list) and len(matched) == 1 and isinstance(matched[0], dict):
        property_id = matched[0].get("property_id")
        if isinstance(property_id, str) and property_id:
            return property_id
    return conversation.matched_property_id


def _property_for_conversation(session: Session, conversation: Conversation, property_id: str | None = None) -> Property | None:
    """Resolve a property for a conversation."""
    resolved_id = property_id or conversation.matched_property_id
    if not resolved_id:
        return None
    return session.scalar(select(Property).where(Property.property_id == resolved_id))


def _playbook_action(
    session: Session,
    conversation: Conversation,
    *,
    stage: str,
    field_name: str,
    property_id: str | None,
    reason: str = "",
) -> OutboundAction | None:
    playbook_property_id = property_id
    playbook = get_property_playbook(session, playbook_property_id) if playbook_property_id else None
    blocks = enabled_blocks_for_stage(playbook, field_name)
    if blocks:
        return OutboundAction(
            action_type="send_playbook",
            stage=stage,
            property_id=property_id,
            playbook_property_id=playbook_property_id,
            blocks=blocks,
            reason=reason,
        )
    return None


def plan_outbound_actions(conversation: Conversation, pipeline_result: dict[str, Any], session: Session | None = None) -> list[OutboundAction]:
    """Plan deterministic outbound actions from AI stage outputs."""
    if session is None:
        from .database.connection import SessionLocal

        with SessionLocal() as transient_session:
            return plan_outbound_actions(conversation, pipeline_result, transient_session)

    listing_matching = _listing_matching_result(pipeline_result)

    if listing_matching.get("match_status") == "matched":
        property_id = _matched_property_id(listing_matching, conversation)
        if property_id:
            action = _playbook_action(
                session,
                conversation,
                stage="rental_listing_matching",
                field_name="initial_reply_blocks",
                property_id=property_id,
                reason=str(listing_matching.get("reason") or ""),
            )
            return [action] if action else []

    return []


def _latest_message_for_conversation(session: Session, conversation_id: int) -> Message | None:
    """Return the latest message for inspection/debug context."""
    return session.scalar(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.timestamp_ms.desc())
    )


def _render_action_text(session: Session, action: OutboundAction) -> str:
    """Render the text body for a planned action using direct messages or config snippets."""
    if action.action_type == "send_message":
        return action.message.strip()
    if action.action_type == "send_playbook":
        return ""
    return ""


def _render_action_parts(
    session: Session,
    conversation: Conversation,
    action: OutboundAction,
) -> tuple[list[RenderedPlaybookPart], list[PropertyMedia]]:
    """Render an action into structured text/delay/gallery parts and fallback media."""
    if action.action_type == "send_playbook" and action.blocks:
        property_ = _property_for_conversation(session, conversation, action.playbook_property_id or action.property_id)
        rendered = render_playbook_blocks(
            session,
            action.blocks,
            property_=property_,
        )
        has_gallery = any(part.type == "gallery" for part in rendered)
        if not has_gallery:
            return rendered, []
        media_property_id = action.property_id or action.playbook_property_id
        media_items = list_property_media(session, media_property_id) if media_property_id else []
        return rendered, media_items

    text = _render_action_text(session, action)
    rendered: list[RenderedPlaybookPart] = []
    for part_type, value in split_outbound_parts(text):
        rendered.append(RenderedPlaybookPart("gallery" if part_type == "media" else "text", text=value))
    return rendered, []


def _send_block_reason(session: Session, conversation: Conversation) -> str | None:
    """Return a reason that blocks immediate sending, or None when safe to send."""
    from .services import get_config_value, is_ai_paused

    if conversation.source != "fake_chat" and get_config_value(session, "send_lock", "false").lower() == "true":
        return "send_lock_enabled"
    if is_ai_paused(session):
        return "ai_pause_enabled"
    if conversation.status != "active":
        return f"conversation_{conversation.status}"
    contact = session.get(Contact, conversation.contact_id)
    if not contact:
        return "contact_not_found"
    if contact.status != "active":
        return f"contact_{contact.status}"
    if conversation.source not in {"whatsapp", "fake_chat"}:
        return f"source_{conversation.source}"
    return None


def _auto_reply_already_sent(session: Session, conversation: Conversation) -> bool:
    """Check whether Prosper has already sent an automated reply sequence in this conversation."""
    return (
        session.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == "outbound",
                or_(Message.raw_type == "action_send", Message.raw_type.like("action_media_%")),
            )
            .limit(1)
        )
        is not None
    )


def _record_outbound_action_audit(session: Session, conversation: Conversation, result: dict[str, Any]) -> None:
    """Persist the outbound decision so skipped and blocked sends are debuggable later."""
    from .database.models import StageRun

    run = StageRun(
        conversation_id=conversation.id,
        stage="outbound_actions",
        input_snapshot="deterministic outbound action planner",
        output_json=json.dumps(result, ensure_ascii=False),
        status=str(result.get("send_result", {}).get("status") or "unknown"),
    )
    session.add(run)
    session.flush()


def _sendable_property_media(media_items: list[PropertyMedia]) -> tuple[list[PropertyMedia], list[str]]:
    """Split media into sendable items and missing/invalid references."""
    sendable: list[PropertyMedia] = []
    missing: list[str] = []
    for media in media_items:
        descriptor = describe_media_storage(media)
        if descriptor.sendable:
            sendable.append(media)
        else:
            missing.append(descriptor.display_reference)
    return sendable, missing


async def _send_action(
    session: Session,
    conversation: Conversation,
    contact: Contact,
    action: OutboundAction,
) -> SentActionResult:
    """Send one planned action immediately and append outbound message records."""
    rendered_parts, media_items = _render_action_parts(session, conversation, action)
    if not rendered_parts:
        return SentActionResult("skipped", action.to_dict(), "empty_action_text")

    if conversation.source != "fake_chat":
        _, missing_media = _sendable_property_media(media_items)
        if missing_media:
            return SentActionResult("blocked", action.to_dict(), "media_file_missing: " + ", ".join(missing_media))

    bridge_base_url = bridge_base_url_for_conversation(session, conversation)
    sent_at = datetime.now()
    bridge_message_ids: list[str] = []
    send_index = 0
    media_sent = False

    async def send_media_items() -> None:
        nonlocal send_index, media_sent
        if media_sent:
            return
        media_sent = True
        for media in media_items:
            send_index += 1
            if conversation.source == "fake_chat":
                bridge_message_id = f"fake-action-{conversation.id}-{action.stage}-media-{media.id}-{send_index}"
            else:
                bridge_message_id = await send_property_media_via_bridge(contact.chat_jid, media, bridge_base_url=bridge_base_url)
            bridge_message_ids.append(bridge_message_id)
            append_message(
                session,
                conversation,
                contact.chat_jid,
                bridge_message_id or f"sent-action-{conversation.id}-{action.stage}-media-{media.id}-{send_index}",
                f"[{media.media_type}] {describe_media_storage(media).display_reference}",
                int(sent_at.timestamp() * 1000) + send_index - 1,
                "outbound",
                conversation.source,
                None,
                f"action_media_{media.media_type}",
            )

    for part in rendered_parts:
        if part.type == "delay":
            if conversation.source != "fake_chat" and part.seconds > 0:
                await asyncio.sleep(part.seconds)
            continue
        if part.type == "gallery":
            await send_media_items()
            continue

        send_index += 1
        if conversation.source == "fake_chat":
            bridge_message_id = f"fake-action-{conversation.id}-{action.stage}-{send_index}"
        else:
            bridge_message_id = await send_via_bridge(contact.chat_jid, part.text, bridge_base_url=bridge_base_url)
        bridge_message_ids.append(bridge_message_id)
        append_message(
            session,
            conversation,
            contact.chat_jid,
            bridge_message_id or f"sent-action-{conversation.id}-{action.stage}-{send_index}",
            part.text,
            int(sent_at.timestamp() * 1000) + send_index - 1,
            "outbound",
            conversation.source,
            None,
            "action_send",
        )

    conversation.latest_outbound_at = sent_at
    return SentActionResult("sent", action.to_dict(), "sent", bridge_message_ids)


async def execute_outbound_action_plan(session: Session, conversation_id: int, pipeline_result: dict[str, Any]) -> dict[str, Any]:
    """Plan and send outbound actions immediately."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return pipeline_result
    actions = plan_outbound_actions(conversation, pipeline_result, session)
    if actions:
        pipeline_result["planned_actions"] = [action.to_dict() for action in actions]
    needs_review_action = next((action for action in actions if action.action_type == "needs_review"), None)
    if needs_review_action:
        conversation.status = "needs_review"
        conversation.current_stage = "needs_review"
        pipeline_result["send_result"] = {
            "status": "needs_review",
            "reason": needs_review_action.reason,
            "diagnostic": needs_review_action.diagnostic or {},
        }
        _record_outbound_action_audit(session, conversation, pipeline_result)
        return pipeline_result
    if not actions:
        pipeline_result["send_result"] = {"status": "not_attempted", "reason": "no_planned_actions"}
        _record_outbound_action_audit(session, conversation, pipeline_result)
        return pipeline_result

    if _auto_reply_already_sent(session, conversation):
        pipeline_result["send_result"] = {"status": "skipped", "reason": "auto_reply_already_sent"}
        _record_outbound_action_audit(session, conversation, pipeline_result)
        return pipeline_result

    block_reason = _send_block_reason(session, conversation)
    if block_reason:
        pipeline_result["send_result"] = {"status": "blocked", "reason": block_reason}
        _record_outbound_action_audit(session, conversation, pipeline_result)
        return pipeline_result

    contact = session.get(Contact, conversation.contact_id)
    if not contact:
        pipeline_result["send_result"] = {"status": "blocked", "reason": "contact_not_found"}
        _record_outbound_action_audit(session, conversation, pipeline_result)
        return pipeline_result

    results: list[dict[str, Any]] = []
    for action in actions:
        try:
            result = await _send_action(session, conversation, contact, action)
        except Exception as error:
            result = SentActionResult(
                "failed",
                action.to_dict(),
                f"{error.__class__.__name__}: {error}",
            )
        results.append(result.to_dict())
        if result.status != "sent":
            break

    pipeline_result["sent_actions"] = results
    pipeline_result["send_result"] = {
        "status": "sent" if all(result["status"] == "sent" for result in results) else "partial",
        "reason": "actions_executed",
        "results": results,
    }
    _record_outbound_action_audit(session, conversation, pipeline_result)
    return pipeline_result
