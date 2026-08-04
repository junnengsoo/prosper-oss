from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


QualificationStatus = Literal["match", "incomplete", "clarify_fit", "not_match", "unsure"]
CONTINUABLE_STATUSES = frozenset({"incomplete", "clarify_fit"})
MAX_QUALIFICATION_TURNS = 5


class QualificationOutputError(ValueError):
    """Raised when a model response cannot be used as a qualification turn."""


class QualificationTurn(BaseModel):
    """The validated model response for one qualification turn."""

    model_config = ConfigDict(extra="allow")

    qualification_status: QualificationStatus
    message: str = ""
    reason: str = ""
    extracted_facts: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("message", "reason", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("extracted_facts", mode="before")
    @classmethod
    def normalize_facts(cls, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @field_validator("missing_fields", mode="before")
    @classmethod
    def normalize_missing_fields(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(field).strip() for field in value if str(field).strip()]

    @model_validator(mode="after")
    def message_required_for_continuable_turn(self) -> "QualificationTurn":
        if self.qualification_status in CONTINUABLE_STATUSES and not self.message:
            raise ValueError("qualification turns that continue the conversation require message")
        return self

    @classmethod
    def from_model_output(cls, output: Any) -> "QualificationTurn":
        """Validate direct or legacy nested qualification output."""
        if not isinstance(output, Mapping):
            raise QualificationOutputError("Qualification output must be an object")

        candidate: dict[str, Any] = dict(output)
        if "qualification_status" not in candidate and isinstance(candidate.get("qualification"), Mapping):
            candidate = dict(candidate["qualification"])
        if "message" not in candidate and "assistant_message" in candidate:
            candidate["message"] = candidate["assistant_message"]

        try:
            return cls.model_validate(candidate)
        except ValidationError as error:
            raise QualificationOutputError(f"Invalid qualification output: {error}") from error

    def as_result(self) -> dict[str, Any]:
        """Return the legacy-compatible dictionary consumed by the action layer."""
        return self.model_dump(mode="json", exclude_none=True)


class QualificationState(BaseModel):
    """Conversation-level state used to bound and inspect qualification."""

    turn_count: int = 0
    max_turns: int = MAX_QUALIFICATION_TURNS
    extracted_facts: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    last_status: QualificationStatus | None = None

    @model_validator(mode="after")
    def validate_turn_bounds(self) -> "QualificationState":
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.turn_count < 0:
            raise ValueError("turn_count must not be negative")
        return self


class QualificationLoop:
    """Apply validated qualification turns and enforce a finite conversation loop."""

    def __init__(self, *, state: QualificationState | None = None, turn_count: int = 0, max_turns: int = MAX_QUALIFICATION_TURNS):
        self.state = state or QualificationState(turn_count=turn_count, max_turns=max_turns)

    @property
    def exhausted(self) -> bool:
        return self.state.turn_count >= self.state.max_turns

    def advance(self, output: Any) -> dict[str, Any]:
        if self.exhausted:
            return self.handoff("Maximum qualification turns reached")
        turn = QualificationTurn.from_model_output(output)

        self.state.turn_count += 1
        self.state.extracted_facts.update(turn.extracted_facts)
        self.state.missing_fields = list(turn.missing_fields)
        self.state.last_status = turn.qualification_status

        if self.state.turn_count >= self.state.max_turns and turn.qualification_status in CONTINUABLE_STATUSES:
            return self.handoff("Maximum qualification turns reached")

        result = turn.as_result()
        result.update(
            {
                "qualification_turn_count": self.state.turn_count,
                "qualification_max_turns": self.state.max_turns,
                "handoff_required": turn.qualification_status == "unsure",
            }
        )
        return result

    def handoff(self, reason: str) -> dict[str, Any]:
        self.state.last_status = "unsure"
        return {
            "qualification_status": "unsure",
            "message": "",
            "reason": reason,
            "extracted_facts": dict(self.state.extracted_facts),
            "missing_fields": list(self.state.missing_fields),
            "qualification_turn_count": self.state.turn_count,
            "qualification_max_turns": self.state.max_turns,
            "handoff_required": True,
        }
