import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from hmac import compare_digest
from pathlib import Path
import tempfile
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    RequestContext,
    create_session_token,
    current_user_from_request,
    resolve_dashboard_context,
    resolve_workspace_context,
    verify_access_password,
)
from .config import get_settings
from .db import SessionLocal, get_session, init_db
from .models import Contact, Conversation, Message, Property, PropertyMedia, PropertyPlaybook, StageRun, SwingCandidate, WhatsappAccount, Workspace, WorkspaceMember
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
    SwingCandidateIn,
    SwingCandidateOut,
    WhatsappAccountOut,
    WorkspaceOut,
)
from .actions import execute_outbound_action_plan, plan_outbound_actions
from .llm import flush_langfuse
from .pipeline import (
    route_stored_conversation_after_inbound,
    run_initial_enquiry_pipeline,
    run_qualification,
    run_swinging,
    run_triage_text,
    run_unit_matching,
    run_unit_matching_then_maybe_qualification,
)
from .playbooks import (
    get_property_playbook,
    list_property_playbooks,
    upsert_property_playbook,
)
from .readiness import runtime_summary, runtime_warnings
from .seed import seed_all
from .status_page import render_demo_conversation, render_demo_overview
from .supabase_storage import (
    SupabaseStorageError,
    build_property_media_object_path,
    delete_file_from_supabase_storage,
    supabase_storage_config_from_settings,
    upload_file_to_supabase_storage,
)
from .services import (
    append_message,
    cancel_contact,
    close_conversation,
    delete_properties,
    delete_property,
    delete_property_media,
    delete_swing_candidate,
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
    upsert_swing_candidate,
)
from .swing import swing_candidate_validity
from .tenant import DEFAULT_WHATSAPP_ACCOUNT_ID, WorkspaceScope, account_conditions, current_workspace_scope, reset_workspace_scope, set_current_workspace_scope, workspace_conditions


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


def normalize_whatsapp_account_id(value: str | None) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", (value or "").strip().lower()).strip("_")


def verify_bridge_headers(
    x_whatsapp_account_id: str | None = Header(default=None),
    x_whatsapp_bridge_token: str | None = Header(default=None),
) -> str:
    settings = get_settings()
    expected_token = settings.whatsapp_pa_bridge_token.strip()
    provided_account_id = normalize_whatsapp_account_id(x_whatsapp_account_id)
    if settings.auth_required and not expected_token:
        raise HTTPException(status_code=500, detail="WHATSAPP_PA_BRIDGE_TOKEN is required when AUTH_REQUIRED=true")
    if expected_token and (not x_whatsapp_bridge_token or not compare_digest(x_whatsapp_bridge_token, expected_token)):
        raise HTTPException(status_code=401, detail="Invalid bridge token")

    expected_account_id = normalize_whatsapp_account_id(settings.whatsapp_account_id)
    if expected_account_id and provided_account_id and provided_account_id != expected_account_id:
        raise HTTPException(status_code=403, detail="Invalid WhatsApp account")
    return provided_account_id or expected_account_id


async def dashboard_context_dependency(
    request: Request,
    session: Session = Depends(get_session),
):
    context = resolve_dashboard_context(session, request)
    token = set_current_workspace_scope(route_scope(context))
    try:
        yield context
    finally:
        reset_workspace_scope(token)


async def bridge_context_dependency(
    session: Session = Depends(get_session),
    x_whatsapp_account_id: str | None = Header(default=None),
    x_whatsapp_bridge_token: str | None = Header(default=None),
):
    provided_account = verify_bridge_headers(x_whatsapp_account_id, x_whatsapp_bridge_token)
    account = None
    if provided_account:
        account = session.get(WhatsappAccount, provided_account)
        if account and account.status != "active":
            account = None
        if not account:
            key_matches = list(
                session.scalars(
                    select(WhatsappAccount)
                    .where(WhatsappAccount.status == "active", WhatsappAccount.account_key == provided_account)
                    .order_by(WhatsappAccount.id)
                ).all()
            )
            if len(key_matches) == 1:
                account = key_matches[0]
            elif len(key_matches) > 1:
                raise HTTPException(status_code=403, detail="Ambiguous WhatsApp account")
        if not account and get_settings().whatsapp_pa_bridge_token.strip():
            raise HTTPException(status_code=403, detail="Unknown WhatsApp account")
    if not account:
        account = session.get(WhatsappAccount, DEFAULT_WHATSAPP_ACCOUNT_ID)
    scope = (
        WorkspaceScope(account.workspace_id, account.id)
        if account
        else WorkspaceScope()
    )
    token = set_current_workspace_scope(scope)
    try:
        yield scope
    finally:
        reset_workspace_scope(token)


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
        scope=current_workspace_scope(),
        role="owner",
    )


