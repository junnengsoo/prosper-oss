from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session


DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_SLUG = "default"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_WHATSAPP_ACCOUNT_ID = "default-whatsapp"
DEFAULT_WHATSAPP_ACCOUNT_KEY = "default"


@dataclass(frozen=True)
class WorkspaceScope:
    workspace_id: str = DEFAULT_WORKSPACE_ID
    whatsapp_account_id: str = DEFAULT_WHATSAPP_ACCOUNT_ID


_workspace_scope: ContextVar[WorkspaceScope] = ContextVar("workspace_scope", default=WorkspaceScope())


def current_workspace_scope() -> WorkspaceScope:
    return _workspace_scope.get()


@contextmanager
def workspace_scope(scope: WorkspaceScope):
    token = _workspace_scope.set(scope)
    try:
        yield scope
    finally:
        _workspace_scope.reset(token)


def set_current_workspace_scope(scope: WorkspaceScope):
    return _workspace_scope.set(scope)


def reset_workspace_scope(token) -> None:
    _workspace_scope.reset(token)


def apply_workspace_scope(entity: Any, scope: WorkspaceScope | None = None) -> Any:
    scope = scope or current_workspace_scope()
    if hasattr(entity, "workspace_id") and not getattr(entity, "workspace_id", None):
        setattr(entity, "workspace_id", scope.workspace_id)
    if hasattr(entity, "whatsapp_account_id") and not getattr(entity, "whatsapp_account_id", None):
        setattr(entity, "whatsapp_account_id", scope.whatsapp_account_id)
    return entity


def workspace_conditions(model: Any, workspace_id: str = DEFAULT_WORKSPACE_ID) -> tuple[Any, ...]:
    if not hasattr(model, "workspace_id"):
        return ()
    return (model.workspace_id == workspace_id,)


def account_conditions(
    model: Any,
    scope: WorkspaceScope | None = None,
) -> tuple[Any, ...]:
    scope = scope or current_workspace_scope()
    conditions = list(workspace_conditions(model, scope.workspace_id))
    if hasattr(model, "whatsapp_account_id"):
        conditions.append(model.whatsapp_account_id == scope.whatsapp_account_id)
    return tuple(conditions)


def scoped_select(model: Any, scope: WorkspaceScope | None = None) -> Select:
    return select(model).where(*account_conditions(model, scope))


def ensure_default_workspace(session: Session):
    from .models import Workspace, WhatsappAccount

    workspace = session.get(Workspace, DEFAULT_WORKSPACE_ID)
    if not workspace:
        workspace = Workspace(
            id=DEFAULT_WORKSPACE_ID,
            slug=DEFAULT_WORKSPACE_SLUG,
            name=DEFAULT_WORKSPACE_NAME,
            status="active",
        )
        session.add(workspace)

    account = session.get(WhatsappAccount, DEFAULT_WHATSAPP_ACCOUNT_ID)
    if not account:
        account = WhatsappAccount(
            id=DEFAULT_WHATSAPP_ACCOUNT_ID,
            workspace_id=DEFAULT_WORKSPACE_ID,
            account_key=DEFAULT_WHATSAPP_ACCOUNT_KEY,
            display_name="Default WhatsApp account",
            status="active",
        )
        session.add(account)

    session.flush()
    return workspace, account
