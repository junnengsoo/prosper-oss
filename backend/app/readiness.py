from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Contact, Conversation, Property, PropertyMedia, PropertyPlaybook, StageRun


def count_rows(session: Session, model: type, *criteria: object) -> int:
    statement = select(func.count()).select_from(model)
    for criterion in criteria:
        statement = statement.where(criterion)
    return int(session.scalar(statement) or 0)


def runtime_summary(session: Session) -> dict[str, object]:
    return {
        "playbooks": {
            "total": count_rows(session, PropertyPlaybook),
            "enabled": count_rows(session, PropertyPlaybook, PropertyPlaybook.enabled.is_(True)),
        },
        "properties": {
            "total": count_rows(session, Property),
            "available": count_rows(session, Property, Property.status == "available"),
            "unavailable": count_rows(session, Property, Property.status == "unavailable"),
            "unknown": count_rows(session, Property, Property.status == "unknown"),
        },
        "media": {
            "total": count_rows(session, PropertyMedia),
            "enabled": count_rows(session, PropertyMedia, PropertyMedia.enabled.is_(True)),
        },
        "contacts": {
            "total": count_rows(session, Contact),
            "active": count_rows(session, Contact, Contact.status == "active"),
            "paused": count_rows(session, Contact, Contact.status == "paused"),
            "ignored": count_rows(session, Contact, Contact.status == "ignored"),
        },
        "conversations": {
            "total": count_rows(session, Conversation),
            "active": count_rows(session, Conversation, Conversation.status == "active"),
            "closed": count_rows(session, Conversation, Conversation.status == "closed"),
            "handover": count_rows(session, Conversation, Conversation.status == "handover"),
            "paused": count_rows(session, Conversation, Conversation.status == "paused"),
        },
        "stage_runs": {
            "total": count_rows(session, StageRun),
            "success": count_rows(session, StageRun, StageRun.status == "success"),
            "error": count_rows(session, StageRun, StageRun.status == "error"),
        },
    }


def runtime_warnings(session: Session) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    return warnings
