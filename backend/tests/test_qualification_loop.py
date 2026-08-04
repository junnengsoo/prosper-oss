from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.database.models import Conversation, StageRun
from app.qualification import QualificationLoop, QualificationOutputError, QualificationState
from app.pipeline import run_qualification
from app.schemas import PropertyIn
from app.database.seed import seed_all
from app.services import append_message, get_or_create_active_conversation, get_or_create_contact, upsert_property


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with SessionLocal() as db:
            seed_all(db)
            yield db
    finally:
        engine.dispose()


def qualification_conversation(session: Session) -> Conversation:
    property_ = upsert_property(
        session,
        PropertyIn(
            property_id="QUAL-001",
            property_name="Qualification Test Unit",
            status="available",
            property_type="HDB",
            bedrooms=3,
            bathrooms=2,
            asking_rent=3400,
            full_address="Qualification Test Unit",
            landlord_profile_requirements="Up to 4 occupants",
        ),
    )
    contact = get_or_create_contact(session, "qualification-test@s.whatsapp.net", "Qualification Test")
    conversation = get_or_create_active_conversation(session, contact, "fake_chat")
    conversation.matched_property_id = property_.property_id
    conversation.current_stage = "qualification"
    append_message(
        session,
        conversation,
        contact.chat_jid,
        "qualification-message-1",
        "We are a family of four and need to move in soon.",
        1_000,
        "inbound",
        "fake_chat",
        contact.chat_jid,
        "fake_text",
    )
    session.commit()
    return conversation


def test_qualification_loop_preserves_model_reply_and_structured_state():
    loop = QualificationLoop(max_turns=3)

    result = loop.advance(
        {
            "qualification_status": "incomplete",
            "message": "What type of pass do the occupants hold?",
            "reason": "Pass type is still needed.",
            "extracted_facts": {"occupants": 4, "budget": 3400},
            "missing_fields": ["pass_type"],
        }
    )

    assert result["message"] == "What type of pass do the occupants hold?"
    assert result["qualification_turn_count"] == 1
    assert result["qualification_max_turns"] == 3
    assert result["handoff_required"] is False
    assert loop.state.extracted_facts == {"occupants": 4, "budget": 3400}
    assert loop.state.missing_fields == ["pass_type"]


def test_qualification_loop_accepts_legacy_nested_output():
    loop = QualificationLoop(max_turns=3)

    result = loop.advance(
        {
            "qualification": {
                "qualification_status": "match",
                "message": "The profile appears suitable.",
                "reason": "Required fields are present.",
            }
        }
    )

    assert result["qualification_status"] == "match"
    assert result["message"] == "The profile appears suitable."
    assert result["qualification_turn_count"] == 1


def test_qualification_loop_hands_off_after_maximum_continuable_turns():
    loop = QualificationLoop(max_turns=2)

    loop.advance(
        {
            "qualification_status": "incomplete",
            "message": "What is the move-in date?",
            "missing_fields": ["move_in_date"],
        }
    )
    result = loop.advance(
        {
            "qualification_status": "clarify_fit",
            "message": "Can you move in earlier?",
            "missing_fields": ["move_in_date"],
        }
    )

    assert result["qualification_status"] == "unsure"
    assert result["handoff_required"] is True
    assert result["qualification_turn_count"] == 2
    assert "Maximum qualification turns" in result["reason"]


def test_qualification_loop_rejects_continuable_turn_without_reply():
    loop = QualificationLoop()

    with pytest.raises(QualificationOutputError, match="require message"):
        loop.advance(
            {
                "qualification_status": "incomplete",
                "missing_fields": ["budget"],
            }
        )


def test_exhausted_loop_hands_off_without_validating_another_model_response():
    loop = QualificationLoop(turn_count=2, max_turns=2)

    result = loop.advance({"not_a_qualification_response": True})

    assert result["qualification_status"] == "unsure"
    assert result["handoff_required"] is True
    assert result["qualification_turn_count"] == 2


def test_qualification_state_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="max_turns must be positive"):
        QualificationState(max_turns=0)


def test_pipeline_records_typed_qualification_turn_and_keeps_conversation_open(session):
    conversation = qualification_conversation(session)

    async def generator(_messages):
        return {
            "qualification_status": "incomplete",
            "message": "What type of pass do the occupants hold?",
            "reason": "Pass type is still needed.",
            "extracted_facts": {"occupants": 4},
            "missing_fields": ["pass_type"],
        }

    result = asyncio.run(run_qualification(session, conversation.id, generator))

    assert result["qualification_status"] == "incomplete"
    assert result["qualification_turn_count"] == 1
    assert result["message"] == "What type of pass do the occupants hold?"
    assert conversation.current_stage == "qualification"
    stage_run = session.scalar(select(StageRun).where(StageRun.conversation_id == conversation.id, StageRun.stage == "qualification"))
    assert stage_run is not None
    assert '"missing_fields": ["pass_type"]' in (stage_run.output_json or "")


def test_pipeline_rejects_invalid_qualification_output_and_records_error(session):
    conversation = qualification_conversation(session)

    async def generator(_messages):
        return {"qualification_status": "incomplete", "missing_fields": ["budget"]}

    result = asyncio.run(run_qualification(session, conversation.id, generator))

    assert result["qualification_status"] == "unsure"
    assert conversation.current_stage == "end"
    stage_run = session.scalar(select(StageRun).where(StageRun.conversation_id == conversation.id, StageRun.stage == "qualification"))
    assert stage_run is not None
    assert stage_run.status == "error"
    assert "require message" in (stage_run.error or "")
