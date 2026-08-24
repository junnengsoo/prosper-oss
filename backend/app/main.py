import json
import logging
from datetime import datetime
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    RequestContext,
    create_session_token,
    current_user_from_request,
    resolve_dashboard_context,
    verify_access_password,
)
from .config import get_settings
from .database.connection import SessionLocal, get_session, init_db
from .database.models import Contact, Conversation, Message, Property, PropertyMedia, PropertyPlaybook, StageRun
from .schemas import (
    AppConfigOut,
    AppConfigUpdate,
    AuthLoginIn,
    AuthSessionOut,
    BridgeAck,
    BridgeInboundBatch,
    BridgeInboundMessage,
    ContactOut,
    ContactStatusUpdate,
    ConfigExportOut,
    ConversationStageUpdate,
    ConversationOut,
    FakeChatResetOut,
    FakeInboundMessage,
    HealthOut,
    MeOut,
    MessageOut,
    PipelineRunResponse,
    PropertyBulkDeleteIn,
    PropertyDeleteSummaryOut,
    PropertyIn,
    PropertyMediaIn,
    PropertyMediaOut,
    PropertyPlaybookIn,
    PropertyPlaybookOut,
    PropertyOut,
    StartNewEnquiryRequest,
    StageRunOut,
)
from .actions import execute_outbound_action_plan, plan_outbound_actions
from .llm import flush_langfuse
from .pipeline import (
    route_stored_conversation_after_inbound,
    run_initial_enquiry_pipeline,
    run_rental_listing_matching,
    run_rental_listing_matching_pipeline,
    run_triage_text,
)
from .playbooks import (
    get_property_playbook,
    list_property_playbooks,
    upsert_property_playbook,
)
from .database.seed import seed_all
from .media_storage import delete_stored_file, describe_media_storage, media_content_type, store_uploaded_file
from .services import (
    append_message,
    cancel_contact,
    close_conversation,
    delete_properties,
    delete_property,
    delete_property_media,
    fetch_bridge_pairing_qr,
    fetch_bridge_status,
    get_all_config,
    get_or_create_contact,
    handle_bridge_inbound,
    handle_fake_inbound,
    ignore_contact,
    is_ai_paused,
    list_property_media,
    now_ms,
    start_new_enquiry,
    pause_contact,
    resume_conversation_stage,
    reset_fake_chat_data,
    request_bridge_reconnect,
    timestamp_to_datetime,
    update_config,
    upsert_property,
    upsert_property_media,
)


logger = logging.getLogger(__name__)

