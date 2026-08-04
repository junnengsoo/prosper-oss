from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import WhatsappAccount, WorkspaceMember
from .tenant import DEFAULT_WHATSAPP_ACCOUNT_ID, DEFAULT_WORKSPACE_ID, WorkspaceScope


SESSION_COOKIE_NAME = "prosper_session"


@dataclass(frozen=True)
class CurrentUser:
    auth_user_id: str
    email: str | None = None
    claims: dict[str, Any] | None = None
    is_dev_fallback: bool = False
    is_single_user_session: bool = False


@dataclass(frozen=True)
class RequestContext:
    user: CurrentUser
    scope: WorkspaceScope
    role: str = "owner"


def _require_single_user_auth_config() -> None:
    settings = get_settings()
    if not settings.access_password:
        raise HTTPException(status_code=500, detail="ACCESS_PASSWORD is required when AUTH_REQUIRED=true")
    if not settings.session_secret:
        raise HTTPException(status_code=500, detail="SESSION_SECRET is required when AUTH_REQUIRED=true")


def verify_access_password(password: str) -> None:
    _require_single_user_auth_config()
    expected = get_settings().access_password.encode("utf-8")
    provided = password.encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid password")


def create_session_token() -> str:
    _require_single_user_auth_config()
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(get_settings().session_secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    _require_single_user_auth_config()
    payload, separator, signature = token.rpartition(".")
    if not separator or not payload or not signature:
        return False
    expected_signature = hmac.new(get_settings().session_secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return False
    issued_at_text, separator, _nonce = payload.partition(".")
    if not separator:
        return False
    try:
        issued_at = int(issued_at_text)
    except ValueError:
        return False
    now = int(time.time())
    return issued_at <= now + 60 and now - issued_at <= get_settings().session_ttl_seconds


def current_user_from_session(token: str | None) -> CurrentUser:
    settings = get_settings()
    if not settings.auth_required:
        return CurrentUser(
            auth_user_id="dev-user",
            email="dev@local.test",
            claims={"sub": "dev-user", "email": "dev@local.test"},
            is_dev_fallback=True,
        )
    if not verify_session_token(token):
        raise HTTPException(status_code=401, detail="Authentication required")
    return CurrentUser(
        auth_user_id="prosper-owner",
        claims={"sub": "prosper-owner"},
        is_single_user_session=True,
    )


def current_user_from_request(request: Request) -> CurrentUser:
    return current_user_from_session(request.cookies.get(SESSION_COOKIE_NAME))


def resolve_workspace_context(
    session: Session,
    user: CurrentUser,
    requested_workspace_id: str | None = None,
) -> RequestContext:
    if (user.is_dev_fallback and not get_settings().auth_required) or user.is_single_user_session:
        return RequestContext(
            user=user,
            scope=WorkspaceScope(DEFAULT_WORKSPACE_ID, DEFAULT_WHATSAPP_ACCOUNT_ID),
            role="owner",
        )

    query = select(WorkspaceMember).where(
        WorkspaceMember.auth_user_id == user.auth_user_id,
        WorkspaceMember.status == "active",
    )
    if requested_workspace_id:
        query = query.where(WorkspaceMember.workspace_id == requested_workspace_id)
    memberships = list(session.scalars(query.order_by(WorkspaceMember.id)).all())
    if not memberships:
        raise HTTPException(status_code=403, detail="No active workspace membership")
    if not requested_workspace_id and len(memberships) > 1:
        raise HTTPException(status_code=403, detail="Multiple active workspaces; x-workspace-id is required")

    membership = memberships[0]
    whatsapp_accounts = list(
        session.scalars(
            select(WhatsappAccount)
            .where(WhatsappAccount.workspace_id == membership.workspace_id, WhatsappAccount.status == "active")
            .order_by(WhatsappAccount.id)
        ).all()
    )
    if not whatsapp_accounts:
        raise HTTPException(status_code=403, detail="Workspace has no active WhatsApp account")
    if len(whatsapp_accounts) > 1:
        raise HTTPException(status_code=403, detail="Workspace has multiple active WhatsApp accounts")

    whatsapp_account = whatsapp_accounts[0]
    return RequestContext(
        user=user,
        scope=WorkspaceScope(membership.workspace_id, whatsapp_account.id),
        role=membership.role,
    )


def resolve_single_workspace_context(session: Session, user: CurrentUser) -> RequestContext:
    return resolve_workspace_context(session, user, requested_workspace_id=None)


def active_workspace_membership_count(session: Session, auth_user_id: str) -> int:
    return len(
        list(
            session.scalars(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.auth_user_id == auth_user_id,
                    WorkspaceMember.status == "active",
                )
            ).all()
        )
    )


def resolve_dashboard_context(session: Session, request: Request) -> RequestContext:
    return resolve_single_workspace_context(session, current_user_from_request(request))
