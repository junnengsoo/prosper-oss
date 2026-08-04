from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppConfig
from .tenant import current_workspace_scope, workspace_conditions


def get_config_value(session: Session, key: str, default: str = "") -> str:
    scope = current_workspace_scope()
    config = session.scalar(select(AppConfig).where(*workspace_conditions(AppConfig, scope.workspace_id), AppConfig.key == key))
    return config.value if config else default
