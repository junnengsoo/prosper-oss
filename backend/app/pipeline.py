import json
import re
from datetime import datetime
from typing import Any, Callable, Awaitable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .llm import LlmMessage, LlmNotConfiguredError, LlmProviderError, generate_json
from .models import Contact, Conversation, Message, Property, StageRun, SwingCandidate
from .property_context import render_property_context, render_property_facts, render_qualification_property_context
from .prompts import get_prompt
from .qualification import QualificationLoop, QualificationOutputError
from .services import get_config_value, is_ai_paused
from .swing import stale_swing_property_diagnostic, swing_property_is_available
from .tenant import WorkspaceScope, current_workspace_scope, workspace_conditions


JsonGenerator = Callable[[list[LlmMessage]], Awaitable[dict[str, Any]]]
SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
INITIAL_STAGE = "unit_matching"
END_STAGE = "end"


def conversation_messages(session: Session, conversation_id: int) -> list[Message]:
    """Return a conversation's stored messages in chronological order for LLM context."""
    return list(
        session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.timestamp_ms)).all()
    )


def message_to_llm_message(message: Message) -> LlmMessage:
    """Convert one stored message into the chat role/content shape expected by the LLM."""
    if message.direction == "inbound":
        return {"role": "user", "content": message.text}
    if message.direction == "outbound":
        return {"role": "assistant", "content": message.text}
    if message.direction == "human":
        return {"role": "assistant", "content": f"[manual agent reply] {message.text}"}
    raise ValueError(f"Unsupported message direction {message.direction}")


def messages_to_llm_messages(messages: list[Message]) -> list[LlmMessage]:
    """Convert stored conversation messages into LLM messages while preserving order."""
    return [message_to_llm_message(message) for message in messages]


def pretriage_messages_from_thread(thread: str) -> list[LlmMessage]:
    """Wrap raw pre-conversation text as a single user message for initial triage."""
    return [{"role": "user", "content": thread}]


def serialize_llm_messages(messages: list[LlmMessage]) -> str:
    """Serialize the exact LLM input so stage runs can be audited later."""
    return json.dumps(messages, ensure_ascii=False, indent=2)


def render_runtime_context(now: datetime | None = None) -> str:
    """Render current Singapore date context for prompts that interpret relative dates."""
    current = now.astimezone(SINGAPORE_TZ) if now else datetime.now(SINGAPORE_TZ)
    today = f"{current.day} {current.strftime('%b %Y')}"
    return f"Today is {today} in Singapore. Use Singapore time when interpreting relative dates."


def build_triage_messages(thread: str) -> list[LlmMessage]:
    """Build pre-storage triage input from a raw WhatsApp thread or burst batch."""
    prompt = get_prompt("triage")
    return [{"role": "system", "content": prompt.render(known_context="NONE")}, *pretriage_messages_from_thread(thread)]


