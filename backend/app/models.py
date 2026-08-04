import builtins
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, and_, func, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from .db import Base
from .media_storage import describe_media_storage
from .tenant import DEFAULT_WHATSAPP_ACCOUNT_ID, DEFAULT_WORKSPACE_ID


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WorkspaceScopedMixin:
    workspace_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("workspaces.id"),
        default=DEFAULT_WORKSPACE_ID,
        nullable=False,
        index=True,
    )


class WhatsappAccountScopedMixin(WorkspaceScopedMixin):
    whatsapp_account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("whatsapp_accounts.id"),
        default=DEFAULT_WHATSAPP_ACCOUNT_ID,
        nullable=False,
        index=True,
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)

    members: Mapped[list["WorkspaceMember"]] = relationship(back_populates="workspace")
    whatsapp_accounts: Mapped[list["WhatsappAccount"]] = relationship(back_populates="workspace")


class WorkspaceMember(TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "auth_user_id", name="uq_workspace_members_auth_user"),
        UniqueConstraint("workspace_id", "email", name="uq_workspace_members_email"),
        Index("ix_workspace_members_workspace_role", "workspace_id", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    auth_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, default="owner", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="members")


class WhatsappAccount(TimestampMixin, Base):
    __tablename__ = "whatsapp_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "account_key", name="uq_whatsapp_accounts_workspace_key"),
        UniqueConstraint("workspace_id", "phone_jid", name="uq_whatsapp_accounts_workspace_phone_jid"),
        Index("ix_whatsapp_accounts_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    account_key: Mapped[str] = mapped_column(String, nullable=False)
    phone_jid: Mapped[Optional[str]] = mapped_column(String, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String)
    bridge_base_url: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="whatsapp_accounts")


class Contact(TimestampMixin, WhatsappAccountScopedMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "whatsapp_account_id", "chat_jid", name="uq_contacts_workspace_account_chat"),
        Index("ix_contacts_workspace_status", "workspace_id", "status"),
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
        Index("ix_conversations_workspace_account_status", "workspace_id", "whatsapp_account_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, nullable=False, index=True)
    whatsapp_account_id: Mapped[str] = mapped_column(
        ForeignKey("whatsapp_accounts.id"),
        default=DEFAULT_WHATSAPP_ACCOUNT_ID,
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, default="whatsapp", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    current_stage: Mapped[Optional[str]] = mapped_column(String)
    host_property_id: Mapped[Optional[str]] = mapped_column(String)
    matched_property_id: Mapped[Optional[str]] = mapped_column(String)
    current_suggested_property_id: Mapped[Optional[str]] = mapped_column(String)
    latest_inbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    latest_outbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    contact: Mapped[Contact] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(TimestampMixin, WhatsappAccountScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "whatsapp_account_id", "chat_jid", "message_id", name="uq_messages_workspace_account_chat_message"),
    )

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
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, nullable=False, index=True)
    whatsapp_account_id: Mapped[str] = mapped_column(
        ForeignKey("whatsapp_accounts.id"),
        default=DEFAULT_WHATSAPP_ACCOUNT_ID,
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("conversations.id"), index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    input_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String)


class Property(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("workspace_id", "property_id", name="uq_properties_workspace_property_id"),
        Index("ix_properties_workspace_status", "workspace_id", "status"),
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
        primaryjoin=lambda: and_(
            Property.workspace_id == foreign(PropertyMedia.workspace_id),
            Property.property_id == foreign(PropertyMedia.property_id),
        ),
    )


class PropertyPlaybook(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "property_playbooks"
    __table_args__ = (UniqueConstraint("workspace_id", "property_id", name="uq_property_playbooks_workspace_property"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    initial_reply_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    qualification_suitable_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    qualification_not_suitable_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    swing_suggestion_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PropertyMedia(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "property_media"
    __table_args__ = (UniqueConstraint("workspace_id", "property_id", "file_path", name="uq_property_media_workspace_path"),)

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
        primaryjoin=lambda: and_(
            foreign(PropertyMedia.workspace_id) == Property.workspace_id,
            foreign(PropertyMedia.property_id) == Property.property_id,
        ),
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


class SwingCandidate(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "swing_candidates"
    __table_args__ = (UniqueConstraint("workspace_id", "source_property_id", "candidate_property_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    candidate_property_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppConfig(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "app_config"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_app_config_workspace_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
