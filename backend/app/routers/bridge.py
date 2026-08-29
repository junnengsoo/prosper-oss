from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.connection import get_session
from ..database.models import Contact, Conversation
from ..dependencies import BridgeContext
from ..pipeline import (
    is_manual_review_result,
    route_stored_conversation_after_inbound,
    run_rental_listing_matching_pipeline,
    run_triage_text,
)
from ..router_support import attach_outbound_action_result, route_triage_manual_review, triage_is_initial_enquiry
from ..schemas import BridgeAck, BridgeInboundBatch, BridgeInboundMessage
from ..services import (
    get_or_create_contact,
    handle_bridge_inbound,
    is_ai_paused,
)

router = APIRouter()


def active_whatsapp_conversation_for_payload(session: Session, payload: BridgeInboundMessage) -> tuple[Contact | None, Conversation | None]:
    contact = session.scalar(select(Contact).where(Contact.chat_jid == payload.chat_jid))
    if not contact:
        return None, None
    conversation = session.scalar(
        select(Conversation).where(Conversation.contact_id == contact.id, Conversation.status == "active")
    )
    return contact, conversation


def should_pretriage_before_storing(session: Session, payload: BridgeInboundMessage) -> bool:
    if payload.from_me or is_ai_paused(session):
        return False
    contact, conversation = active_whatsapp_conversation_for_payload(session, payload)
    if conversation:
        return False
    return contact is None or contact.status == "active"


@router.get("/api/bridge/chat-state")
def bridge_chat_state(
    chat_jid: str = Query(min_length=1),
    session: Session = Depends(get_session),
    _bridge_scope: object = BridgeContext,
) -> dict[str, Any]:
    contact = session.scalar(select(Contact).where(Contact.chat_jid == chat_jid))
    if not contact:
        return {
            "chat_jid": chat_jid,
            "contact_status": "unknown",
            "conversation_id": None,
            "burst_mode": "triage",
            "reason": "unknown_contact",
        }

    conversation = session.scalar(
        select(Conversation).where(Conversation.contact_id == contact.id, Conversation.status == "active")
    )
    if conversation:
        return {
            "chat_jid": chat_jid,
            "contact_status": contact.status,
            "conversation_id": conversation.id,
            "burst_mode": "active_conversation",
            "reason": "active_conversation_exists",
        }

    return {
        "chat_jid": chat_jid,
        "contact_status": contact.status,
        "conversation_id": None,
        "burst_mode": "triage",
        "reason": "no_active_conversation",
    }


def render_bridge_batch_thread(messages: list[BridgeInboundMessage]) -> str:
    return "\n".join(message.text for message in messages if not message.from_me)


@router.post("/api/bridge/inbound", response_model=BridgeAck)
async def bridge_inbound(
    payload: BridgeInboundMessage,
    session: Session = Depends(get_session),
    _bridge_scope: object = BridgeContext,
) -> BridgeAck:
    if should_pretriage_before_storing(session, payload):
        triage = await run_triage_text(session, payload.text, conversation_id=None, persist_input_snapshot=False)
        if not triage_is_initial_enquiry(triage) and triage.get("stage_status") != "manual_review":
            contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
            session.commit()
            return BridgeAck(
                accepted=True,
                reason="end_no_conversation",
                data={"contact_id": contact.id, "pipeline": {"triage": triage}},
            )

    accepted, reason, data = handle_bridge_inbound(session, payload)
    if reason == "stored_inbound_message" and data.get("conversation_id") and not is_ai_paused(session):
        if "triage" in locals() and triage_is_initial_enquiry(triage):
            conversation = session.get(Conversation, int(data["conversation_id"]))
            if conversation:
                conversation.current_stage = "rental_listing_matching"
            result = {"triage": triage, **await run_rental_listing_matching_pipeline(session, int(data["conversation_id"]))}
        elif "triage" in locals() and is_manual_review_result(triage):
            result = route_triage_manual_review(session, session.get(Conversation, int(data["conversation_id"])), triage)
        else:
            result = await route_stored_conversation_after_inbound(session, int(data["conversation_id"]))
        result = await attach_outbound_action_result(session, result, int(data["conversation_id"]))
        data["pipeline"] = result
    elif reason == "stored_inbound_message" and is_ai_paused(session):
        data["pipeline"] = {"stage_status": "paused", "reason": "Global AI pause is enabled"}
    session.commit()
    return BridgeAck(accepted=accepted, reason=reason, data=data)


