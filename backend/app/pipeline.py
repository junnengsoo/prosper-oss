import json
import re
from typing import Any, Callable, Awaitable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm import LlmMessage, LlmNotConfiguredError, LlmProviderError, generate_json
from .database.models import Contact, Conversation, Message, Property, StageRun
from .prompts import get_prompt
from .schemas import RentalListingMatchingOutputContract, TriageOutputContract
from .services import is_ai_paused


JsonGenerator = Callable[[list[LlmMessage]], Awaitable[dict[str, Any]]]
INITIAL_STAGE = "rental_listing_matching"
END_STAGE = "end"
MANUAL_REVIEW_STAGE = "manual_review"


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


def build_triage_messages(thread: str) -> list[LlmMessage]:
    """Build pre-storage triage input from a raw WhatsApp thread or burst batch."""
    prompt = get_prompt("triage")
    return [{"role": "system", "content": prompt.render(known_context="NONE")}, *pretriage_messages_from_thread(thread)]


def render_available_property_jsonl(session: Session) -> str:
    """Render configured rental listings as JSONL for the matching prompt."""
    properties = session.scalars(select(Property).order_by(Property.property_id)).all()
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
    run = StageRun(
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


def is_manual_review_result(result: dict[str, Any] | None) -> bool:
    """Return whether a stage or pipeline result requires manual review."""
    if not isinstance(result, dict):
        return False
    if result.get("stage_status") == MANUAL_REVIEW_STAGE or result.get("match_status") == MANUAL_REVIEW_STAGE:
        return True
    return any(is_manual_review_result(child) for child in result.values() if isinstance(child, dict))


def mark_conversation_manual_review(
    session: Session,
    conversation: Conversation,
    *,
    source_stage: str,
    reason: str,
    output: dict[str, Any] | None = None,
) -> None:
    """Route a conversation to Manual Review and persist an inspectable decision."""
    conversation.current_stage = MANUAL_REVIEW_STAGE
    output_payload = output or {"stage_status": MANUAL_REVIEW_STAGE, "reason": reason}
    record_stage_run(
        session,
        conversation.id,
        MANUAL_REVIEW_STAGE,
        f"{source_stage} manual review routing decision",
        {"source_stage": source_stage, **output_payload},
        MANUAL_REVIEW_STAGE,
        reason,
    )


def manual_review_stage_result(reason: str) -> dict[str, Any]:
    """Build a consistent Manual Review stage result."""
    return {"stage_status": MANUAL_REVIEW_STAGE, "reason": reason}


def manual_review_result(result: dict[str, Any], reason: str) -> dict[str, Any]:
    """Convert a listing-matching result into a manual-review result with a clear reason."""
    updated = dict(result)
    if "match_status" in updated and updated["match_status"] != MANUAL_REVIEW_STAGE:
        updated["original_match_status"] = updated["match_status"]
    updated["match_status"] = "manual_review"
    updated["reason"] = reason
    return updated


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
        raw_result = await run_llm_stage(
            generator,
            llm_messages,
            stage="triage",
            conversation_id=conversation_id,
            metadata={"persist_input_snapshot": persist_input_snapshot},
        )
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, ValueError) as error:
        result = manual_review_stage_result(str(error))
        record_stage_run(session, conversation_id, "triage", input_snapshot, result, MANUAL_REVIEW_STAGE, str(error))
        return result

    try:
        parsed = TriageOutputContract.model_validate(raw_result)
    except ValidationError as error:
        reason = f"Invalid triage output: {error}"
        result = manual_review_stage_result(reason)
        record_stage_run(session, conversation_id, "triage", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    result = parsed.model_dump()
    if parsed.confidence != "high":
        reason = "Triage confidence is not high enough for automatic handling"
        result = {"stage_status": MANUAL_REVIEW_STAGE, "reason": reason, "triage": result}
        record_stage_run(session, conversation_id, "triage", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    record_stage_run(session, conversation_id, "triage", input_snapshot, result, "success")
    return result


async def run_rental_listing_matching(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Match a rental enquiry conversation to one configured rental listing and route by availability."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")
    ensure_pipeline_allowed(session, conversation)
    prompt = get_prompt("rental_listing_matching")
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
        raw_result = await run_llm_stage(
            generator,
            llm_messages,
            stage="rental_listing_matching",
            conversation_id=conversation_id,
            metadata={"available_property_count": property_jsonl.count("\n") + 1 if property_jsonl else 0},
        )
    except (LlmNotConfiguredError, LlmProviderError, json.JSONDecodeError, ValueError) as error:
        result = manual_review_result({}, str(error))
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=str(error), output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, str(error))
        return result

    try:
        parsed = RentalListingMatchingOutputContract.model_validate(raw_result)
    except ValidationError as error:
        reason = f"Invalid rental listing matching output: {error}"
        result = manual_review_result({}, reason)
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=reason, output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    result = parsed.model_dump()
    if result.get("match_status") != "matched":
        reason = f"Rental listing matching returned {result['match_status']}"
        result = manual_review_result(result, reason)
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=reason, output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    matched_property = result["matched_properties"][0]
    property_id = matched_property.get("property_id")

    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        reason = "Matched property is not configured in SQLite"
        result = manual_review_result(result, reason)
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=reason, output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result
    if property_.status == "unknown":
        conversation.matched_property_id = property_.property_id
        reason = "Matched property availability is unknown in SQLite"
        result = manual_review_result(result, reason)
        result["matched_property_status"] = property_.status
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=reason, output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    conversation.matched_property_id = property_.property_id
    result["matched_property_status"] = property_.status
    if property_.status == "unavailable":
        reason = "Matched rental listing is unavailable"
        result = manual_review_result(result, reason)
        mark_conversation_manual_review(session, conversation, source_stage="rental_listing_matching", reason=reason, output=result)
        record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, MANUAL_REVIEW_STAGE, reason)
        return result

    result["available_sequence_required"] = True
    conversation.current_stage = END_STAGE
    record_stage_run(session, conversation_id, "rental_listing_matching", input_snapshot, result, "success")
    return result


async def run_rental_listing_matching_pipeline(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Run the automatic rental listing matching flow."""
    return {"rental_listing_matching": await run_rental_listing_matching(session, conversation_id, generator)}


async def run_initial_enquiry_pipeline(
    session: Session,
    conversation_id: int,
    generator: JsonGenerator = generate_json,
) -> dict[str, Any]:
    """Run the first stored-conversation rental enquiry flow."""
    return await run_rental_listing_matching_pipeline(session, conversation_id, generator)


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

    if stage == "rental_listing_matching":
        if conversation.matched_property_id:
            return {"stage_status": "no_action", "reason": "Matched conversation already handled"}
        return await run_initial_enquiry_pipeline(session, conversation_id, generator)

    if stage == END_STAGE:
        return {"stage_status": "no_action", "reason": "Conversation is at end stage"}

    return {"stage_status": "no_action", "reason": f"No automatic pipeline for stage {stage}"}
