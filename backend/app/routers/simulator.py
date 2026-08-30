from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import RequestContext
from ..database.connection import get_session
from ..database.models import Contact, Conversation, Message
from ..dependencies import DashboardContext
from ..pipeline import (
    is_manual_review_result,
    record_triage_result_for_conversation,
    route_stored_conversation_after_inbound,
    run_rental_listing_matching_pipeline,
    run_triage_text,
)
from ..router_support import attach_outbound_action_result, route_triage_manual_review, triage_is_initial_enquiry
from ..schemas import FakeChatResetOut, FakeInboundMessage, MessageOut, PipelineRunResponse
from ..services import (
    get_or_create_contact,
    handle_fake_inbound,
    is_ai_paused,
    reset_fake_chat_data,
)

router = APIRouter()


@router.post("/api/fake-chat/inbound", response_model=MessageOut)
def fake_inbound(
    payload: FakeInboundMessage,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Message:
    existing_contact = session.scalar(select(Contact).where(Contact.chat_jid == payload.chat_jid))
    existing_conversation = (
        session.scalar(select(Conversation).where(Conversation.contact_id == existing_contact.id, Conversation.status == "active"))
        if existing_contact
        else None
    )
    if existing_contact and existing_contact.status == "ignored":
        session.commit()
        raise HTTPException(status_code=409, detail="Contact is ignored")
    if existing_contact and existing_contact.status == "paused" and not existing_conversation:
        session.commit()
        raise HTTPException(status_code=409, detail="Contact is paused and has no active conversation")
    message = handle_fake_inbound(session, payload)
    session.commit()
    session.refresh(message)
    return message


@router.post("/api/fake-chat/inbound-and-run", response_model=PipelineRunResponse)
async def fake_inbound_and_run(
    payload: FakeInboundMessage,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    existing_contact = session.scalar(select(Contact).where(Contact.chat_jid == payload.chat_jid))
    existing_conversation = (
        session.scalar(select(Conversation).where(Conversation.contact_id == existing_contact.id, Conversation.status == "active"))
        if existing_contact
        else None
    )
    if existing_contact and existing_contact.status == "ignored":
        session.commit()
        return PipelineRunResponse(conversation_id=None, result={"stage_status": "skipped", "reason": "contact_ignored"})

    if existing_contact and existing_contact.status == "paused":
        if existing_conversation:
            message = handle_fake_inbound(session, payload)
            session.commit()
            return PipelineRunResponse(
                conversation_id=message.conversation_id,
                result={"stage_status": "skipped", "reason": "contact_paused"},
            )
        session.commit()
        return PipelineRunResponse(conversation_id=None, result={"stage_status": "skipped", "reason": "contact_paused"})

    triage = None
    if not existing_conversation and not is_ai_paused(session):
        triage = await run_triage_text(
            session,
            payload.text,
            conversation_id=None,
            persist_input_snapshot=False,
            record_run=False,
        )
        if not triage_is_initial_enquiry(triage) and triage.get("stage_status") != "manual_review":
            contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
            session.commit()
            return PipelineRunResponse(conversation_id=None, result={"triage": triage})

    message = handle_fake_inbound(session, payload)
    if triage is not None:
        record_triage_result_for_conversation(session, message.conversation_id, triage)
    if is_ai_paused(session):
        result = {"stage_status": "paused", "reason": "Global AI pause is enabled"}
    elif triage_is_initial_enquiry(triage):
        conversation = session.get(Conversation, message.conversation_id)
        if conversation:
            conversation.current_stage = "rental_listing_matching"
        result = {"triage": triage, **await run_rental_listing_matching_pipeline(session, message.conversation_id)}
    elif is_manual_review_result(triage):
        result = route_triage_manual_review(session, session.get(Conversation, message.conversation_id), triage)
    else:
        result = await route_stored_conversation_after_inbound(session, message.conversation_id)
    result = await attach_outbound_action_result(session, result, message.conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=message.conversation_id, result=result)


@router.post("/api/fake-chat/reset", response_model=FakeChatResetOut)
def reset_fake_chat_route(
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> FakeChatResetOut:
    result = reset_fake_chat_data(session)
    session.commit()
    return FakeChatResetOut(**result)