def route_scope(context: RequestContext | object = None) -> WorkspaceScope:
    return route_context(context).scope


def triage_is_initial_enquiry(triage: dict[str, Any] | None) -> bool:
    """Return whether triage classified a thread as an initial property enquiry.

    The old prompt used `is_initial_rental_enquiry`; keep accepting it so older
    manual tests and traces remain compatible while sale enquiries move to the
    broader `is_initial_property_enquiry` key.
    """
    if not isinstance(triage, dict):
        return False
    return triage.get("is_initial_property_enquiry") is True or triage.get("is_initial_rental_enquiry") is True


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


@app.get("/", response_class=HTMLResponse)
async def demo_overview_page(
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> HTMLResponse:
    runtime = await runtime_status(session)
    return HTMLResponse(await render_demo_overview(session, runtime))


@app.get("/demo/conversations/{conversation_id}", response_class=HTMLResponse)
async def demo_conversation_page(
    conversation_id: int,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> HTMLResponse:
    runtime = await runtime_status(session)
    return HTMLResponse(await render_demo_conversation(session, runtime, conversation_id))


@app.post("/demo/conversations/{conversation_id}/close")
def demo_close_conversation(
    conversation_id: int,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> RedirectResponse:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    close_conversation(session, conversation)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/demo/contacts/{contact_id}/unpause")
def demo_unpause_contact(
    contact_id: int,
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> RedirectResponse:
    contact = session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.status = "active"
    contact.status_reason = "resumed_from_demo_monitor"
    session.commit()
    return RedirectResponse(url="/", status_code=303)


def workspace_out_with_role(workspace: Workspace, role: str | None = None) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        status=workspace.status,
        role=role,
    )


@app.get("/api/me", response_model=MeOut)
def get_me(
    session: Session = Depends(get_session),
    user: CurrentUser = DashboardUser,
    x_workspace_id: str | None = Header(default=None),
) -> MeOut:
    memberships = list(
        session.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.auth_user_id == user.auth_user_id, WorkspaceMember.status == "active")
            .order_by(WorkspaceMember.id)
        ).all()
    )
    context: RequestContext | None = None
    if user.is_dev_fallback and not memberships:
        context = resolve_workspace_context(session, user, x_workspace_id)
        workspace = session.get(Workspace, route_scope(context).workspace_id)
        workspaces = [workspace_out_with_role(workspace, route_context(context).role)] if workspace else []
    else:
        workspaces = []
        for membership in memberships:
            workspace = session.get(Workspace, membership.workspace_id)
            if workspace:
                workspaces.append(workspace_out_with_role(workspace, membership.role))

    active_workspace: WorkspaceOut | None = None
    if x_workspace_id or len(workspaces) <= 1:
        context = resolve_workspace_context(session, user, x_workspace_id)
        active_workspace = next((workspace for workspace in workspaces if workspace.id == route_scope(context).workspace_id), None)
        if not active_workspace:
            workspace = session.get(Workspace, route_scope(context).workspace_id)
            if not workspace:
                raise HTTPException(status_code=403, detail="Active workspace not found")
            active_workspace = workspace_out_with_role(workspace, route_context(context).role)
            workspaces.append(active_workspace)

    return MeOut(
        auth_user_id=user.auth_user_id,
        email=user.email,
        workspace=active_workspace,
        workspaces=workspaces,
        whatsapp_account_id=route_scope(context).whatsapp_account_id if context else None,
    )


@app.get("/api/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    session: Session = Depends(get_session),
    user: CurrentUser = DashboardUser,
) -> list[WorkspaceOut]:
    if user.is_dev_fallback:
        context = resolve_workspace_context(session, user)
        workspace = session.get(Workspace, route_scope(context).workspace_id)
        return [workspace_out_with_role(workspace, route_context(context).role)] if workspace else []
    memberships = list(
        session.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.auth_user_id == user.auth_user_id, WorkspaceMember.status == "active")
            .order_by(WorkspaceMember.id)
        ).all()
    )
    rows: list[WorkspaceOut] = []
    for membership in memberships:
        workspace = session.get(Workspace, membership.workspace_id)
        if workspace:
            rows.append(workspace_out_with_role(workspace, membership.role))
    return rows


