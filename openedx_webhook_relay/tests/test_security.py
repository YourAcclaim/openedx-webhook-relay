"""Unit tests for security helpers (signing, PII filtering, fingerprinting)."""

# pylint: disable=missing-function-docstring

import json

from openedx_webhook_relay.security import (
    apply_allowlist,
    apply_denylist,
    payload_fingerprint,
    serialize_payload,
    should_send_passing_event,
    sign_payload,
    verify_signature,
)


def test_sign_and_verify_payload():
    body = b'{"event":"test"}'
    secret = "super-secret"
    signature = sign_payload(body, secret)

    assert signature.startswith("sha256=")
    assert verify_signature(body, secret, signature)
    assert not verify_signature(body, "wrong", signature)


def test_verify_signature_rejects_missing_signature():
    assert not verify_signature(b"body", "secret", "")
    assert not verify_signature(b"body", "secret", None)


def test_serialize_payload_is_deterministic():
    payload = {"b": 2, "a": {"z": 1, "y": 2}}
    first = serialize_payload(payload)
    second = serialize_payload(payload)
    assert first == second
    assert json.loads(first.decode()) == {"a": {"y": 2, "z": 1}, "b": 2}


def test_payload_fingerprint_is_stable_and_short():
    payload = {"a": 1}
    fp1 = payload_fingerprint(payload)
    fp2 = payload_fingerprint(dict(payload))
    assert fp1 == fp2
    assert len(fp1) == 16


def test_payload_fingerprint_changes_with_content():
    assert payload_fingerprint({"a": 1}) != payload_fingerprint({"a": 2})


def test_apply_allowlist():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {
        data_key: {
            "is_passing": True,
            "user": {"pii": {"email": "a@b.com", "name": "Alice"}},
        },
        "event_metadata": {"id": "evt-1"},
    }
    allowlist = ["data.is_passing", "data.user.pii.email", "event_metadata"]
    result = apply_allowlist(payload, allowlist)

    assert result["event_metadata"] == {"id": "evt-1"}
    data = result[data_key]
    assert data["is_passing"] is True
    assert data["user"]["pii"]["email"] == "a@b.com"
    assert "name" not in data["user"]["pii"]


def test_apply_allowlist_empty_returns_payload_unchanged():
    payload = {"data": {"a": 1}}
    assert apply_allowlist(payload, []) is payload


def test_apply_allowlist_skips_missing_paths():
    payload = {"data": {"a": 1}, "event_metadata": {"id": "evt-1"}}
    result = apply_allowlist(payload, ["data.does_not_exist", "event_metadata.id"])
    assert result == {"event_metadata": {"id": "evt-1"}}


def test_apply_denylist():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {
        data_key: {"user": {"pii": {"email": "a@b.com", "name": "Alice"}}},
        "event_metadata": {"id": "evt-1"},
    }
    apply_denylist(payload, ["data.user.pii.name"])
    assert payload[data_key]["user"]["pii"] == {"email": "a@b.com"}


def test_apply_denylist_top_level_key():
    payload = {"event_metadata": {"id": "evt-1"}, "extra": "drop-me"}
    apply_denylist(payload, ["extra"])
    assert "extra" not in payload


def test_should_send_passing_event():
    passing = {"openedx_events.learning.data.CoursePassingStatusData": {"is_passing": True}}
    failing = {"openedx_events.learning.data.CoursePassingStatusData": {"is_passing": False}}

    assert should_send_passing_event(passing, True)
    assert not should_send_passing_event(failing, True)
    assert should_send_passing_event(failing, False)


def test_should_send_passing_event_defaults_true_when_no_is_passing_key():
    payload = {"event_metadata": {"id": "evt-1"}}
    assert should_send_passing_event(payload, True)
