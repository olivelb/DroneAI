"""Identity and access models."""

from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from shared.database_schema import (
    ACCESS_AUDIT_ACTOR_REALMS,
    ACCESS_AUDIT_OUTCOMES,
    ACCESS_AUDIT_RESOURCE_TYPES,
    API_CREDENTIAL_STATUSES,
    IDENTITY_AUDIT_ACTIONS,
    IDENTITY_CAPABILITY_PURPOSES,
    IDENTITY_CAPABILITY_STATUSES,
    ORGANIZATION_MEMBER_ROLES,
    ORGANIZATION_MEMBER_STATUSES,
    ORGANIZATION_STATUSES,
    PLATFORM_AUDIT_ACTIONS,
    PLATFORM_CREDENTIAL_STATUSES,
    PLATFORM_MEMBER_ROLES,
    PLATFORM_MEMBER_STATUSES,
    AppendOnlyAuditMixin,
    Base,
    RequiredTimestampMixin,
    RevocableCredentialMixin,
    _values_check,
)


class Organization(RequiredTimestampMixin, Base):
    """Durable customer boundary for every SaaS-owned resource."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            _values_check("status", ORGANIZATION_STATUSES),
            name="ck_organizations_status",
        ),
    )

    id = Column(String(64), primary_key=True)
    display_name = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class OrganizationMember(RequiredTimestampMixin, Base):
    """Organization-scoped human or service identity and current role."""

    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_members_subject",
        ),
        Index(
            "ix_organization_members_org_status_role",
            "organization_id",
            "status",
            "role",
        ),
        CheckConstraint(
            _values_check("status", ORGANIZATION_MEMBER_STATUSES),
            name="ck_organization_members_status",
        ),
        CheckConstraint(
            _values_check("role", ORGANIZATION_MEMBER_ROLES),
            name="ck_organization_members_role",
        ),
        CheckConstraint(
            "auth_version >= 1",
            name="ck_organization_members_auth_version",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    auth_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class ApiCredential(RevocableCredentialMixin, RequiredTimestampMixin, Base):
    """Revocable API credential; only its peppered digest is persisted."""

    __tablename__ = "api_credentials"
    __table_args__ = (
        Index(
            "ix_api_credentials_org_member_status",
            "organization_id",
            "member_id",
            "status",
        ),
        CheckConstraint(
            _values_check("status", API_CREDENTIAL_STATUSES),
            name="ck_api_credentials_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    member_id = Column(
        String(36),
        ForeignKey("organization_members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rotated_from_id = Column(
        String(36),
        ForeignKey("api_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )


class IdentityAuditEvent(AppendOnlyAuditMixin, Base):
    """Database-protected identity and access lifecycle history."""

    __tablename__ = "identity_audit_events"
    __table_args__ = (
        Index(
            "ix_identity_audit_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_identity_audit_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        CheckConstraint(
            _values_check("action", IDENTITY_AUDIT_ACTIONS),
            name="ck_identity_audit_action",
        ),
    )

    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action = Column(String(48), nullable=False)
    target_type = Column(String(32), nullable=False)
    target_id = Column(String(256), nullable=False)


class IdentityCapability(RequiredTimestampMixin, Base):
    """One-time invitation or self-issued recovery capability."""

    __tablename__ = "identity_capabilities"
    __table_args__ = (
        Index(
            "ix_identity_capabilities_org_purpose_status",
            "organization_id",
            "purpose",
            "status",
        ),
        CheckConstraint(
            _values_check("purpose", IDENTITY_CAPABILITY_PURPOSES),
            name="ck_identity_capabilities_purpose",
        ),
        CheckConstraint(
            _values_check("status", IDENTITY_CAPABILITY_STATUSES),
            name="ck_identity_capabilities_status",
        ),
        CheckConstraint(
            _values_check("role", ORGANIZATION_MEMBER_ROLES),
            name="ck_identity_capabilities_role",
        ),
        CheckConstraint(
            "(purpose = 'invitation' AND member_id IS NULL) OR (purpose = 'recovery' AND member_id IS NOT NULL)",
            name="ck_identity_capabilities_member_purpose",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    member_id = Column(
        String(36),
        ForeignKey("organization_members.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    purpose = Column(String(32), nullable=False)
    subject = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    redeemed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(256), nullable=True)
    created_by = Column(String(256), nullable=False)


class PlatformMember(RequiredTimestampMixin, Base):
    """Global support identity with no implicit tenant membership."""

    __tablename__ = "platform_members"
    __table_args__ = (
        CheckConstraint(
            _values_check("role", PLATFORM_MEMBER_ROLES),
            name="ck_platform_members_role",
        ),
        CheckConstraint(
            _values_check("status", PLATFORM_MEMBER_STATUSES),
            name="ck_platform_members_status",
        ),
        CheckConstraint(
            "auth_version >= 1",
            name="ck_platform_members_auth_version",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    subject = Column(String(256), nullable=False, unique=True, index=True)
    role = Column(String(32), nullable=False, default="support")
    status = Column(String(32), nullable=False, default="active")
    auth_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class PlatformCredential(
    RevocableCredentialMixin,
    RequiredTimestampMixin,
    Base,
):
    """Revocable credential for the isolated platform-support realm."""

    __tablename__ = "platform_credentials"
    __table_args__ = (
        Index(
            "ix_platform_credentials_member_status",
            "member_id",
            "status",
        ),
        CheckConstraint(
            _values_check("status", PLATFORM_CREDENTIAL_STATUSES),
            name="ck_platform_credentials_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    member_id = Column(
        String(36),
        ForeignKey("platform_members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rotated_from_id = Column(
        String(36),
        ForeignKey("platform_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )


class PlatformAuditEvent(AppendOnlyAuditMixin, Base):
    """Global append-only support action history, separate from tenants."""

    __tablename__ = "platform_audit_events"
    __table_args__ = (
        Index(
            "ix_platform_audit_action_created",
            "action",
            "created_at",
        ),
        Index(
            "ix_platform_audit_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        CheckConstraint(
            _values_check("action", PLATFORM_AUDIT_ACTIONS),
            name="ck_platform_audit_action",
        ),
    )

    action = Column(String(64), nullable=False)
    target_type = Column(String(32), nullable=False)
    target_id = Column(String(256), nullable=False)


class AccessAuditEvent(AppendOnlyAuditMixin, Base):
    """Append-only evidence of explicit cross-member data access."""

    __tablename__ = "access_audit_events"
    __table_args__ = (
        Index(
            "ix_access_audit_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_access_audit_owner_created",
            "organization_id",
            "target_owner_subject",
            "created_at",
        ),
        Index(
            "ix_access_audit_resource_created",
            "resource_type",
            "resource_id",
            "created_at",
        ),
        CheckConstraint(
            _values_check("actor_realm", ACCESS_AUDIT_ACTOR_REALMS),
            name="ck_access_audit_actor_realm",
        ),
        CheckConstraint(
            _values_check("resource_type", ACCESS_AUDIT_RESOURCE_TYPES),
            name="ck_access_audit_resource_type",
        ),
        CheckConstraint(
            _values_check("outcome", ACCESS_AUDIT_OUTCOMES),
            name="ck_access_audit_outcome",
        ),
        CheckConstraint(
            "length(action) BETWEEN 1 AND 64",
            name="ck_access_audit_action_length",
        ),
    )

    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    actor_role = Column(String(32), nullable=False)
    actor_realm = Column(String(32), nullable=False, default="tenant")
    actor_member_id = Column(String(36), nullable=True)
    actor_credential_id = Column(String(36), nullable=True)
    action = Column(String(64), nullable=False)
    target_owner_subject = Column(String(256), nullable=False)
    resource_type = Column(String(32), nullable=False)
    resource_id = Column(String(256), nullable=True)
    outcome = Column(String(32), nullable=False, default="authorized")
