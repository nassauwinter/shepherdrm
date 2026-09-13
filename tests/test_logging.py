"""Verify structured logs expose only explicitly approved request metadata."""

import logging

from shepherd_rm.logging import JsonFormatter


def test_json_formatter_omits_sensitive_extra_fields() -> None:
    """Passwords, tokens, keys, values, and references in record extras are omitted."""
    canaries = {
        "password": "password-canary",
        "authorization": "bearer-token-canary",
        "encryption_key": "encryption-key-canary",
        "secret_value": "managed-value-canary",
        "external_reference": "external-reference-canary",
    }
    record = logging.LogRecord(
        name="shepherd_rm.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "safe-correlation"
    for field, value in canaries.items():
        setattr(record, field, value)

    output = JsonFormatter().format(record)

    assert "safe-correlation" in output
    assert all(canary not in output for canary in canaries.values())
