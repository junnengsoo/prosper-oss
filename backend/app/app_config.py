from sqlalchemy import select
from sqlalchemy.orm import Session

from .database.models import AppConfig


def get_config_value(session: Session, key: str, default: str = "") -> str:
    config = session.scalar(select(AppConfig).where(AppConfig.key == key))
    return config.value if config else default
