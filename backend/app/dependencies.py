from hmac import compare_digest

from fastapi import Depends, Header, HTTPException, Request

from .auth import CurrentUser, current_user_from_request, resolve_dashboard_context
from .config import get_settings


def verify_bridge_headers(
    x_whatsapp_bridge_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    expected_token = settings.bridge_token.strip()
    if settings.auth_required and not expected_token:
        raise HTTPException(status_code=500, detail="PROSPER_BRIDGE_TOKEN is required when AUTH_REQUIRED=true")
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