app = FastAPI(title="WhatsApp PA MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_bridge_headers(
    x_whatsapp_bridge_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    expected_token = settings.whatsapp_pa_bridge_token.strip()
    if settings.auth_required and not expected_token:
        raise HTTPException(status_code=500, detail="WHATSAPP_PA_BRIDGE_TOKEN is required when AUTH_REQUIRED=true")
    if expected_token and (not x_whatsapp_bridge_token or not compare_digest(x_whatsapp_bridge_token, expected_token)):
        raise HTTPException(status_code=401, detail="Invalid bridge token")

async def dashboard_context_dependency(
    request: Request,
):
    yield resolve_dashboard_context(request)


async def bridge_context_dependency(
    x_whatsapp_bridge_token: str | None = Header(default=None),
):
    verify_bridge_headers(x_whatsapp_bridge_token)
    yield None


def dashboard_user_dependency(request: Request) -> CurrentUser:
    return current_user_from_request(request)


DashboardContext = Depends(dashboard_context_dependency)
DashboardUser = Depends(dashboard_user_dependency)
BridgeContext = Depends(bridge_context_dependency)


def route_context(context: RequestContext | object = None) -> RequestContext:
    if isinstance(context, RequestContext):
        return context
    return RequestContext(
        user=CurrentUser(
            auth_user_id="dev-user",
            email="dev@local.test",
            claims={"sub": "dev-user", "email": "dev@local.test"},
            is_dev_fallback=True,
        ),
        role="owner",
    )


def triage_is_initial_enquiry(triage: dict[str, Any] | None) -> bool:
    """Return whether triage classified a thread as an initial rental enquiry."""
    if not isinstance(triage, dict):
        return False
    return triage.get("is_initial_rental_enquiry") is True


async def attach_outbound_action_result(session: Session, result: dict, conversation_id: int | None = None) -> dict:
    if conversation_id is not None and "sent_actions" not in result:
        result = await execute_outbound_action_plan(session, conversation_id, result)
    result["outbound_actions"] = result.get("send_result", {"status": "not_attempted", "reason": "no_conversation"})
    flush_langfuse()
    return result


def parse_stage_output(run: StageRun) -> dict[str, Any] | None:
    if not run.output_json:
        return None
    try:
        parsed = json.loads(run.output_json)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def build_pipeline_inspection(session: Session, conversation: Conversation) -> dict[str, Any]:
    runs = list(
        session.scalars(
            select(StageRun)
            .where(StageRun.conversation_id == conversation.id)
            .order_by(StageRun.created_at.asc(), StageRun.id.asc())
        ).all()
    )
    pipeline_result: dict[str, Any] = {}
    for run in runs:
        parsed = parse_stage_output(run)
        if run.status == "success" and parsed:
            pipeline_result[run.stage] = parsed

    planned_actions = [action.to_dict() for action in plan_outbound_actions(conversation, pipeline_result, session)]
    return {
        "conversation_id": conversation.id,
        "pipeline_result": pipeline_result,
        "planned_actions": planned_actions,
        "stage_runs": [
            {
                "id": run.id,
                "stage": run.stage,
                "status": run.status,
                "output": parse_stage_output(run),
                "error": run.error,
                "model": run.model,
                "created_at": run.created_at,
            }
            for run in reversed(runs)
        ],
    }


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as session:
        seed_all(session)


@app.on_event("shutdown")
def shutdown() -> None:
    flush_langfuse()


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(ok=True, app="whatsapp-pa")


@app.post("/api/auth/login", response_model=AuthSessionOut)
def login(payload: AuthLoginIn, response: Response) -> AuthSessionOut:
    settings = get_settings()
    if not settings.auth_required:
        return AuthSessionOut(authenticated=True, email="dev@local.test")
    verify_access_password(payload.password)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return AuthSessionOut(authenticated=True)


@app.get("/api/auth/session", response_model=AuthSessionOut)
def auth_session(request: Request) -> AuthSessionOut:
    user = current_user_from_request(request)
    return AuthSessionOut(authenticated=True, email=user.email)


@app.post("/api/auth/logout", response_model=AuthSessionOut)
def logout(response: Response) -> AuthSessionOut:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return AuthSessionOut(authenticated=False)


@app.get("/api/me", response_model=MeOut)
def get_me(
    user: CurrentUser = DashboardUser,
) -> MeOut:
    return MeOut(auth_user_id=user.auth_user_id, email=user.email)


@app.get("/api/config", response_model=AppConfigOut)
def list_config(session: Session = Depends(get_session), _context: RequestContext = DashboardContext) -> AppConfigOut:
    return AppConfigOut(values=get_all_config(session))


@app.patch("/api/config", response_model=AppConfigOut)
def patch_config(
    payload: AppConfigUpdate,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> AppConfigOut:
    try:
        values = update_config(session, payload.values)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    session.commit()
    return AppConfigOut(values=values)


@app.get("/api/config/export", response_model=ConfigExportOut)
def export_config(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> ConfigExportOut:
    return ConfigExportOut(
        exported_at=datetime.now(),
        app="whatsapp-pa",
        config=get_all_config(session),
        properties=list(session.scalars(select(Property).order_by(Property.property_id)).all()),
        property_media=list(
            session.scalars(
                select(PropertyMedia)
                .order_by(PropertyMedia.property_id, PropertyMedia.sort_order, PropertyMedia.id)
            ).all()
        ),
        playbooks=list_property_playbooks(session),
    )


def llm_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "provider": "deepseek",
        "configured": bool(settings.deepseek_api_key),
        "model": settings.deepseek_model,
        "base_url": settings.deepseek_base_url,
    }


def whatsapp_connection_state(bridge: dict[str, object]) -> str:
    if bridge.get("available") is False or (bridge.get("ok") is False and not bridge.get("connection")):
        return "bridge_offline"
    if bridge.get("connection") == "open":
        return "connected"
    if bridge.get("last_disconnect_requires_reauth") is True:
        return "needs_reauth"
    pairing = bridge.get("pairing")
    if isinstance(pairing, dict):
        if pairing.get("qr_available") is True:
            return "qr_available"
        if pairing.get("qr_expired") is True:
            return "qr_expired"
    if bridge.get("connection") in {"connecting", "starting"}:
        return "connecting"
    return "disconnected"


def bridge_base_url_for_route() -> str | None:
    return get_settings().bridge_base_url


@app.get("/api/runtime/status")
async def runtime_status(
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    return {
        "app": "whatsapp-pa",
        "config": get_all_config(session),
        "llm": llm_status(),
        "bridge": await fetch_bridge_status(get_settings().bridge_base_url),
    }


@app.get("/api/whatsapp/connection")
async def whatsapp_connection(
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    bridge = await fetch_bridge_status(get_settings().bridge_base_url)
    return {
        "state": whatsapp_connection_state(bridge),
        "bridge": bridge,
    }


@app.get("/api/whatsapp/qr")
async def whatsapp_pairing_qr(
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, object]:
    qr = await fetch_bridge_pairing_qr(bridge_base_url_for_route())
    state = "qr_available" if qr.get("ok") is True else str(qr.get("status") or "qr_unavailable")
    return {
        "state": state,
        **qr,
    }


@app.post("/api/whatsapp/reconnect")
async def whatsapp_reconnect(
    clear_auth: bool = Query(default=False),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, object]:
    bridge_base_url = bridge_base_url_for_route()
    bridge = await fetch_bridge_status(bridge_base_url)
    clear_auth_requested = clear_auth is True
    should_clear_auth = clear_auth_requested or bridge.get("last_disconnect_requires_reauth") is True
    result = await request_bridge_reconnect(bridge_base_url, clear_auth=should_clear_auth)
    return {
        "state": "reconnecting" if result.get("ok") is True else str(result.get("status") or "bridge_offline"),
        "clear_auth": should_clear_auth,
        **result,
    }


@app.get("/api/properties", response_model=list[PropertyOut])
def list_properties(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[Property]:
    return list(
        session.scalars(
            select(Property).order_by(Property.property_id)
        ).all()
    )


@app.post("/api/properties", response_model=PropertyOut)
def create_or_update_property(
    payload: PropertyIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> Property:
    property_ = upsert_property(session, payload)
    session.commit()
    session.refresh(property_)
    return property_


@app.post("/api/properties/bulk-delete", response_model=PropertyDeleteSummaryOut)
def bulk_delete_properties_route(
    payload: PropertyBulkDeleteIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    try:
        summary = delete_properties(session, payload.property_ids)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return summary


@app.delete("/api/properties/{property_id}", response_model=PropertyDeleteSummaryOut)
def delete_property_route(
    property_id: str,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    try:
        summary = delete_property(session, property_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return summary


@app.get("/api/properties/{property_id}/playbook", response_model=PropertyPlaybookOut)
def get_property_playbook_route(
    property_id: str,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyPlaybook | dict[str, Any]:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")
    playbook = get_property_playbook(session, property_id)
    if not playbook:
        return {
            "id": None,
            "property_id": property_id,
            "initial_reply_blocks": [],
            "enabled": False,
            "created_at": None,
            "updated_at": None,
        }
    return playbook


@app.put("/api/properties/{property_id}/playbook", response_model=PropertyPlaybookOut)
def put_property_playbook_route(
    property_id: str,
    payload: PropertyPlaybookIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> PropertyPlaybook:
    try:
        playbook = upsert_property_playbook(session, property_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    session.commit()
    session.refresh(playbook)
    return playbook


@app.get("/api/properties/{property_id}/media", response_model=list[PropertyMediaOut])
def list_property_media_route(
    property_id: str,
    include_disabled: bool = Query(False),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> list[PropertyMedia]:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")
    return list_property_media(session, property_id, include_disabled=include_disabled)


@app.post("/api/properties/{property_id}/media", response_model=PropertyMediaOut)
def create_or_update_property_media(
    property_id: str,
    payload: PropertyMediaIn,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> PropertyMedia:
    try:
        media = upsert_property_media(session, property_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    session.refresh(media)
    return media


@app.post("/api/properties/{property_id}/media/upload", response_model=PropertyMediaOut)
def upload_property_media_route(
    property_id: str,
    media_type: str = Form("photo"),
    caption: str = Form(""),
    sort_order: int = Form(0),
    enabled: bool = Form(True),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyMedia:
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")

    filename = Path(file.filename or "property-media").name
    stored_path: str | None = None
    try:
        stored = store_uploaded_file(
            file.file,
            property_id=property_id,
            filename=filename,
            max_bytes=get_settings().media_max_upload_bytes,
        )
        stored_path = stored.file_path
        media = upsert_property_media(
            session,
            property_id,
            PropertyMediaIn(
                media_type=media_type,
                file_path=stored.file_path,
                caption=caption,
                sort_order=sort_order,
                enabled=enabled,
            ),
        )
    except ValueError as error:
        logger.info(
            "Property media upload rejected property_id=%s filename=%s error=%s",
            property_id,
            filename,
            error,
        )
        if stored_path:
            delete_stored_file(stored_path)
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        file.file.close()

    session.commit()
    session.refresh(media)
    return media


@app.delete("/api/property-media/{media_id}", response_model=PropertyMediaOut)
def delete_property_media_route(
    media_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyMedia:
    media = session.scalar(select(PropertyMedia).where(PropertyMedia.id == media_id))
    if not media:
        raise HTTPException(status_code=404, detail="Property media not found")

    try:
        delete_stored_file(media.file_path)
    except ValueError:
        logger.info("Media path is outside managed runtime storage; deleting database row only media_id=%s", media.id)

    media = delete_property_media(session, media_id)
    session.commit()
    return media


@app.get("/api/property-media/{media_id}/content")
def serve_property_media(
    media_id: int,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> FileResponse:
    media = session.get(PropertyMedia, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Property media not found")
    descriptor = describe_media_storage(media)
    if not descriptor.local_file_exists:
        raise HTTPException(status_code=404, detail="Media file not found")
    try:
        media_path = Path(media.file_path).expanduser().resolve()
        media_path.relative_to(get_settings().media_root.expanduser().resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media file is outside managed storage") from error
    return FileResponse(media_path, media_type=media_content_type(str(media_path)), filename=media_path.name)


@app.get("/api/contacts", response_model=list[ContactOut])
def list_contacts(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact).order_by(Contact.updated_at.desc())
        ).all()
    )


@app.patch("/api/contacts/{contact_id}/status", response_model=ContactOut)
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


@app.post("/api/contacts/{contact_id}/cancel", response_model=ContactOut)
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


@app.get("/api/conversations", response_model=list[ConversationOut])
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


@app.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageOut])
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


@app.post("/api/conversations/{conversation_id}/close", response_model=ConversationOut)
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


@app.post("/api/conversations/{conversation_id}/start-new-enquiry", response_model=ConversationOut)
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


@app.patch("/api/conversations/{conversation_id}/stage", response_model=ConversationOut)
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


@app.post("/api/fake-chat/inbound", response_model=MessageOut)
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
        existing_contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms or 0) if payload.timestamp_ms else existing_contact.last_message_at
        session.commit()
        raise HTTPException(status_code=409, detail="Contact is ignored")
    if existing_contact and existing_contact.status == "paused" and not existing_conversation:
        existing_contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms or 0) if payload.timestamp_ms else existing_contact.last_message_at
        session.commit()
        raise HTTPException(status_code=409, detail="Contact is paused and has no active conversation")
    message = handle_fake_inbound(session, payload)
    session.commit()
    session.refresh(message)
    return message


@app.post("/api/fake-chat/inbound-and-run", response_model=PipelineRunResponse)
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
        existing_contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms or 0) if payload.timestamp_ms else existing_contact.last_message_at
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
        existing_contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms or 0) if payload.timestamp_ms else existing_contact.last_message_at
        session.commit()
        return PipelineRunResponse(conversation_id=None, result={"stage_status": "skipped", "reason": "contact_paused"})

    if not existing_conversation and not is_ai_paused(session):
        triage = await run_triage_text(session, payload.text, conversation_id=None, persist_input_snapshot=False)
        if not triage_is_initial_enquiry(triage) and triage.get("stage_status") != "manual_review":
            contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
            contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms or 0) if payload.timestamp_ms else contact.last_message_at
            session.commit()
            flush_langfuse()
            return PipelineRunResponse(conversation_id=None, result={"triage": triage})

    message = handle_fake_inbound(session, payload)
    if is_ai_paused(session):
        result = {"stage_status": "paused", "reason": "Global AI pause is enabled"}
    elif "triage" in locals() and triage_is_initial_enquiry(triage):
        conversation = session.get(Conversation, message.conversation_id)
        if conversation:
            conversation.current_stage = "rental_listing_matching"
        result = {"triage": triage, **await run_rental_listing_matching_pipeline(session, message.conversation_id)}
    else:
        result = await route_stored_conversation_after_inbound(session, message.conversation_id)
    result = await attach_outbound_action_result(session, result, message.conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=message.conversation_id, result=result)


@app.post("/api/fake-chat/reset", response_model=FakeChatResetOut)
def reset_fake_chat_route(
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> FakeChatResetOut:
    result = reset_fake_chat_data(session)
    session.commit()
    return FakeChatResetOut(**result)


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


@app.get("/api/bridge/chat-state")
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


@app.post("/api/bridge/inbound", response_model=BridgeAck)
async def bridge_inbound(
    payload: BridgeInboundMessage,
    session: Session = Depends(get_session),
    _bridge_scope: object = BridgeContext,
) -> BridgeAck:
    if should_pretriage_before_storing(session, payload):
        triage = await run_triage_text(session, payload.text, conversation_id=None, persist_input_snapshot=False)
        if not triage_is_initial_enquiry(triage) and triage.get("stage_status") != "manual_review":
            contact = get_or_create_contact(session, payload.chat_jid, payload.display_name)
            contact.last_message_at = timestamp_to_datetime(payload.timestamp_ms)
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
        else:
            result = await route_stored_conversation_after_inbound(session, int(data["conversation_id"]))
        result = await attach_outbound_action_result(session, result, int(data["conversation_id"]))
        data["pipeline"] = result
    elif reason == "stored_inbound_message" and is_ai_paused(session):
        data["pipeline"] = {"stage_status": "paused", "reason": "Global AI pause is enabled"}
    session.commit()
    return BridgeAck(accepted=accepted, reason=reason, data=data)


@app.post("/api/bridge/inbound-batch", response_model=BridgeAck)
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
                contact.last_message_at = timestamp_to_datetime(latest.timestamp_ms)
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


@app.post("/api/conversations/{conversation_id}/run-initial-pipeline", response_model=PipelineRunResponse)
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


@app.post("/api/conversations/{conversation_id}/run-next", response_model=PipelineRunResponse)
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


@app.post("/api/conversations/{conversation_id}/run-rental-listing-matching", response_model=PipelineRunResponse)
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


@app.get("/api/stage-runs", response_model=list[StageRunOut])
def list_stage_runs(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[StageRun]:
    return list(
        session.scalars(
            select(StageRun).order_by(StageRun.created_at.desc(), StageRun.id.desc())
        ).all()
    )


@app.get("/api/conversations/{conversation_id}/inspection")
def inspect_conversation_pipeline(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, Any]:
    conversation = session.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return jsonable_encoder(build_pipeline_inspection(session, conversation))
