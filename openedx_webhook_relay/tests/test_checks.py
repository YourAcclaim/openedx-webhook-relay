"""
Tests for the deploy-time configuration checks.

These exist so a missing or malformed encryption key fails at ``manage.py
check`` rather than as a 500 the first time somebody saves an endpoint.
"""

# pylint: disable=missing-function-docstring

from cryptography.fernet import Fernet
from django.core.checks import Error
from django.core.checks import Warning as CheckWarning

from openedx_webhook_relay.checks import check_encryption_key


def _ids(messages):
    return [m.id for m in messages]


def test_valid_key_produces_no_issues(settings):
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")

    assert not check_encryption_key(None)


def test_missing_key_is_an_error_on_the_database_backend(settings):
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = ""
    settings.OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "database"

    messages = check_encryption_key(None)
    assert _ids(messages) == ["openedx_webhook_relay.E001"]
    assert isinstance(messages[0], Error)
    # The hint has to be actionable — it is the whole point of the check.
    assert "openssl rand" in messages[0].hint


def test_missing_key_is_only_a_warning_on_an_external_backend(settings):
    """Secrets live elsewhere, but local rotation still needs the key."""
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = ""
    settings.OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "aws_secrets_manager"

    messages = check_encryption_key(None)
    assert _ids(messages) == ["openedx_webhook_relay.W001"]
    assert isinstance(messages[0], CheckWarning)


def test_unset_key_is_treated_as_missing(settings):
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = None

    assert _ids(check_encryption_key(None)) == ["openedx_webhook_relay.E001"]


def test_malformed_key_is_an_error(settings):
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "not-a-fernet-key"

    messages = check_encryption_key(None)
    assert _ids(messages) == ["openedx_webhook_relay.E002"]
    assert isinstance(messages[0], Error)


def test_truncated_key_is_an_error(settings):
    """A key clipped by a copy/paste is the realistic malformed case."""
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")[:20]

    assert _ids(check_encryption_key(None)) == ["openedx_webhook_relay.E002"]


def test_key_as_bytes_is_accepted(settings):
    """Fernet accepts bytes, so the check must not reject them."""
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = Fernet.generate_key()

    assert not check_encryption_key(None)
