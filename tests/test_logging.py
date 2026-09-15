"""Verify structured logs expose only explicitly approved request metadata."""

import ast
import logging
import sys
from pathlib import Path

from shepherd_rm.logging import JsonFormatter

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "shepherd_rm"


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


def test_json_formatter_omits_formatted_arguments_and_exception_messages() -> None:
    """Log arguments and exception messages cannot carry secret values into JSON output."""
    argument_canary = "argument-secret-canary"
    exception_canary = "exception-secret-canary"
    try:
        raise ValueError(exception_canary)
    except ValueError:
        exception_info = sys.exc_info()
    record = logging.LogRecord(
        name="shepherd_rm.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="operation failed: %s",
        args=(argument_canary,),
        exc_info=exception_info,
    )

    output = JsonFormatter().format(record)

    assert '"message":"operation failed: %s"' in output
    assert '"exception_type":"ValueError"' in output
    assert argument_canary not in output
    assert exception_canary not in output


def test_application_log_calls_use_static_event_names() -> None:
    """Application logging calls cannot interpolate runtime or secret-bearing values."""
    for source_path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"critical", "debug", "error", "exception", "info", "warning"}:
                continue
            assert len(node.args) == 1, f"{source_path}: log arguments can expose runtime values"
            assert isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str), (
                f"{source_path}: log event names must be static strings"
            )
