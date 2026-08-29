import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actions import execute_outbound_action_plan, plan_outbound_actions
from .database.models import Conversation, StageRun
from .pipeline import mark_conversation_manual_review


def triage_is_initial_enquiry(triage: dict[str, Any] | None) -> bool:
    """Return whether triage classified a thread as an initial rental enquiry."""
    if not isinstance(triage, dict):
        return False
    return triage.get("is_initial_rental_enquiry") is True


def route_triage_manual_review(session: Session, conversation: Conversation | None, triage: dict[str, Any]) -> dict[str, Any]:
    """Persist Manual Review routing for invalid or uncertain triage output."""
    if conversation:
        mark_conversation_manual_review(
            session,
            conversation,
            source_stage="triage",
            reason=str(triage.get("reason") or "triage manual review"),
            output=triage,
        )
    return {"triage": triage}


async def attach_outbound_action_result(session: Session, result: dict, conversation_id: int | None = None) -> dict:
    if conversation_id is not None and "sent_actions" not in result:
        result = await execute_outbound_action_plan(session, conversation_id, result)
    result["outbound_actions"] = result.get("send_result", {"status": "not_attempted", "reason": "no_conversation"})
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
