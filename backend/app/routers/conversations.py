from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import RequestContext
from ..database.connection import get_session
from ..database.models import Contact, Conversation, Message, StageRun
from ..dependencies import DashboardContext
from ..pipeline import (
    route_stored_conversation_after_inbound,
    run_initial_enquiry_pipeline,
    run_rental_listing_matching,
)
from ..router_support import attach_outbound_action_result, build_pipeline_inspection
from ..schemas import (
    ContactOut,
    ContactStatusUpdate,
    ConversationOut,
    ConversationStageUpdate,
    MessageOut,
    PipelineRunResponse,
    StartNewEnquiryRequest,
    StageRunOut,
)
from ..services import (
    append_message,
    cancel_contact,
    close_conversation,
    ignore_contact,
    now_ms,
    pause_contact,
    resume_conversation_stage,
    start_new_enquiry,
)

router = APIRouter()


@router.get("/api/contacts", response_model=list[ContactOut])
def list_contacts(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact).order_by(Contact.updated_at.desc())
        ).all()
    )


@router.patch("/api/contacts/{contact_id}/status", response_model=ContactOut)
def update_contact_status(
    contact_id: int,
    payload: ContactStatusUpdate,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Contact:
    contact = session.scalar(select(Contact).where(Contact.id == contact_id))
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if payload.status == "paused":
        pause_contact(session, contact, payload.status_reason or "paused_by_user")
    elif payload.status == "ignored":
        ignore_contact(session, contact, payload.status_reason or "ignored_from_dashboard")
    else:
        contact.status = payload.status
        contact.status_reason = payload.status_reason or None
    session.commit()
    session.refresh(contact)
    return contact


@router.post("/api/contacts/{contact_id}/cancel", response_model=ContactOut)
def cancel_contact_route(
    contact_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Contact:
    contact = session.scalar(select(Contact).where(Contact.id == contact_id))
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    cancel_contact(session, contact)
    session.commit()
    session.refresh(contact)
    return contact


@router.get("/api/conversations", response_model=list[ConversationOut])
def list_conversations(
    include_closed: bool = Query(False),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> list[ConversationOut]:
    query = select(Conversation).order_by(Conversation.updated_at.desc())
    if not include_closed:
        query = query.where(Conversation.status != "closed")
    conversations = session.scalars(query).all()
    rows = []
    for conversation in conversations:
        latest_message = session.scalar(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.timestamp_ms.desc(), Message.id.desc())
        )
        rows.append(
            ConversationOut(
                id=conversation.id,
                contact_id=conversation.contact_id,
                source=conversation.source,
                status=conversation.status,
                current_stage=conversation.current_stage,
                matched_property_id=conversation.matched_property_id,
                latest_message_text=latest_message.text if latest_message else None,
                latest_message_timestamp_ms=latest_message.timestamp_ms if latest_message else None,
                latest_message_direction=latest_message.direction if latest_message else None,
            )
        )
    return rows


@router.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> list[Message]:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list(
        session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.timestamp_ms)
        ).all()
    )


@router.post("/api/conversations/{conversation_id}/close", response_model=ConversationOut)
def close_conversation_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Conversation:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    close_conversation(session, conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.post("/api/conversations/{conversation_id}/start-new-enquiry", response_model=ConversationOut)
def start_new_enquiry_route(
    conversation_id: int,
    payload: StartNewEnquiryRequest,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Conversation:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        next_conversation = start_new_enquiry(session, conversation)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if payload.latest_message_text.strip():
        append_text = payload.latest_message_text.strip()
        timestamp_ms = now_ms()
        append_message(
            session,
            next_conversation,
            conversation.contact.chat_jid,
            f"manual-new-enquiry-{next_conversation.id}-{timestamp_ms}",
            append_text,
            timestamp_ms,
            "inbound",
            next_conversation.source,
            conversation.contact.chat_jid,
            "manual_new_enquiry_seed",
        )
    session.commit()
    session.refresh(next_conversation)
    return next_conversation


@router.patch("/api/conversations/{conversation_id}/stage", response_model=ConversationOut)
def update_conversation_stage_route(
    conversation_id: int,
    payload: ConversationStageUpdate,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Conversation:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        resume_conversation_stage(session, conversation, payload.stage, payload.resume_contact)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    session.commit()
    session.refresh(conversation)
    return conversation


@router.post("/api/conversations/{conversation_id}/run-initial-pipeline", response_model=PipelineRunResponse)
async def run_initial_pipeline_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await run_initial_enquiry_pipeline(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@router.post("/api/conversations/{conversation_id}/run-next", response_model=PipelineRunResponse)
async def run_next_pipeline_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await route_stored_conversation_after_inbound(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@router.post("/api/conversations/{conversation_id}/run-rental-listing-matching", response_model=PipelineRunResponse)
async def run_rental_listing_matching_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await run_rental_listing_matching(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@router.get("/api/stage-runs", response_model=list[StageRunOut])
def list_stage_runs(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[StageRun]:
    return list(
        session.scalars(
            select(StageRun).order_by(StageRun.created_at.desc(), StageRun.id.desc())
        ).all()
    )


@router.get("/api/conversations/{conversation_id}/inspection")
def inspect_conversation_pipeline(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, Any]:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return jsonable_encoder(build_pipeline_inspection(session, conversation))
