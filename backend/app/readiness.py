from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Contact, Conversation, Property, PropertyMedia, PropertyPlaybook, StageRun, SwingCandidate


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
        "swing_candidates": {
            "total": count_rows(session, SwingCandidate),
            "enabled": count_rows(session, SwingCandidate, SwingCandidate.enabled.is_(True)),
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
    enabled_swing_candidates = session.scalars(select(SwingCandidate).where(SwingCandidate.enabled.is_(True))).all()
    for candidate in enabled_swing_candidates:
        source = session.scalar(select(Property).where(Property.property_id == candidate.source_property_id))
        target = session.scalar(select(Property).where(Property.property_id == candidate.candidate_property_id))
        label = f"{candidate.source_property_id}->{candidate.candidate_property_id}"
        if not source or not target:
            warnings.append(
                {
                    "code": "swing_candidate_missing_property",
                    "severity": "warning",
                    "message": f"Enabled swing candidate references missing property: {label}",
                }
            )
            continue
        if source.status != "available":
            warnings.append(
                {
                    "code": "swing_candidate_source_unavailable",
                    "severity": "warning",
                    "message": f"Enabled swing candidate source is not available in automatic flow: {label} source_status={source.status}",
                }
            )
        if target.status != "available":
            warnings.append(
                {
                    "code": "swing_candidate_target_unavailable",
                    "severity": "warning",
                    "message": f"Enabled swing candidate target is not available: {label} target_status={target.status}",
                }
            )
    return warnings
