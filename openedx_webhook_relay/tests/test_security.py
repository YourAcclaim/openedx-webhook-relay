"""Unit tests for security helpers (signing, PII filtering, fingerprinting)."""

# use-implicit-booleaness-not-comparison: these assertions compare against {}
# on purpose. "nothing was allowed through" is a stronger, more precise claim
# than a falsey check, which would also pass for None or 0.
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison

import json

from openedx_webhook_relay.security import (
    _delete_nested_path,
    _get_nested_value,
    _set_nested_value,
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


# --- allowlist: the `data` alias and literal dotted keys -------------------
#
# These branches decide which fields leave the system, so each one is asserted
# for what it *excludes* as well as what it keeps.


def test_allowlist_data_alias_keeps_whole_data_object():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {
        data_key: {"is_passing": True, "user": {"pii": {"email": "a@b.com"}}},
        "event_metadata": {"id": "evt-1"},
    }
    result = apply_allowlist(payload, ["data"])

    assert result[data_key] == payload[data_key]
    # "data" alone must not smuggle event_metadata through.
    assert "event_metadata" not in result


def test_allowlist_data_paths_are_dropped_when_payload_has_no_data_key():
    """A metadata-only payload has no data key; `data.*` must yield nothing."""
    payload = {"event_metadata": {"id": "evt-1"}}

    assert apply_allowlist(payload, ["data"]) == {}
    assert apply_allowlist(payload, ["data.is_passing"]) == {}


def test_allowlist_accepts_the_real_dotted_data_key_not_just_the_alias():
    """Callers may pass the long openedx-events key instead of `data`."""
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {
        data_key: {"is_passing": True, "user": {"pii": {"email": "a@b.com"}}},
        "event_metadata": {"id": "evt-1"},
    }

    whole = apply_allowlist(payload, [data_key])
    assert whole == {data_key: payload[data_key]}

    nested = apply_allowlist(payload, [f"{data_key}.user.pii.email"])
    assert nested == {data_key: {"user": {"pii": {"email": "a@b.com"}}}}
    assert "is_passing" not in nested[data_key]


def test_allowlist_literal_dotted_key_with_missing_leaf_keeps_nothing():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {data_key: {"is_passing": True}, "event_metadata": {"id": "evt-1"}}

    assert apply_allowlist(payload, [f"{data_key}.user.pii.email"]) == {}


def test_allowlist_metadata_subpath_with_missing_leaf_keeps_nothing():
    payload = {"event_metadata": {"id": "evt-1"}}

    assert apply_allowlist(payload, ["event_metadata.missing"]) == {}


def test_allowlist_unknown_top_level_path_is_ignored():
    payload = {"event_metadata": {"id": "evt-1"}}

    assert apply_allowlist(payload, ["nope", "nope.deeper"]) == {}


# --- denylist: every path form --------------------------------------------


def test_denylist_removes_whole_metadata_and_data_objects():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {data_key: {"is_passing": True}, "event_metadata": {"id": "evt-1"}}

    apply_denylist(payload, ["event_metadata", "data"])
    assert payload == {}


def test_denylist_removes_a_metadata_subpath():
    payload = {"event_metadata": {"id": "evt-1", "source": "lms"}}

    apply_denylist(payload, ["event_metadata.source"])
    assert payload == {"event_metadata": {"id": "evt-1"}}


def test_denylist_accepts_the_real_dotted_data_key():
    data_key = "openedx_events.learning.data.CoursePassingStatusData"
    payload = {
        data_key: {"is_passing": True, "user": {"pii": {"email": "a@b.com"}}},
        "event_metadata": {"id": "evt-1"},
    }

    apply_denylist(payload, [f"{data_key}.user.pii.email"])
    assert payload[data_key] == {"is_passing": True, "user": {"pii": {}}}


def test_denylist_data_paths_are_a_no_op_without_a_data_key():
    payload = {"event_metadata": {"id": "evt-1"}}

    apply_denylist(payload, ["data", "data.is_passing"])
    assert payload == {"event_metadata": {"id": "evt-1"}}


def test_denylist_unknown_paths_are_a_no_op():
    payload = {"event_metadata": {"id": "evt-1"}}

    apply_denylist(payload, ["missing", "missing.deeper", "event_metadata.absent"])
    assert payload == {"event_metadata": {"id": "evt-1"}}


def test_denylist_on_non_dict_branch_does_not_raise():
    """A scalar where a dict is expected must be skipped, not crash delivery."""
    payload = {"event_metadata": {"id": "evt-1"}, "scalar": "value"}

    apply_denylist(payload, ["scalar.nested.deeper"])
    assert payload["scalar"] == "value"


# --- only_on_passing edge cases -------------------------------------------


def test_should_send_passing_event_skips_non_dict_values():
    """A scalar top-level value must not shadow the real data object."""
    payload = {
        "correlation_id": "abc-123",
        "openedx_events.learning.data.CoursePassingStatusData": {"is_passing": False},
    }

    assert not should_send_passing_event(payload, True)


# --- path-helper guard clauses --------------------------------------------


def test_get_nested_value_guards():
    root = {"a": {"b": 1}}

    assert _get_nested_value(root, "") is root       # empty path returns the root
    assert _get_nested_value(None, "a") is None      # missing root
    assert _get_nested_value(root, "a.missing") is None
    assert _get_nested_value(root, "a.b.too.deep") is None  # descends past a scalar


def test_set_nested_value_ignores_an_empty_path():
    root = {"a": 1}

    _set_nested_value(root, "", "ignored")
    assert root == {"a": 1}


def test_set_nested_value_replaces_a_scalar_with_a_dict():
    root = {"a": "scalar"}

    _set_nested_value(root, "a.b", 2)
    assert root == {"a": {"b": 2}}


def test_delete_nested_path_guards():
    root = {"a": {"b": 1}}

    _delete_nested_path(None, "a")        # no root
    _delete_nested_path(root, "")         # no path
    _delete_nested_path(root, "x.y")      # missing intermediate
    assert root == {"a": {"b": 1}}
