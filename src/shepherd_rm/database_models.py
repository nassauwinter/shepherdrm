"""Declare the SQLAlchemy metadata that defines Shepherd RM's database model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
    )


class Principal(TimestampMixin, Base):
    __tablename__ = "principals"
    __table_args__ = (
        CheckConstraint("kind IN ('User', 'Service')", name="kind_allowed"),
        CheckConstraint("role IN ('Admin', 'User')", name="role_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16), server_default="User")
    name: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordCredential(Base):
    __tablename__ = "password_credentials"

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="expiry_after_creation"
        ),
        Index("ix_api_tokens_principal_active", "principal_id", "revoked_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Group(TimestampMixin, Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (Index("ix_group_memberships_principal_id", "principal_id"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class Resource(TimestampMixin, Base):
    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint("sharing_mode IN ('Exclusive', 'Shared')", name="sharing_mode_allowed"),
        CheckConstraint(
            "visibility_mode IN ('Public', 'Restricted')", name="visibility_mode_allowed"
        ),
        CheckConstraint(
            "expiration_mode IN ('Required', 'Optional')", name="expiration_mode_allowed"
        ),
        CheckConstraint(
            "default_ttl_seconds IS NULL OR default_ttl_seconds > 0",
            name="default_ttl_positive",
        ),
        CheckConstraint("max_ttl_seconds IS NULL OR max_ttl_seconds > 0", name="max_ttl_positive"),
        CheckConstraint(
            "default_ttl_seconds IS NULL OR max_ttl_seconds IS NULL "
            "OR default_ttl_seconds <= max_ttl_seconds",
            name="default_ttl_within_maximum",
        ),
        CheckConstraint(
            "expiration_mode = 'Optional' OR default_ttl_seconds IS NOT NULL",
            name="required_expiration_has_default",
        ),
        CheckConstraint(
            "operational_status IN ('Active', 'Cleaning', 'Quarantined', 'Disabled')",
            name="operational_status_allowed",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "ix_resources_match",
            "type",
            "sharing_mode",
            "operational_status",
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_resources_labels_gin", "labels", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(255), index=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    sharing_mode: Mapped[str] = mapped_column(String(16))
    visibility_mode: Mapped[str] = mapped_column(String(16), server_default="Restricted")
    expiration_mode: Mapped[str] = mapped_column(String(16), server_default="Optional")
    default_ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    max_ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    operational_status: Mapped[str] = mapped_column(String(16), server_default="Active")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    last_leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ResourcePrincipalGrant(Base):
    __tablename__ = "resource_principal_grants"
    __table_args__ = (Index("ix_resource_principal_grants_principal_id", "principal_id"),)

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class ResourceGroupGrant(Base):
    __tablename__ = "resource_group_grants"
    __table_args__ = (Index("ix_resource_group_grants_group_id", "group_id"),)

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )


class ResourceSecret(TimestampMixin, Base):
    __tablename__ = "resource_secrets"
    __table_args__ = (
        CheckConstraint("mode IN ('Managed', 'External')", name="mode_allowed"),
        CheckConstraint(
            "(mode = 'Managed' AND encrypted_value IS NOT NULL "
            "AND encryption_key_id IS NOT NULL AND external_provider IS NULL "
            "AND external_reference IS NULL) OR "
            "(mode = 'External' AND encrypted_value IS NULL "
            "AND encryption_key_id IS NULL AND external_provider IS NOT NULL "
            "AND external_reference IS NOT NULL)",
            name="material_matches_mode",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        UniqueConstraint("resource_id", "name", name="uq_resource_secrets_resource_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(1000))
    mode: Mapped[str] = mapped_column(String(16))
    encrypted_value: Mapped[bytes | None] = mapped_column(LargeBinary)
    encryption_key_id: Mapped[str | None] = mapped_column(String(255))
    external_provider: Mapped[str | None] = mapped_column(String(255))
    external_reference: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class Lease(Base):
    __tablename__ = "leases"
    __table_args__ = (
        CheckConstraint(
            "expires_at IS NULL OR expires_at > acquired_at",
            name="expiration_after_acquisition",
        ),
        CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND end_reason IS NOT NULL)",
            name="terminal_fields_consistent",
        ),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('Released', 'Expired', 'Revoked')",
            name="end_reason_allowed",
        ),
        UniqueConstraint("acquired_by", "idempotency_key", name="uq_leases_principal_idempotency"),
        Index(
            "ix_leases_resource_active", "resource_id", postgresql_where=text("ended_at IS NULL")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="RESTRICT"), index=True
    )
    acquired_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    consumer: Mapped[str | None] = mapped_column(String(500))
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_subject", "subject_type", "subject_id"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(255))
    subject_type: Mapped[str] = mapped_column(String(100))
    subject_id: Mapped[str] = mapped_column(String(255))
    correlation_id: Mapped[str] = mapped_column(String(255), index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