@router.post("/api/bridge/inbound-batch", response_model=BridgeAck)
async def bridge_inbound_batch(
    payload: BridgeInboundBatch,
    session: Session = Depends(get_session),
    _bridge_scope: object = BridgeContext,
) -> BridgeAck:
    if not payload.messages:
        return BridgeAck(accepted=True, reason="empty_batch", data={"count": 0})

    first_message = payload.messages[0]
    pretriage_result = None
    if all(message.chat_jid == first_message.chat_jid for message in payload.messages) and all(not message.from_me for message in payload.messages):
        if should_pretriage_before_storing(session, first_message):
            pretriage_result = await run_triage_text(
                session,
                render_bridge_batch_thread(payload.messages),
                conversation_id=None,
                persist_input_snapshot=False,
            )
            if not triage_is_initial_enquiry(pretriage_result) and pretriage_result.get("stage_status") != "manual_review":
                latest = max(payload.messages, key=lambda message: message.timestamp_ms)
                contact = get_or_create_contact(session, latest.chat_jid, latest.display_name)
                session.commit()
                return BridgeAck(
                    accepted=True,
                    reason="batch_end_no_conversation",
                    data={
                        "count": len(payload.messages),
                        "contact_id": contact.id,
                        "conversation_id": None,
                        "pipeline": {"triage": pretriage_result},
                    },
                )

    results = []
    pipeline_conversation_id: int | None = None
    skip_pipeline_reason: str | None = None
    for message in payload.messages:
        accepted, reason, data = handle_bridge_inbound(session, message)
        results.append({"accepted": accepted, "reason": reason, "data": data})
        if reason == "stored_inbound_message" and data.get("conversation_id"):
            pipeline_conversation_id = int(data["conversation_id"])
        if reason in {"human_reply_paused_contact", "contact_paused", "contact_ignored", "contact_reset_by_command"}:
            skip_pipeline_reason = reason

    pipeline_result = None
    if skip_pipeline_reason:
        pipeline_result = {"stage_status": "skipped", "reason": skip_pipeline_reason}
    elif pipeline_conversation_id and not is_ai_paused(session):
        conversation = session.get(Conversation, pipeline_conversation_id)
        contact = session.get(Contact, conversation.contact_id) if conversation else None
        if contact and contact.status == "active":
            if pretriage_result and triage_is_initial_enquiry(pretriage_result):
                if conversation:
                    conversation.current_stage = "rental_listing_matching"
                pipeline_result = {
                    "triage": pretriage_result,
                    **await run_rental_listing_matching_pipeline(session, pipeline_conversation_id),
                }
            elif pretriage_result and is_manual_review_result(pretriage_result):
                pipeline_result = route_triage_manual_review(session, conversation, pretriage_result)
            else:
                pipeline_result = await route_stored_conversation_after_inbound(session, pipeline_conversation_id)
            if pipeline_result:
                pipeline_result = await attach_outbound_action_result(session, pipeline_result, pipeline_conversation_id)
        else:
            pipeline_result = {"stage_status": "skipped", "reason": f"contact_{contact.status if contact else 'missing'}"}
    elif pipeline_conversation_id and is_ai_paused(session):
        pipeline_result = {"stage_status": "paused", "reason": "Global AI pause is enabled"}

    session.commit()
    return BridgeAck(
        accepted=True,
        reason="batch_processed",
        data={
            "count": len(payload.messages),
            "results": results,
            "conversation_id": pipeline_conversation_id,
            "pipeline": pipeline_result,
        },
    )
