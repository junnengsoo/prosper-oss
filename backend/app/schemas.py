from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ContactStatus = Literal["active", "paused", "ignored"]
ConversationStatus = Literal["active", "paused", "handover", "closed"]
MessageDirection = Literal["inbound", "outbound", "human"]
MessageSource = Literal["whatsapp", "fake_chat"]
MediaType = Literal["photo", "video"]


class MeOut(BaseModel):
    auth_user_id: str
    email: Optional[str]


class AuthLoginIn(BaseModel):
    password: str = Field(min_length=1)


class AuthSessionOut(BaseModel):
    authenticated: bool
    email: Optional[str] = None


class ContactOut(BaseModel):
    id: int
    chat_jid: str
    display_name: Optional[str]
    phone: Optional[str]
    status: str
    status_reason: Optional[str]
    last_message_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ContactStatusUpdate(BaseModel):
    status: ContactStatus
    status_reason: str = ""


class ConversationOut(BaseModel):
    id: int
    contact_id: int
    source: str
    status: str
    current_stage: Optional[str]
    matched_property_id: Optional[str]
    latest_message_text: Optional[str] = None
    latest_message_timestamp_ms: Optional[int] = None
    latest_message_direction: Optional[str] = None

    model_config = {"from_attributes": True}


class StartNewEnquiryRequest(BaseModel):
    latest_message_text: str = ""


class ConversationStageUpdate(BaseModel):
    stage: Literal["rental_listing_matching", "end"]
    resume_contact: bool = True
    model_config = {"extra": "forbid"}


class TriageOutputContract(BaseModel):
    is_initial_rental_enquiry: bool
    confidence: Literal["high", "medium", "low"]
    reason: str = ""
    model_config = ConfigDict(extra="forbid", strict=True)


class RentalListingMatchedPropertyContract(BaseModel):
    property_id: str = Field(min_length=1)
    property_name: str = ""
    reason: str = ""
    model_config = ConfigDict(extra="forbid", strict=True)


class RentalListingMatchingOutputContract(BaseModel):
    match_status: Literal["matched", "no_property_mentioned", "unmatched_property", "ambiguous_multiple_matches"]
    mentioned_property_raw: str = ""
    mentioned_listing_url: str = ""
    extracted_listing_id: str = ""
    matched_by: Literal["propertyguru_listing_id", "property_name", "full_address", "none"] = "none"
    matched_properties: list[RentalListingMatchedPropertyContract] = Field(default_factory=list)
    reason: str = ""
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def matched_status_requires_exactly_one_property(self) -> "RentalListingMatchingOutputContract":
        if self.match_status == "matched" and len(self.matched_properties) != 1:
            raise ValueError("matched output must include exactly one matched property")
        return self


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    chat_jid: str
    sender_jid: Optional[str]
    message_id: str
    direction: str
    source: str
    raw_type: Optional[str]
    text: str
    timestamp_ms: int

    model_config = {"from_attributes": True}


class PropertyIn(BaseModel):
    property_id: str
    property_name: str
    status: str = "unknown"
    property_type: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    asking_rent: Optional[float] = None
    available_from: Optional[str] = None
    full_address: Optional[str] = None
    property_url: Optional[str] = None
    propertyguru_listing_id: Optional[str] = None
    tenant_facing_caveats: str = ""
    model_config = {"extra": "forbid"}


class PropertyMediaIn(BaseModel):
    media_type: MediaType
    file_path: str = Field(min_length=1)
    caption: str = ""
    sort_order: int = 0
    enabled: bool = True

    @field_validator("file_path")
    @classmethod
    def file_path_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("file_path must not be blank")
        return stripped

    @field_validator("caption")
    @classmethod
    def caption_normalized(cls, value: str) -> str:
        return value.strip()


class PropertyMediaOut(PropertyMediaIn):
    id: int
    property_id: str
    file_exists: bool
    sendable: bool
    storage_reference: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PropertyOut(PropertyIn):
    id: int
    created_at: datetime
    updated_at: datetime
    media: list[PropertyMediaOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PropertyBulkDeleteIn(BaseModel):
    property_ids: list[str] = Field(min_length=1)

    @field_validator("property_ids")
    @classmethod
    def property_ids_must_be_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            property_id = item.strip() if isinstance(item, str) else ""
            if not property_id:
                raise ValueError("property_ids must not contain blank values")
            if property_id not in seen:
                normalized.append(property_id)
                seen.add(property_id)
        return normalized


class PropertyDeleteSummaryOut(BaseModel):
    deleted_property_ids: list[str]
    deleted_counts: dict[str, int]


class PlaybookBlock(BaseModel):
    type: Literal["message", "delay", "gallery"]
    text: Optional[str] = None
    seconds: Optional[float] = None
    mode: Optional[Literal["enabled_property_gallery"]] = None
    model_config = {"extra": "forbid"}


class PropertyPlaybookIn(BaseModel):
    initial_reply_blocks: list[PlaybookBlock] = Field(default_factory=list)
    enabled: bool = True
    model_config = {"extra": "forbid"}


class PropertyPlaybookOut(PropertyPlaybookIn):
    id: Optional[int] = None
    property_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PropertyMediaExportOut(PropertyMediaIn):
    id: int
    property_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PropertyExportOut(PropertyIn):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FakeInboundMessage(BaseModel):
    chat_jid: str
    text: str
    display_name: Optional[str] = None
    message_id: Optional[str] = None
    timestamp_ms: Optional[int] = None


class AppConfigOut(BaseModel):
    values: dict[str, str]


class AppConfigUpdate(BaseModel):
    values: dict[str, str]


class HealthOut(BaseModel):
    ok: bool
    app: str


class BridgeInboundMessage(BaseModel):
    chat_jid: str
    sender_jid: Optional[str] = None
    message_id: str
    timestamp_ms: int
    from_me: bool
    text: str
    raw_type: Optional[str] = None
    display_name: Optional[str] = None


class BridgeInboundBatch(BaseModel):
    messages: list[BridgeInboundMessage] = Field(default_factory=list)


class BridgeAck(BaseModel):
    accepted: bool
    reason: str
    data: dict[str, Any] = {}


class PipelineRunResponse(BaseModel):
    conversation_id: Optional[int]
    result: dict[str, Any]


class FakeChatResetOut(BaseModel):
    contacts_deleted: int
    conversations_deleted: int
    messages_deleted: int
    stage_runs_deleted: int


class StageRunOut(BaseModel):
    id: int
    conversation_id: Optional[int]
    stage: str
    input_snapshot: str
    output_json: Optional[str]
    status: str
    error: Optional[str]
    model: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfigExportOut(BaseModel):
    exported_at: datetime
    app: str
    config: dict[str, str]
    properties: list[PropertyExportOut]
    property_media: list[PropertyMediaExportOut]
    playbooks: list[PropertyPlaybookOut] = Field(default_factory=list)