@app.get("/api/whatsapp-accounts", response_model=list[WhatsappAccountOut])
def list_whatsapp_accounts(
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> list[WhatsappAccount]:
    return list(
        session.scalars(
            select(WhatsappAccount)
            .where(WhatsappAccount.workspace_id == route_scope(context).workspace_id)
            .order_by(WhatsappAccount.created_at.desc(), WhatsappAccount.id)
        ).all()
    )


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
        properties=list(session.scalars(select(Property).where(*workspace_conditions(Property, route_scope(context).workspace_id)).order_by(Property.property_id)).all()),
        property_media=list(
            session.scalars(
                select(PropertyMedia)
                .where(*workspace_conditions(PropertyMedia, route_scope(context).workspace_id))
                .order_by(PropertyMedia.property_id, PropertyMedia.sort_order, PropertyMedia.id)
            ).all()
        ),
        swing_candidates=list(
            session.scalars(
                select(SwingCandidate)
                .where(*workspace_conditions(SwingCandidate, route_scope(context).workspace_id))
                .order_by(SwingCandidate.source_property_id, SwingCandidate.sort_order)
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


def bridge_base_url_for_route(session: Session, context: RequestContext) -> str | None:
    scope = route_scope(context)
    account = session.get(WhatsappAccount, scope.whatsapp_account_id)
    return account.bridge_base_url if account else None


@app.get("/api/runtime/status")
async def runtime_status(
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, object]:
    scope = route_scope(context)
    account = session.get(WhatsappAccount, scope.whatsapp_account_id)
    return {
        "app": "whatsapp-pa",
        "config": get_all_config(session),
        "summary": runtime_summary(session),
        "warnings": runtime_warnings(session),
        "llm": llm_status(),
        "bridge": await fetch_bridge_status(account.bridge_base_url if account else None),
    }


@app.get("/api/whatsapp/connection")
async def whatsapp_connection(
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, object]:
    bridge = await fetch_bridge_status(bridge_base_url_for_route(session, route_context(context)))
    return {
        "state": whatsapp_connection_state(bridge),
        "bridge": bridge,
    }


@app.get("/api/whatsapp/qr")
async def whatsapp_pairing_qr(
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, object]:
    qr = await fetch_bridge_pairing_qr(bridge_base_url_for_route(session, route_context(context)))
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
    bridge_base_url = bridge_base_url_for_route(session, route_context(context))
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
            select(Property).where(*workspace_conditions(Property, route_scope(context).workspace_id)).order_by(Property.property_id)
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


@app.get("/api/playbooks", response_model=list[PropertyPlaybookOut])
def list_playbooks_route(
    session: Session = Depends(get_session),
    _context: RequestContext = DashboardContext,
) -> list[PropertyPlaybook]:
    return list_property_playbooks(session)


@app.get("/api/properties/{property_id}/playbook", response_model=PropertyPlaybookOut)
def get_property_playbook_route(
    property_id: str,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyPlaybook | dict[str, Any]:
    property_ = session.scalar(select(Property).where(*workspace_conditions(Property, route_scope(context).workspace_id), Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")
    playbook = get_property_playbook(session, property_id)
    if not playbook:
        return {
            "id": None,
            "workspace_id": route_scope(context).workspace_id,
            "property_id": property_id,
            "initial_reply_blocks": [],
            "qualification_suitable_blocks": [],
            "qualification_not_suitable_blocks": [],
            "swing_suggestion_blocks": [],
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
    property_ = session.scalar(select(Property).where(*workspace_conditions(Property, route_scope(context).workspace_id), Property.property_id == property_id))
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
    property_ = session.scalar(select(Property).where(*workspace_conditions(Property, route_scope(context).workspace_id), Property.property_id == property_id))
    if not property_:
        raise HTTPException(status_code=404, detail="Property not found")

    filename = Path(file.filename or "property-media").name
    object_path = build_property_media_object_path(
        property_id,
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{filename}",
    )
    suffix = Path(filename).suffix
    temp_path: Path | None = None
    written_bytes = 0
    try:
        storage_config = supabase_storage_config_from_settings()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := file.file.read(1024 * 1024):
                written_bytes += len(chunk)
                if written_bytes > storage_config.max_upload_bytes:
                    raise ValueError(f"upload file exceeds limit of {storage_config.max_upload_bytes} bytes")
                temp_file.write(chunk)

        uploaded = upload_file_to_supabase_storage(
            temp_path,
            object_path,
            config=storage_config,
            content_type=file.content_type,
        )
        media = upsert_property_media(
            session,
            property_id,
            PropertyMediaIn(
                media_type=media_type,
                caption=caption,
                sort_order=sort_order,
                enabled=enabled,
                **uploaded.as_property_media_values(),
            ),
        )
    except ValueError as error:
        logger.info(
            "Property media upload rejected property_id=%s filename=%s bytes=%s object_path=%s error=%s",
            property_id,
            filename,
            written_bytes,
            object_path,
            error,
        )
        raise HTTPException(status_code=400, detail=str(error)) from error
    except SupabaseStorageError as error:
        logger.warning(
            "Supabase Storage upload failed property_id=%s filename=%s bytes=%s object_path=%s error=%s",
            property_id,
            filename,
            written_bytes,
            object_path,
            error,
        )
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        file.file.close()
        if temp_path and temp_path.exists():
            temp_path.unlink()

    session.commit()
    session.refresh(media)
    return media


@app.delete("/api/property-media/{media_id}", response_model=PropertyMediaOut)
def delete_property_media_route(
    media_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PropertyMedia:
    media = session.scalar(select(PropertyMedia).where(*workspace_conditions(PropertyMedia, route_scope(context).workspace_id), PropertyMedia.id == media_id))
    if not media:
        raise HTTPException(status_code=404, detail="Property media not found")

    if media.storage_provider == "supabase" and media.storage_object_path:
        storage_config = supabase_storage_config_from_settings()
        if media.storage_bucket:
            storage_config = replace(storage_config, bucket=media.storage_bucket)
        try:
            delete_file_from_supabase_storage(media.storage_object_path, config=storage_config)
        except (ValueError, SupabaseStorageError) as error:
            logger.warning(
                "Supabase Storage delete failed media_id=%s bucket=%s object_path=%s error=%s",
                media.id,
                media.storage_bucket,
                media.storage_object_path,
                error,
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
    elif media.storage_provider == "supabase":
        logger.warning("Supabase media row missing object path; deleting database row only media_id=%s", media.id)

    media = delete_property_media(session, media_id)
    session.commit()
    return media


@app.get("/api/swing-candidates", response_model=list[SwingCandidateOut])
def list_swing_candidates(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[dict[str, Any]]:
    scope = route_scope(context)
    candidates = list(
        session.scalars(
            select(SwingCandidate)
            .where(*workspace_conditions(SwingCandidate, scope.workspace_id))
            .order_by(SwingCandidate.source_property_id, SwingCandidate.sort_order)
        ).all()
    )
    return [_swing_candidate_payload(session, candidate, scope.workspace_id) for candidate in candidates]


def _swing_candidate_payload(session: Session, candidate: SwingCandidate, workspace_id: str) -> dict[str, Any]:
    property_ = session.scalar(
        select(Property).where(*workspace_conditions(Property, workspace_id), Property.property_id == candidate.candidate_property_id)
    )
    return {
        "id": candidate.id,
        "source_property_id": candidate.source_property_id,
        "candidate_property_id": candidate.candidate_property_id,
        "sort_order": candidate.sort_order,
        "enabled": candidate.enabled,
        "candidate_property_name": property_.property_name if property_ else None,
        **swing_candidate_validity(candidate.candidate_property_id, property_),
    }


@app.post("/api/swing-candidates", response_model=SwingCandidateOut)
def create_or_update_swing_candidate(
    payload: SwingCandidateIn,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, Any]:
    scope = route_scope(context)
    source = session.scalar(select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == payload.source_property_id))
    candidate_property = session.scalar(select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == payload.candidate_property_id))
    if not source or not candidate_property:
        raise HTTPException(status_code=404, detail="Source and candidate properties must both exist")
    candidate = upsert_swing_candidate(session, payload)
    session.commit()
    session.refresh(candidate)
    return _swing_candidate_payload(session, candidate, scope.workspace_id)


@app.delete("/api/swing-candidates/{candidate_id}", response_model=SwingCandidateOut)
def delete_swing_candidate_route(
    candidate_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, Any]:
    scope = route_scope(context)
    candidate = session.scalar(select(SwingCandidate).where(*workspace_conditions(SwingCandidate, scope.workspace_id), SwingCandidate.id == candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Swing candidate not found")
    payload = _swing_candidate_payload(session, candidate, scope.workspace_id)
    candidate = delete_swing_candidate(session, candidate_id)
    session.commit()
    return payload


@app.get("/api/contacts", response_model=list[ContactOut])
def list_contacts(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact).where(*workspace_conditions(Contact, route_scope(context).workspace_id)).order_by(Contact.updated_at.desc())
        ).all()
    )


@app.patch("/api/contacts/{contact_id}/status", response_model=ContactOut)
def update_contact_status(
    contact_id: int,
    payload: ContactStatusUpdate,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Contact:
    contact = session.scalar(select(Contact).where(*workspace_conditions(Contact, route_scope(context).workspace_id), Contact.id == contact_id))
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
    contact = session.scalar(select(Contact).where(*workspace_conditions(Contact, route_scope(context).workspace_id), Contact.id == contact_id))
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
    query = select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id)).order_by(Conversation.updated_at.desc())
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
                host_property_id=conversation.host_property_id,
                matched_property_id=conversation.matched_property_id,
                current_suggested_property_id=conversation.current_suggested_property_id,
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
    conversation = session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list(
        session.scalars(
            select(Message)
            .where(*workspace_conditions(Message, route_scope(context).workspace_id), Message.conversation_id == conversation_id)
            .order_by(Message.timestamp_ms)
        ).all()
    )


@app.post("/api/conversations/{conversation_id}/close", response_model=ConversationOut)
def close_conversation_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> Conversation:
    conversation = session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id))
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
    conversation = session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id))
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
    conversation = session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id))
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
    existing_contact = session.scalar(select(Contact).where(*workspace_conditions(Contact, route_scope(context).workspace_id), Contact.chat_jid == payload.chat_jid))
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
    existing_contact = session.scalar(select(Contact).where(*workspace_conditions(Contact, route_scope(context).workspace_id), Contact.chat_jid == payload.chat_jid))
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
            conversation.current_stage = "unit_matching"
        result = {"triage": triage, **await run_unit_matching_then_maybe_qualification(session, message.conversation_id)}
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
    scope = current_workspace_scope()
    contact = session.scalar(select(Contact).where(*account_conditions(Contact, scope), Contact.chat_jid == payload.chat_jid))
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
    _bridge_scope: WorkspaceScope = BridgeContext,
) -> dict[str, Any]:
    contact = session.scalar(select(Contact).where(*account_conditions(Contact, route_scope(_bridge_scope)), Contact.chat_jid == chat_jid))
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
    _bridge_scope: WorkspaceScope = BridgeContext,
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
                conversation.current_stage = "unit_matching"
            result = {"triage": triage, **await run_unit_matching_then_maybe_qualification(session, int(data["conversation_id"]))}
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
    _bridge_scope: WorkspaceScope = BridgeContext,
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
                    conversation.current_stage = "unit_matching"
                pipeline_result = {
                    "triage": pretriage_result,
                    **await run_unit_matching_then_maybe_qualification(session, pipeline_conversation_id),
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
    if not session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id)):
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
    if not session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await route_stored_conversation_after_inbound(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@app.post("/api/conversations/{conversation_id}/run-unit-matching", response_model=PipelineRunResponse)
async def run_unit_matching_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await run_unit_matching(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@app.post("/api/conversations/{conversation_id}/run-qualification", response_model=PipelineRunResponse)
async def run_qualification_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await run_qualification(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@app.post("/api/conversations/{conversation_id}/run-swinging", response_model=PipelineRunResponse)
async def run_swinging_route(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> PipelineRunResponse:
    if not session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        result = await run_swinging(session, conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = await attach_outbound_action_result(session, result, conversation_id)
    session.commit()
    return PipelineRunResponse(conversation_id=conversation_id, result=result)


@app.get("/api/stage-runs", response_model=list[StageRunOut])
def list_stage_runs(session: Session = Depends(get_session), context: RequestContext = DashboardContext) -> list[StageRun]:
    return list(
        session.scalars(
            select(StageRun).where(*workspace_conditions(StageRun, route_scope(context).workspace_id)).order_by(StageRun.created_at.desc(), StageRun.id.desc())
        ).all()
    )


@app.get("/api/conversations/{conversation_id}/inspection")
def inspect_conversation_pipeline(
    conversation_id: int,
    session: Session = Depends(get_session),
    context: RequestContext = DashboardContext,
) -> dict[str, Any]:
    conversation = session.scalar(select(Conversation).where(*workspace_conditions(Conversation, route_scope(context).workspace_id), Conversation.id == conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return jsonable_encoder(build_pipeline_inspection(session, conversation))
