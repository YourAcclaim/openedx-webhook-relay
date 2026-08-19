"""
Tests for the rotate_encryption_key management command.

Keys are passed via ``--old-key-file`` / ``--new-key-file`` (the preferred
form) or as a single ``--old-key=<key>`` argv token. They are never passed as
``call_command("...", old_key=key)`` keyword options: for a required argument
Django re-parses the value as a separate argv token, and Fernet keys are
urlsafe base64 — about 1.5% of them begin with ``-``, which argparse then
reads as the start of another option. That made the old tests fail roughly
one run in sixteen.
"""

# pylint: disable=missing-function-docstring

from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from openedx_webhook_relay.models import WebhookEndpoint
from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

pytestmark = pytest.mark.django_db


def _key_file(tmp_path, name, key):
    """Write ``key`` to a file and return its path as a string."""
    path = tmp_path / name
    path.write_text(key, encoding="utf-8")
    return str(path)


def _leading_dash_key():
    """A valid Fernet key whose base64 text starts with '-'."""
    while True:
        key = Fernet.generate_key().decode("utf-8")
        if key.startswith("-"):
            return key


def test_rotate_encryption_key_reencrypts_all_secrets(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")

    endpoint = WebhookEndpointFactory(
        signing_secret="current-secret", signing_secret_previous="previous-secret"
    )

    out = StringIO()
    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
        f"--new-key-file={_key_file(tmp_path, 'new.key', new_key)}",
        stdout=out,
    )

    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
        rotated = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert rotated.signing_secret == "current-secret"
        assert rotated.signing_secret_previous == "previous-secret"

    # Old key can no longer decrypt (EncryptedCharField swallows InvalidToken -> "").
    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=old_key):
        stale = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert stale.signing_secret == ""


def test_rotate_encryption_key_dry_run_changes_nothing(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")
    endpoint = WebhookEndpointFactory(signing_secret="current-secret")

    out = StringIO()
    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
        f"--new-key-file={_key_file(tmp_path, 'new.key', new_key)}",
        "--dry-run",
        stdout=out,
    )

    assert "Dry run" in out.getvalue()
    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "current-secret"  # still readable under the OLD key


def test_rotate_encryption_key_rejects_invalid_key_format(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    with pytest.raises(CommandError, match="not a valid Fernet key"):
        call_command(
            "rotate_encryption_key",
            f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
            f"--new-key-file={_key_file(tmp_path, 'new.key', 'not-a-valid-key')}",
            stdout=StringIO(),
        )


def test_rotate_encryption_key_with_no_secrets_is_a_no_op(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")
    WebhookEndpointFactory(signing_secret="")

    out = StringIO()
    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
        f"--new-key-file={_key_file(tmp_path, 'new.key', new_key)}",
        stdout=out,
    )
    assert "0 endpoint(s)" in out.getvalue()


def test_key_file_accepts_a_key_starting_with_a_dash(settings, tmp_path):
    """Regression: a leading-dash key is fine in a file, where argparse never sees it."""
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = _leading_dash_key()
    endpoint = WebhookEndpointFactory(signing_secret="current-secret")

    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
        f"--new-key-file={_key_file(tmp_path, 'new.key', new_key)}",
        stdout=StringIO(),
    )

    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
        assert WebhookEndpoint.objects.get(pk=endpoint.pk).signing_secret == "current-secret"


def test_inline_key_option_accepts_a_key_starting_with_a_dash(settings, tmp_path):
    """The ``--new-key=<key>`` form must also tolerate a leading dash."""
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = _leading_dash_key()
    endpoint = WebhookEndpointFactory(signing_secret="current-secret")

    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
        f"--new-key={new_key}",
        stdout=StringIO(),
    )

    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
        assert WebhookEndpoint.objects.get(pk=endpoint.pk).signing_secret == "current-secret"


def test_key_file_and_inline_key_are_mutually_exclusive(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    with pytest.raises(CommandError, match="not allowed with"):
        call_command(
            "rotate_encryption_key",
            f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
            f"--old-key={old_key}",
            f"--new-key-file={_key_file(tmp_path, 'new.key', old_key)}",
            stdout=StringIO(),
        )


def test_missing_key_file_reports_a_clear_error(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    with pytest.raises(CommandError, match="Could not read --new-key-file"):
        call_command(
            "rotate_encryption_key",
            f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
            f"--new-key-file={tmp_path / 'does-not-exist.key'}",
            stdout=StringIO(),
        )


def test_empty_key_file_reports_a_clear_error(settings, tmp_path):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    # Assigned out here: a backslash inside an f-string expression is 3.12+.
    whitespace_only = "   \n"
    with pytest.raises(CommandError, match="is empty"):
        call_command(
            "rotate_encryption_key",
            f"--old-key-file={_key_file(tmp_path, 'old.key', old_key)}",
            f"--new-key-file={_key_file(tmp_path, 'new.key', whitespace_only)}",
            stdout=StringIO(),
        )


def test_key_file_contents_are_stripped(settings, tmp_path):
    """A trailing newline from `echo`/editors must not corrupt the key."""
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")
    endpoint = WebhookEndpointFactory(signing_secret="current-secret")

    call_command(
        "rotate_encryption_key",
        f"--old-key-file={_key_file(tmp_path, 'old.key', old_key + chr(10))}",
        f"--new-key-file={_key_file(tmp_path, 'new.key', new_key + chr(10))}",
        stdout=StringIO(),
    )

    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
        assert WebhookEndpoint.objects.get(pk=endpoint.pk).signing_secret == "current-secret"


def test_missing_both_forms_is_rejected():
    with pytest.raises(CommandError):
        call_command("rotate_encryption_key", stdout=StringIO())
