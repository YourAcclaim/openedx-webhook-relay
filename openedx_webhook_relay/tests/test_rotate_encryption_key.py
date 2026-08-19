"""Tests for the rotate_encryption_key management command."""

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


def test_rotate_encryption_key_reencrypts_all_secrets(settings):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")

    endpoint = WebhookEndpointFactory(
        signing_secret="current-secret", signing_secret_previous="previous-secret"
    )

    out = StringIO()
    call_command("rotate_encryption_key", old_key=old_key, new_key=new_key, stdout=out)

    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
        rotated = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert rotated.signing_secret == "current-secret"
        assert rotated.signing_secret_previous == "previous-secret"

    # Old key can no longer decrypt (EncryptedCharField swallows InvalidToken -> "").
    with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=old_key):
        stale = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert stale.signing_secret == ""


def test_rotate_encryption_key_dry_run_changes_nothing(settings):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")
    endpoint = WebhookEndpointFactory(signing_secret="current-secret")

    out = StringIO()
    call_command(
        "rotate_encryption_key", old_key=old_key, new_key=new_key, dry_run=True, stdout=out
    )

    assert "Dry run" in out.getvalue()
    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "current-secret"  # still readable under the OLD key


def test_rotate_encryption_key_rejects_invalid_key_format(settings):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    with pytest.raises(CommandError):
        call_command(
            "rotate_encryption_key", old_key=old_key, new_key="not-a-valid-key", stdout=StringIO()
        )


def test_rotate_encryption_key_with_no_secrets_is_a_no_op(settings):
    old_key = settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode("utf-8")
    WebhookEndpointFactory(signing_secret="")

    out = StringIO()
    call_command("rotate_encryption_key", old_key=old_key, new_key=new_key, stdout=out)
    assert "0 endpoint(s)" in out.getvalue()
