import builtins
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, and_, func, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from .db import Base
from .media_storage import describe_media_storage


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("chat_jid", name="uq_contacts_chat"),
        Index("ix_contacts_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_jid: Mapped[str] = mapped_column(String, index=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String)
    phone: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="contact")


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "one_open_conversation_per_contact",
            "contact_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, default="whatsapp", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    current_stage: Mapped[Optional[str]] = mapped_column(String)
    matched_property_id: Mapped[Optional[str]] = mapped_column(String)
    latest_inbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    latest_outbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    contact: Mapped[Contact] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("chat_jid", "message_id", name="uq_messages_chat_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    chat_jid: Mapped[str] = mapped_column(String, index=True, nullable=False)
    sender_jid: Mapped[Optional[str]] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, default="whatsapp", nullable=False)
    raw_type: Mapped[Optional[str]] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class StageRun(TimestampMixin, Base):
    __tablename__ = "stage_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("conversations.id"), index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    input_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String)


class Property(TimestampMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("property_id", name="uq_properties_property_id"),
        Index("ix_properties_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    property_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="unknown", nullable=False)
    property_type: Mapped[Optional[str]] = mapped_column(String)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer)
    bathrooms: Mapped[Optional[int]] = mapped_column(Integer)
    asking_rent: Mapped[Optional[float]] = mapped_column(Float)
    available_from: Mapped[Optional[str]] = mapped_column(String)
    full_address: Mapped[Optional[str]] = mapped_column(Text)
    property_url: Mapped[Optional[str]] = mapped_column(Text)
    propertyguru_listing_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    landlord_profile_requirements: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tenant_facing_caveats: Mapped[str] = mapped_column(Text, default="", nullable=False)

    media: Mapped[list["PropertyMedia"]] = relationship(
        back_populates="property",
        order_by="PropertyMedia.sort_order",
        primaryjoin=lambda: and_(Property.property_id == foreign(PropertyMedia.property_id)),
    )


class PropertyPlaybook(TimestampMixin, Base):
    __tablename__ = "property_playbooks"
    __table_args__ = (UniqueConstraint("property_id", name="uq_property_playbooks_property"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    initial_reply_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    qualification_suitable_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    qualification_not_suitable_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PropertyMedia(TimestampMixin, Base):
    __tablename__ = "property_media"
    __table_args__ = (UniqueConstraint("property_id", "file_path", name="uq_property_media_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    storage_provider: Mapped[str] = mapped_column(String, default="local", nullable=False)
    storage_bucket: Mapped[Optional[str]] = mapped_column(String)
    storage_object_path: Mapped[Optional[str]] = mapped_column(Text)
    signed_url: Mapped[Optional[str]] = mapped_column(Text)
    signed_url_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    public_url: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    property: Mapped[Property] = relationship(
        back_populates="media",
        primaryjoin=lambda: foreign(PropertyMedia.property_id) == Property.property_id,
    )

    @builtins.property
    def file_exists(self) -> bool:
        return describe_media_storage(self).local_file_exists

    @builtins.property
    def sendable(self) -> bool:
        return describe_media_storage(self).sendable

    @builtins.property
    def storage_reference(self) -> str:
        return describe_media_storage(self).display_reference


class AppConfig(TimestampMixin, Base):
    __tablename__ = "app_config"
    __table_args__ = (UniqueConstraint("key", name="uq_app_config_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
