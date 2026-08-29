from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import RequestContext
from ..database.connection import get_session
from ..database.models import Contact, Conversation, Message, StageRun
from ..dependencies import DashboardContext
from ..router_support import build_pipeline_inspection
from ..schemas import (
    ContactOut,
    ContactStatusUpdate,
    ConversationOut,
    MessageOut,
    StageRunOut,
)
from ..services import (
    ignore_contact,
    pause_contact,
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