def render_available_property_jsonl(session: Session) -> str:
    """Render configured properties as JSONL for the unit-matching prompt."""
    scope = current_workspace_scope()
    properties = session.scalars(
        select(Property).where(*workspace_conditions(Property, scope.workspace_id)).order_by(Property.property_id)
    ).all()
    lines = []
    for property_ in properties:
        lines.append(
            json.dumps(
                {
                    "property_id": property_.property_id,
                    "property_name": property_.property_name,
                    "full_address": property_.full_address or "",
                    "property_url": property_.property_url or "",
                    "propertyguru_listing_id": property_.propertyguru_listing_id or "",
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


PROPERTY_ID_PATTERN = re.compile(r"\bRTF-\d+\b")


def render_swing_candidate_jsonl(session: Session, source_property_id: str, exclude_property_ids: set[str] | None = None) -> str:
    """Render remaining configured swing candidates for a host property as JSONL."""
    scope = current_workspace_scope()
    exclude_property_ids = exclude_property_ids or set()
    candidates = session.scalars(
        select(SwingCandidate)
        .where(
            *workspace_conditions(SwingCandidate, scope.workspace_id),
            SwingCandidate.source_property_id == source_property_id,
            SwingCandidate.enabled.is_(True),
        )
        .order_by(SwingCandidate.sort_order, SwingCandidate.id)
    ).all()
    lines = []
    for candidate in candidates:
        if candidate.candidate_property_id in exclude_property_ids:
            continue
        property_ = session.scalar(
            select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == candidate.candidate_property_id)
        )
        if not swing_property_is_available(property_):
            continue
        lines.append(
            json.dumps(
                {
                    "property_id": property_.property_id,
                    "property_name": property_.property_name,
                    "property_facts": render_property_facts(property_),
                    "landlord_profile_requirements": property_.landlord_profile_requirements,
                    "tenant_facing_caveats": property_.tenant_facing_caveats,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def enabled_swing_candidate_ids(session: Session, source_property_id: str, exclude_property_ids: set[str] | None = None) -> set[str]:
    """Return enabled swing candidate property IDs after excluding already attempted units."""
    scope = current_workspace_scope()
    exclude_property_ids = exclude_property_ids or set()
    candidates = session.scalars(
        select(SwingCandidate)
        .where(
            *workspace_conditions(SwingCandidate, scope.workspace_id),
            SwingCandidate.source_property_id == source_property_id,
            SwingCandidate.enabled.is_(True),
        )
        .order_by(SwingCandidate.sort_order, SwingCandidate.id)
    ).all()
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_property_id in exclude_property_ids:
            continue
        property_ = session.scalar(
            select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == candidate.candidate_property_id)
        )
        if swing_property_is_available(property_):
            candidate_ids.add(candidate.candidate_property_id)
    return candidate_ids


def first_enabled_swing_candidate(
    session: Session,
    source_property_id: str,
    workspace_id: str | None = None,
) -> tuple[SwingCandidate, Property] | None:
    """Return the first configured fallback property for a source property."""
    workspace_id = workspace_id or current_workspace_scope().workspace_id
    candidates = session.scalars(
        select(SwingCandidate)
        .where(
            *workspace_conditions(SwingCandidate, workspace_id),
            SwingCandidate.source_property_id == source_property_id,
            SwingCandidate.enabled.is_(True),
        )
        .order_by(SwingCandidate.sort_order, SwingCandidate.id)
    ).all()
    for candidate in candidates:
        property_ = session.scalar(
            select(Property).where(*workspace_conditions(Property, workspace_id), Property.property_id == candidate.candidate_property_id)
        )
        if swing_property_is_available(property_):
            return candidate, property_
    return None


def first_stale_swing_candidate_diagnostic(
    session: Session,
    source_property_id: str,
    workspace_id: str | None = None,
) -> dict[str, str] | None:
    """Return diagnostics for the first enabled swing candidate that is not currently available."""
    workspace_id = workspace_id or current_workspace_scope().workspace_id
    candidates = session.scalars(
        select(SwingCandidate)
        .where(
            *workspace_conditions(SwingCandidate, workspace_id),
            SwingCandidate.source_property_id == source_property_id,
            SwingCandidate.enabled.is_(True),
        )
        .order_by(SwingCandidate.sort_order, SwingCandidate.id)
    ).all()
    for candidate in candidates:
        property_ = session.scalar(
            select(Property).where(*workspace_conditions(Property, workspace_id), Property.property_id == candidate.candidate_property_id)
        )
        if not swing_property_is_available(property_):
            return stale_swing_property_diagnostic(candidate.candidate_property_id, property_)
    return None


def collect_property_ids(value: Any) -> set[str]:
    """Recursively collect explicit property IDs from nested stage outputs or text."""
    ids: set[str] = set()
    if isinstance(value, dict):
        property_id = value.get("property_id")
        if isinstance(property_id, str) and property_id:
            ids.add(property_id)
        for child in value.values():
            ids.update(collect_property_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(collect_property_ids(child))
    elif isinstance(value, str):
        ids.update(PROPERTY_ID_PATTERN.findall(value))
    return ids


def attempted_swing_property_ids(session: Session, conversation_id: int) -> set[str]:
    """Find properties already matched, qualified, or suggested in this conversation."""
    attempted: set[str] = set()
    runs = session.scalars(
        select(StageRun)
        .where(StageRun.conversation_id == conversation_id, StageRun.stage.in_(["unit_matching", "qualification", "swinging"]))
        .order_by(StageRun.id)
    ).all()
    for run in runs:
        if run.stage == "qualification":
            # TODO: Use object recognition to find property IDs in the input snapshot instead of relying on the output JSON, since the output may not always include the full property info.
            attempted.update(PROPERTY_ID_PATTERN.findall(run.input_snapshot or ""))
            continue
        if not run.output_json:
            continue
        try:
            output = json.loads(run.output_json)
        except json.JSONDecodeError:
            continue
        attempted.update(collect_property_ids(output))
    return attempted


def record_stage_run(
    session: Session,
    conversation_id: int | None,
    stage: str,
    input_snapshot: str,
    output: dict[str, Any] | None,
    status: str,
    error: str | None = None,
    model: str | None = None,
) -> StageRun:
    """Persist one AI stage attempt, including input snapshot, output, status, and error."""
    conversation = session.get(Conversation, conversation_id) if conversation_id is not None else None
    scope = WorkspaceScope(conversation.workspace_id, conversation.whatsapp_account_id) if conversation else current_workspace_scope()
    run = StageRun(
        workspace_id=scope.workspace_id,
        whatsapp_account_id=scope.whatsapp_account_id,
        conversation_id=conversation_id,
        stage=stage,
        input_snapshot=input_snapshot,
        output_json=json.dumps(output, ensure_ascii=False) if output is not None else None,
        status=status,
        error=error,
        model=model,
    )
    session.add(run)
    session.flush()
    return run


async def run_llm_stage(
    generator: JsonGenerator,
    messages: list[LlmMessage],
    *,
    stage: str,
    conversation_id: int | None,
    property_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one LLM stage with tracing metadata, or use an injected test generator."""
    if generator is generate_json:
        return await generate_json(
            messages,
            {
                "stage": stage,
                "conversation_id": conversation_id,
                "property_id": property_id,
                "metadata": metadata or {},
            },
        )
    return await generator(messages)


def ensure_pipeline_allowed(session: Session, conversation: Conversation) -> None:
    """Block pipeline execution when the conversation, contact, or global AI is paused."""
    if conversation.status != "active":
        raise ValueError(f"Conversation is {conversation.status}")

    contact = session.get(Contact, conversation.contact_id)
    if not contact:
        raise ValueError("Contact not found")
    if contact.status != "active":
        raise ValueError(f"Contact is {contact.status}")

    if is_ai_paused(session):
        raise ValueError("Global AI pause is enabled")


def as_dict(value: Any) -> dict[str, Any] | None:
    """Return a value only if it is a dictionary, used for defensive LLM parsing."""
    return value if isinstance(value, dict) else None


def manual_review_result(result: dict[str, Any], reason: str) -> dict[str, Any]:
    """Convert a unit-matching result into a manual-review result with a clear reason."""
    updated = dict(result)
    updated["match_status"] = "manual_review"
    updated["reason"] = reason
    return updated


def has_profile_info_from_unit_matching(result: dict[str, Any]) -> bool:
    """Check whether unit matching says the inbound thread already includes a profile."""
    return str(result.get("profile_info_status") or "").strip().lower() == "profile_present"


def latest_unit_matching_has_profile_info(session: Session, conversation_id: int) -> bool:
    """Check whether the latest unit-matching run detected an already-provided tenant profile."""
    run = session.scalar(
        select(StageRun)
        .where(StageRun.conversation_id == conversation_id, StageRun.stage == "unit_matching", StageRun.output_json.is_not(None))
        .order_by(StageRun.id.desc())
    )
    if not run or not run.output_json:
        return False
    try:
        output = json.loads(run.output_json)
    except json.JSONDecodeError:
        return False
    return has_profile_info_from_unit_matching(output)


def conversation_has_qualification_context(session: Session, conversation_id: int) -> bool:
    """Return whether qualification has already interpreted tenant profile context in this conversation."""
    run = session.scalar(
        select(StageRun)
        .where(StageRun.conversation_id == conversation_id, StageRun.stage == "qualification", StageRun.output_json.is_not(None))
        .order_by(StageRun.id.desc())
    )
    if not run or not run.output_json:
        return False
    try:
        output = json.loads(run.output_json)
    except json.JSONDecodeError:
        return False
    return isinstance(output, dict) and "qualification_status" in output


def conversation_has_profile_context(session: Session, conversation_id: int) -> bool:
    """Decide whether a swing acceptance can go straight into qualification."""
    return latest_unit_matching_has_profile_info(session, conversation_id) or conversation_has_qualification_context(
        session, conversation_id
    )


def should_send_profile_present_available_sequence(qualification_result: dict[str, Any]) -> bool:
    """Decide whether to still send the available-unit sequence after profile-present qualification."""
    return qualification_result.get("qualification_status") in {"match", "incomplete", "clarify_fit"}


def _qualification_decision(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize direct or nested qualification output into a single decision dictionary."""
    if "qualification_status" in result:
        return result
    nested = result.get("qualification")
    return nested if isinstance(nested, dict) else {}


async def run_triage_text(
    session: Session,
    thread: str,
    generator: JsonGenerator = generate_json,
    conversation_id: int | None = None,
    persist_input_snapshot: bool = True,
) -> dict[str, Any]:
    """Run triage on raw text before or outside a stored conversation."""
    llm_messages = build_triage_messages(thread)
    input_snapshot = serialize_llm_messages(llm_messages) if persist_input_snapshot else "[redacted pre-conversation triage input]"
    try:
        result = await run_llm_stage(
            generator,
            llm_messages,
            stage="triage",
            conversation_id=conversation_id,
            metadata={"persist_input_snapshot": persist_input_snapshot},
        )
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, ValueError) as error:
        record_stage_run(session, conversation_id, "triage", input_snapshot, None, "error", str(error))
        return {"stage_status": "manual_review", "reason": str(error)}

    record_stage_run(session, conversation_id, "triage", input_snapshot, result, "success")
    return result


async def run_unit_matching(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Match an enquiry conversation to one configured property and route by availability."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    ensure_pipeline_allowed(session, conversation)
    prompt = get_prompt("unit_matching")
    property_jsonl = render_available_property_jsonl(session)
    llm_messages = [
        {
            "role": "system",
            "content": prompt.render(
                property_list=property_jsonl,
            ),
        },
        *messages_to_llm_messages(conversation_messages(session, conversation_id)),
    ]
    input_snapshot = serialize_llm_messages(llm_messages)
    try:
        result = await run_llm_stage(
            generator,
            llm_messages,
            stage="unit_matching",
            conversation_id=conversation_id,
            metadata={"available_property_count": property_jsonl.count("\n") + 1 if property_jsonl else 0},
        )
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, ValueError) as error:
        conversation.current_stage = END_STAGE
        record_stage_run(session, conversation_id, "unit_matching", input_snapshot, None, "error", str(error))
        return {"match_status": "manual_review", "reason": str(error)}

    record_stage_run(session, conversation_id, "unit_matching", input_snapshot, result, "success")
    if result.get("match_status") != "matched":
        conversation.current_stage = END_STAGE
        return result

    matched_properties = result.get("matched_properties") or []
    if not isinstance(matched_properties, list) or len(matched_properties) != 1:
        conversation.current_stage = END_STAGE
        return manual_review_result(result, "Expected exactly one matched property")

    matched_property = as_dict(matched_properties[0])
    if not matched_property:
        conversation.current_stage = END_STAGE
        return manual_review_result(result, "Matched property must be an object")

    property_id = matched_property.get("property_id")
    if not property_id:
        conversation.current_stage = END_STAGE
        return manual_review_result(result, "Matched property is missing property_id")

    scope = WorkspaceScope(conversation.workspace_id, conversation.whatsapp_account_id)
    property_ = session.scalar(select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == property_id))
    if not property_:
        conversation.current_stage = END_STAGE
        return manual_review_result(result, "Matched property is not configured in SQLite")
    if property_.status == "unknown":
        conversation.current_stage = END_STAGE
        return manual_review_result(result, "Matched property availability is unknown in SQLite")

    if not conversation.host_property_id:
        conversation.host_property_id = property_.property_id
    conversation.matched_property_id = property_.property_id
    result["matched_property_status"] = property_.status
    if property_.status == "unavailable":
        result["unavailable_swing_required"] = True
        conversation.current_stage = END_STAGE
        return result

    result["available_sequence_required"] = True
    conversation.current_stage = END_STAGE
    return result


def deterministic_swing_result(session: Session, conversation: Conversation, source_property_id: str) -> dict[str, Any]:
    """Build the MVP swing result from the first configured fallback, without LLM selection."""
    source_property = session.scalar(
        select(Property).where(
            *workspace_conditions(Property, conversation.workspace_id),
            Property.property_id == source_property_id,
        )
    )
    candidate = first_enabled_swing_candidate(session, source_property_id, conversation.workspace_id)
    if not candidate:
        stale_diagnostic = first_stale_swing_candidate_diagnostic(session, source_property_id, conversation.workspace_id)
        conversation.current_suggested_property_id = None
        conversation.current_stage = END_STAGE
        result = {
            "swing_status": "no_configured_candidate",
            "selected_property_id": "",
            "current_suggested_property": {"property_id": "", "property_name": "", "reason": ""},
            "tenant_reply": "",
            "reason": "Matched unit is unavailable and no enabled fallback property is configured.",
            "swing_source_property_id": source_property_id,
            "swing_source_property_status": source_property.status if source_property else "",
            "is_followup_swing": False,
            "deterministic": True,
        }
        if stale_diagnostic:
            result["swing_status"] = "stale_swing_property"
            result["reason"] = stale_diagnostic["reason"]
            result["diagnostic"] = stale_diagnostic
        return result

    _, suggested_property = candidate
    conversation.current_suggested_property_id = suggested_property.property_id
    conversation.current_stage = END_STAGE
    return {
        "swing_status": "suggest_alternative",
        "selected_property_id": "",
        "current_suggested_property": {
            "property_id": suggested_property.property_id,
            "property_name": suggested_property.property_name,
            "reason": "Configured fallback for unavailable matched unit.",
        },
        "tenant_reply": "",
        "reason": "Matched unit is unavailable; using configured fallback property.",
        "swing_source_property_id": source_property_id,
        "swing_source_property_status": source_property.status if source_property else "",
        "is_followup_swing": False,
        "deterministic": True,
    }


async def run_unit_matching_then_maybe_qualification(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Run the MVP automatic flow: unit match, then template reply or deterministic swing."""
    matching = await run_unit_matching(session, conversation_id, generator)
    result: dict[str, Any] = {"unit_matching": matching}
    if matching.get("match_status") == "matched" and matching.get("matched_property_status") == "unavailable":
        conversation = session.get(Conversation, conversation_id)
        matched_properties = matching.get("matched_properties")
        matched_property = matched_properties[0] if isinstance(matched_properties, list) and matched_properties else {}
        source_property_id = matched_property.get("property_id") if isinstance(matched_property, dict) else None
        source_property_id = source_property_id or (conversation.matched_property_id if conversation else None)
        if conversation and source_property_id:
            result["swinging"] = deterministic_swing_result(session, conversation, source_property_id)
    return result


async def run_initial_enquiry_pipeline(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Run the first stored-conversation MVP flow."""
    return await run_unit_matching_then_maybe_qualification(session, conversation_id, generator)


async def route_stored_conversation_after_inbound(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Route a stored conversation after inbound; raw pre-triage happens before storage."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    ensure_pipeline_allowed(session, conversation)

    stage = conversation.current_stage or INITIAL_STAGE
    if stage == INITIAL_STAGE:
        return await run_initial_enquiry_pipeline(session, conversation_id, generator)

    if stage == "unit_matching":
        if conversation.matched_property_id:
            return {"stage_status": "no_action", "reason": "Matched conversation already handled by MVP flow"}
        return await run_initial_enquiry_pipeline(session, conversation_id, generator)

    if stage == "qualification":
        conversation.current_stage = END_STAGE
        return {"stage_status": "no_action", "reason": "Qualification is disabled in the MVP flow"}

    if stage == "swinging":
        conversation.current_stage = END_STAGE
        return {"stage_status": "no_action", "reason": "AI swinging is disabled in the MVP flow"}

    if stage == END_STAGE:
        return {"stage_status": "no_action", "reason": "Conversation is at end stage"}

    return {"stage_status": "no_action", "reason": f"No automatic pipeline for stage {stage}"}


async def run_qualification(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Qualify the tenant profile against the current matched property requirements."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    ensure_pipeline_allowed(session, conversation)
    if not conversation.matched_property_id:
        conversation.current_stage = END_STAGE
        return {"qualification_status": "unsure", "message": "", "reason": "No matched property on conversation"}

    scope = WorkspaceScope(conversation.workspace_id, conversation.whatsapp_account_id)
    property_ = session.scalar(
        select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == conversation.matched_property_id)
    )
    if not property_:
        conversation.current_stage = END_STAGE
        return {"qualification_status": "unsure", "message": "", "reason": "Matched property not found"}

    prompt = get_prompt("qualification")
    property_info = render_qualification_property_context(property_)
    llm_messages = [
        {
            "role": "system",
            "content": prompt.render(
                runtime_context=render_runtime_context(),
                property_info=property_info,
            ),
        },
        *messages_to_llm_messages(conversation_messages(session, conversation_id)),
    ]
    input_snapshot = serialize_llm_messages(llm_messages)
    previous_turn_count = session.scalar(
        select(func.count(StageRun.id)).where(
            StageRun.conversation_id == conversation_id,
            StageRun.stage == "qualification",
            StageRun.status == "success",
        )
    ) or 0
    loop = QualificationLoop(turn_count=int(previous_turn_count))
    if loop.exhausted:
        result = loop.handoff("Maximum qualification turns reached")
        conversation.current_stage = END_STAGE
        record_stage_run(session, conversation_id, "qualification", input_snapshot, result, "needs_review")
        return result
    try:
        raw_result = await run_llm_stage(
            generator,
            llm_messages,
            stage="qualification",
            conversation_id=conversation_id,
            property_id=property_.property_id,
        )
        result = loop.advance(raw_result)
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, QualificationOutputError, ValueError) as error:
        conversation.current_stage = END_STAGE
        record_stage_run(session, conversation_id, "qualification", input_snapshot, None, "error", str(error))
        return {"qualification_status": "unsure", "message": "", "reason": str(error)}

    record_stage_run(session, conversation_id, "qualification", input_snapshot, result, "success")
    status = result.get("qualification_status")
    attempted_property_ids = attempted_swing_property_ids(session, conversation_id)
    swing_source_property_id = conversation.host_property_id or property_.property_id
    if status == "not_match" and render_swing_candidate_jsonl(session, swing_source_property_id, attempted_property_ids).strip():
        conversation.current_stage = "swinging"
        swing_result = await run_swinging(session, conversation_id, generator)
        return {"qualification": result, "swinging": swing_result}

    conversation.current_stage = "qualification" if status in {"incomplete", "clarify_fit"} else END_STAGE
    return result


async def run_swinging(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Suggest or process one swing alternative from the host property's configured candidates."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    ensure_pipeline_allowed(session, conversation)
    if not conversation.matched_property_id:
        conversation.current_stage = END_STAGE
        return {"swing_status": "handover_to_agent", "tenant_reply": "", "reason": "No original matched property"}

    prompt = get_prompt("swinging")
    attempted_property_ids = attempted_swing_property_ids(session, conversation_id)
    swing_source_property_id = conversation.host_property_id or conversation.matched_property_id
    scope = WorkspaceScope(conversation.workspace_id, conversation.whatsapp_account_id)
    swing_source_property = session.scalar(
        select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == swing_source_property_id)
    )
    swing_source_property_status = swing_source_property.status if swing_source_property else ""
    is_followup_swing = bool(conversation.current_suggested_property_id)
    candidates = render_swing_candidate_jsonl(session, swing_source_property_id, attempted_property_ids)
    candidate_ids = enabled_swing_candidate_ids(session, swing_source_property_id, attempted_property_ids)
    if not candidates.strip() and not conversation.current_suggested_property_id:
        conversation.current_stage = END_STAGE
        return {
            "swing_status": "no_good_candidate",
            "selected_property_id": "",
            "current_suggested_property": {"property_id": "", "property_name": "", "reason": ""},
            "tenant_reply": "",
            "reason": "No remaining swing candidates after filtering attempted properties",
            "swing_source_property_id": swing_source_property_id,
            "swing_source_property_status": swing_source_property_status,
            "is_followup_swing": is_followup_swing,
        }
    current = "NONE"
    if conversation.current_suggested_property_id:
        property_ = session.scalar(
            select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == conversation.current_suggested_property_id)
        )
        if not swing_property_is_available(property_):
            diagnostic = stale_swing_property_diagnostic(conversation.current_suggested_property_id, property_)
            result = {
                "swing_status": "stale_swing_property",
                "selected_property_id": "",
                "current_suggested_property": {"property_id": "", "property_name": "", "reason": ""},
                "tenant_reply": "",
                "reason": diagnostic["reason"],
                "diagnostic": diagnostic,
                "swing_source_property_id": swing_source_property_id,
                "swing_source_property_status": swing_source_property_status,
                "is_followup_swing": is_followup_swing,
            }
            conversation.current_suggested_property_id = None
            conversation.current_stage = END_STAGE
            record_stage_run(session, conversation_id, "swinging", "current suggested swing property stale before prompt", result, "needs_review")
            return result
        if property_:
            current = json.dumps(
                {
                    "property_id": property_.property_id,
                    "property_name": property_.property_name,
                    "property_facts": render_property_facts(property_),
                    "landlord_profile_requirements": property_.landlord_profile_requirements,
                    "tenant_facing_caveats": property_.tenant_facing_caveats,
                },
                ensure_ascii=False,
            )

    llm_messages = [
        {
            "role": "system",
            "content": prompt.render(
                runtime_context=render_runtime_context(),
                candidate_properties=candidates,
                current_suggested_candidate=current,
            ),
        },
        *messages_to_llm_messages(conversation_messages(session, conversation_id)),
    ]
    input_snapshot = serialize_llm_messages(llm_messages)
    try:
        result = await run_llm_stage(
            generator,
            llm_messages,
            stage="swinging",
            conversation_id=conversation_id,
            property_id=swing_source_property_id,
            metadata={
                "candidate_count": len(candidate_ids),
                "swing_source_property_id": swing_source_property_id,
                "swing_source_property_status": swing_source_property_status,
                "is_followup_swing": is_followup_swing,
                "matched_property_id": conversation.matched_property_id,
                "current_suggested_property_id": conversation.current_suggested_property_id,
            },
        )
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, ValueError) as error:
        conversation.current_stage = END_STAGE
        record_stage_run(session, conversation_id, "swinging", input_snapshot, None, "error", str(error))
        return {"swing_status": "handover_to_agent", "tenant_reply": "", "reason": str(error)}

    result["swing_source_property_id"] = swing_source_property_id
    result["swing_source_property_status"] = swing_source_property_status
    result["is_followup_swing"] = is_followup_swing
    record_stage_run(session, conversation_id, "swinging", input_snapshot, result, "success")
    status = result.get("swing_status")
    tenant_reply = str(result.get("tenant_reply") or "")
    reason = str(result.get("reason") or "")

    if status == "suggest_alternative":
        current_property = result.get("current_suggested_property") or {}
        if not isinstance(current_property, dict):
            conversation.current_stage = END_STAGE
            result["swing_status"] = "handover_to_agent"
            result["reason"] = "current_suggested_property must be an object"
            return result
        suggested_id = current_property.get("property_id")
        if not suggested_id or suggested_id not in candidate_ids:
            conversation.current_stage = END_STAGE
            result["swing_status"] = "handover_to_agent"
            result["reason"] = "Suggested property must be an enabled swing candidate"
            return result
        suggested_property = session.scalar(
            select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == suggested_id)
        )
        if not suggested_property:
            conversation.current_stage = END_STAGE
            result["swing_status"] = "handover_to_agent"
            result["reason"] = "Suggested property is not a configured property"
            return result
        conversation.current_suggested_property_id = suggested_id
        conversation.current_stage = "swinging"
        result["tenant_reply"] = tenant_reply
    elif status == "proceed_to_qualification":
        selected_id = result.get("selected_property_id") or conversation.current_suggested_property_id
        if not selected_id or selected_id != conversation.current_suggested_property_id:
            conversation.current_stage = END_STAGE
            result["swing_status"] = "handover_to_agent"
            result["reason"] = "Proceed to qualification must use the current suggested candidate"
            return result
        property_ = session.scalar(select(Property).where(*workspace_conditions(Property, scope.workspace_id), Property.property_id == selected_id))
        if not property_:
            conversation.current_stage = END_STAGE
            result["swing_status"] = "handover_to_agent"
            result["reason"] = "Current suggested candidate is not a configured property"
            return result
        conversation.matched_property_id = selected_id
        conversation.current_suggested_property_id = None
        conversation.current_stage = "qualification"
    elif status == "answer_question":
        conversation.current_stage = "swinging"
    elif status == "no_good_candidate":
        conversation.current_stage = END_STAGE
        result["tenant_reply"] = tenant_reply
    else:
        conversation.current_stage = END_STAGE
    return result
