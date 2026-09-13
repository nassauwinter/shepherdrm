"""Verify audit metadata accepts only deliberately safe domain fields."""

import pytest

from shepherd_rm.audit import validate_audit_metadata


def test_audit_metadata_rejects_sensitive_and_unclassified_fields() -> None:
    """Unapproved password, token, key, value, and reference fields cannot be audited."""
    for field in (
        "password",
        "bearer_token",
        "encryption_key",
        "secret_value",
        "external_reference",
        "unclassified_note",
    ):
        with pytest.raises(ValueError, match="Unsafe audit metadata fields"):
            validate_audit_metadata({field: "secret-canary"})


def test_audit_metadata_accepts_current_value_free_domain_fields() -> None:
    """The fields used by current domain audit events remain accepted."""
    validate_audit_metadata(
        {
            "access_path": "Administration",
            "lease_id": "00000000-0000-0000-0000-000000000001",
            "mode": "Managed",
            "resource_id": "00000000-0000-0000-0000-000000000002",
            "target_id": "00000000-0000-0000-0000-000000000003",
            "target_type": "Group",
        }
    )
