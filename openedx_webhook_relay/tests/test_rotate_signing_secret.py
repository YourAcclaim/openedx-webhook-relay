"""Tests for the rotate_signing_secret management command."""

# pylint: disable=missing-function-docstring

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

pytestmark = pytest.mark.django_db


def test_rotate_with_new_secret_file_keeps_previous(tmp_path):
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    secret_file = tmp_path / "new-secret.txt"
    secret_file.write_text("brand-new-secret\n")

    out = StringIO()
    call_command(
        "rotate_signing_secret",
        endpoint_id=endpoint.pk,
        new_secret_file=str(secret_file),
        stdout=out,
    )

    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "brand-new-secret"
    assert endpoint.signing_secret_previous == "old-secret"


def test_rotate_with_no_keep_previous_discards_old_secret(tmp_path):
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    secret_file = tmp_path / "new-secret.txt"
    secret_file.write_text("brand-new-secret")

    call_command(
        "rotate_signing_secret",
        endpoint_id=endpoint.pk,
        new_secret_file=str(secret_file),
        no_keep_previous=True,
        stdout=StringIO(),
    )

    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "brand-new-secret"
    assert endpoint.signing_secret_previous == ""


def test_clear_previous_flag():
    endpoint = WebhookEndpointFactory(signing_secret="current", signing_secret_previous="stale")

    call_command(
        "rotate_signing_secret", endpoint_id=endpoint.pk, clear_previous=True, stdout=StringIO()
    )

    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "current"
    assert endpoint.signing_secret_previous == ""


def test_requires_at_least_one_action():
    endpoint = WebhookEndpointFactory()
    with pytest.raises(CommandError):
        call_command("rotate_signing_secret", endpoint_id=endpoint.pk, stdout=StringIO())


def test_unknown_endpoint_raises():
    with pytest.raises(CommandError):
        call_command(
            "rotate_signing_secret", endpoint_id=999999, clear_previous=True, stdout=StringIO()
        )


def test_empty_secret_file_raises(tmp_path):
    endpoint = WebhookEndpointFactory()
    secret_file = tmp_path / "empty.txt"
    secret_file.write_text("   \n")

    with pytest.raises(CommandError):
        call_command(
            "rotate_signing_secret",
            endpoint_id=endpoint.pk,
            new_secret_file=str(secret_file),
            stdout=StringIO(),
        )


class _NonDatabaseBackend:
    """Stand-in for any backend that stores secrets outside this database."""

    def __init__(self):
        self.written = None

    def get_secret(self, endpoint):  # pylint: disable=unused-argument
        return "old-secret"

    def set_secret(self, endpoint, plaintext):  # pylint: disable=unused-argument
        self.written = plaintext


def test_keep_previous_is_refused_on_a_non_database_backend(tmp_path, monkeypatch):
    """
    Silently ignoring keep-previous used to print success while doing a hard
    cutover, so an operator believed they had a dual-signature window.
    """
    backend = _NonDatabaseBackend()
    monkeypatch.setattr(
        "openedx_webhook_relay.management.commands.rotate_signing_secret.get_secret_backend",
        lambda: backend,
    )
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    secret_file = tmp_path / "new-secret.txt"
    secret_file.write_text("brand-new-secret\n")

    with pytest.raises(CommandError, match="only supported by the 'database' secret backend"):
        call_command(
            "rotate_signing_secret",
            endpoint_id=endpoint.pk,
            new_secret_file=str(secret_file),
            stdout=StringIO(),
        )

    # Refused before writing anything.
    assert backend.written is None


def test_no_keep_previous_is_allowed_on_a_non_database_backend(tmp_path, monkeypatch):
    """Opting into the hard cutover explicitly must still work."""
    backend = _NonDatabaseBackend()
    monkeypatch.setattr(
        "openedx_webhook_relay.management.commands.rotate_signing_secret.get_secret_backend",
        lambda: backend,
    )
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    secret_file = tmp_path / "new-secret.txt"
    secret_file.write_text("brand-new-secret\n")

    call_command(
        "rotate_signing_secret",
        endpoint_id=endpoint.pk,
        new_secret_file=str(secret_file),
        no_keep_previous=True,
        stdout=StringIO(),
    )

    assert backend.written == "brand-new-secret"
