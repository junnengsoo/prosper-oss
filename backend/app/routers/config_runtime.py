from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.connection import get_session
from ..database.models import Property, PropertyMedia
from ..dependencies import DashboardContext
from ..auth import RequestContext
from ..playbooks import list_property_playbooks
from ..schemas import AppConfigOut, AppConfigUpdate, ConfigExportOut
from ..services import fetch_bridge_pairing_qr, fetch_bridge_status, get_all_config, request_bridge_reconnect, update_config

router = APIRouter()


@router.get("/api/config", response_model=AppConfigOut)
def list_config(session: Session = Depends(get_session), _context: RequestContext = DashboardContext) -> AppConfigOut:
    return AppConfigOut(values=get_all_config(session))


@router.patch("/api/config", response_model=AppConfigOut)
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


@router.get("/api/config/export", response_model=ConfigExportOut)
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


@router.get("/api/runtime/status")
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


@router.get("/api/whatsapp/connection")
async def whatsapp_connection(
    _context: RequestContext = DashboardContext,
) -> dict[str, object]:
    bridge = await fetch_bridge_status(get_settings().bridge_base_url)
    return {
        "state": whatsapp_connection_state(bridge),
        "bridge": bridge,
    }


@router.get("/api/whatsapp/qr")
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


@router.post("/api/whatsapp/reconnect")
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
