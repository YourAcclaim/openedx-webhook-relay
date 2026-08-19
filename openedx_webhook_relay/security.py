"""
Signing and PII payload-filtering utilities.

Pure functions only — no Django ORM, no network I/O — so they are cheap to
unit test exhaustively and safe to call from both the synchronous receiver
(for the fast enable/filter check) and the async Celery task (for signing).
"""

import hashlib
import hmac
import json
from typing import Any


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """
    Return HMAC-SHA256 hex digest prefixed with ``sha256=``.

    Receivers on the far end should verify with constant-time comparison:
        expected = sign_payload(body, secret)
        hmac.compare_digest(received, expected)
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(payload_bytes: bytes, secret: str, received_signature: str) -> bool:
    """Verify an incoming signature (used by tests and as receiver reference code)."""
    if not received_signature:
        return False
    expected = sign_payload(payload_bytes, secret)
    return hmac.compare_digest(expected, received_signature)


def _event_data_key(payload: dict) -> str | None:
    """Return the dynamic openedx-events data key (non-metadata top-level key)."""
    for key in payload:
        if key != "event_metadata":
            return key
    return None


def get_nested_value(root, dotted_path: str) -> Any:
    """Read a value using dot notation from an already-resolved root dict."""
    if root is None or not dotted_path:
        return root if dotted_path == "" else None

    current = root
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def set_nested_value(root: dict, dotted_path: str, value: Any) -> None:
    """Set a value using dot notation, creating intermediate dicts."""
    if not dotted_path:
        return

    segments = dotted_path.split(".")
    current = root
    for segment in segments[:-1]:
        if segment not in current or not isinstance(current[segment], dict):
            current[segment] = {}
        current = current[segment]
    current[segments[-1]] = value


def delete_nested_path(root, dotted_path: str) -> None:
    """Remove a nested key if present."""
    if root is None or not dotted_path:
        return

    segments = dotted_path.split(".")
    current = root
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return
        current = current[segment]
    if isinstance(current, dict):
        current.pop(segments[-1], None)


def apply_allowlist(payload: dict, allowlist: list) -> dict:
    # One branch per supported path form; splitting them up would scatter the
    # allowlist semantics across helpers without simplifying them.
    # pylint: disable=too-many-branches
    """
    Keep only configured paths in the outbound payload.

    Each ``path`` in ``allowlist`` is one of:

    * ``"event_metadata"`` — keep the whole ``event_metadata`` object.
    * ``"event_metadata.<sub.path>"`` — keep just that nested field.
    * ``"data"`` — keep the whole event data object (aliases the dynamic
      openedx-events data key, e.g. ``openedx_events.learning...Data``).
    * ``"data.<sub.path>"`` — keep just that nested field of the data object.
    * any other literal top-level payload key, optionally with a nested
      ``.<sub.path>`` — useful if callers pass the real (long, dotted)
      openedx-events key directly instead of the ``data`` alias.

    Unknown/missing paths are silently skipped rather than raising, since
    allowlists are admin-configured and a typo shouldn't break delivery of
    the fields that *do* match.
    """
    if not allowlist:
        return payload

    result: dict = {}
    data_key = _event_data_key(payload)

    for path in allowlist:
        if path == "event_metadata":
            if "event_metadata" in payload:
                result["event_metadata"] = payload["event_metadata"]
            continue

        if path.startswith("event_metadata."):
            sub_path = path[len("event_metadata."):]
            value = get_nested_value(payload.get("event_metadata"), sub_path)
            if value is None:
                continue
            result.setdefault("event_metadata", {})
            set_nested_value(result["event_metadata"], sub_path, value)
            continue

        if path == "data":
            if data_key:
                result[data_key] = payload[data_key]
            continue

        if path.startswith("data."):
            if not data_key:
                continue
            sub_path = path[len("data."):]
            value = get_nested_value(payload.get(data_key), sub_path)
            if value is None:
                continue
            result.setdefault(data_key, {})
            set_nested_value(result[data_key], sub_path, value)
            continue

        if path in payload:
            result[path] = payload[path]
            continue

        for key in payload:
            prefix = f"{key}."
            if not path.startswith(prefix):
                continue
            sub_path = path[len(prefix):]
            value = get_nested_value(payload.get(key), sub_path)
            if value is None:
                break
            result.setdefault(key, {})
            set_nested_value(result[key], sub_path, value)
            break

    return result


def apply_denylist(payload: dict, denylist: list) -> None:
    """
    Remove configured paths from the payload (mutates in place).

    See ``apply_allowlist`` for the path syntax.
    """
    data_key = _event_data_key(payload)

    for path in denylist:
        if path == "event_metadata":
            payload.pop("event_metadata", None)
            continue

        if path.startswith("event_metadata."):
            delete_nested_path(payload.get("event_metadata"), path[len("event_metadata."):])
            continue

        if path == "data":
            if data_key:
                payload.pop(data_key, None)
            continue

        if path.startswith("data."):
            if data_key:
                delete_nested_path(payload.get(data_key), path[len("data."):])
            continue

        if path in payload:
            payload.pop(path, None)
            continue

        for key in list(payload.keys()):
            prefix = f"{key}."
            if path.startswith(prefix):
                delete_nested_path(payload.get(key), path[len(prefix):])
                break


def should_send_passing_event(payload: dict, only_on_passing: bool) -> bool:
    """Return False when only_on_passing is set and is_passing is not true."""
    if not only_on_passing:
        return True

    for value in payload.values():
        if not isinstance(value, dict):
            continue
        is_passing = value.get("is_passing")
        if is_passing is not None:
            return bool(is_passing)

    return True


def serialize_payload(payload: dict) -> bytes:
    """Deterministic JSON bytes, suitable for signing and for hashing in audit records."""
    return json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_fingerprint(payload: dict) -> str:
    """
    Short, non-reversible fingerprint of a payload for audit logs.

    We deliberately do NOT store the full payload in WebhookDeliveryAttempt —
    it may contain PII. The fingerprint lets operators confirm "the same
    payload was retried" without retaining PII in the audit trail.
    """
    return hashlib.sha256(serialize_payload(payload)).hexdigest()[:16]
