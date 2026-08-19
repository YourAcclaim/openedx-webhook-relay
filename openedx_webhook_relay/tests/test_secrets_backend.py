"""Tests for the pluggable secret backend."""

# pylint: disable=invalid-name,missing-function-docstring

import sys
import types

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from openedx_webhook_relay.secrets_backend import (
    AWSSecretsManagerBackend,
    DatabaseSecretBackend,
    get_secret_backend,
)
from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

pytestmark = pytest.mark.django_db


def test_get_secret_backend_defaults_to_database():
    assert isinstance(get_secret_backend(), DatabaseSecretBackend)


@override_settings(OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND="not-a-real-backend")
def test_get_secret_backend_rejects_unknown_name():
    with pytest.raises(ImproperlyConfigured):
        get_secret_backend()


def test_database_backend_get_and_set():
    endpoint = WebhookEndpointFactory(signing_secret="original")
    backend = DatabaseSecretBackend()

    assert backend.get_secret(endpoint) == "original"

    backend.set_secret(endpoint, "updated")
    assert endpoint.signing_secret == "updated"


def test_database_backend_get_secret_blank_when_unset():
    endpoint = WebhookEndpointFactory(signing_secret="")
    assert DatabaseSecretBackend().get_secret(endpoint) == ""


class _FakeClientError(Exception):
    pass


def _install_fake_boto3(monkeypatch):
    """
    Install a minimal fake `boto3` module so AWSSecretsManagerBackend can be
    exercised without the real dependency (which this plugin treats as
    optional and doesn't require in requirements/base.in).
    """
    store = {}

    class _FakeSecretsManagerClient:
        exceptions = types.SimpleNamespace(ResourceNotFoundException=_FakeClientError)

        def get_secret_value(self, SecretId):  # noqa: N803 - matches boto3's casing
            if SecretId not in store:
                raise _FakeClientError("not found")
            return {"SecretString": store[SecretId]}

        def put_secret_value(self, SecretId, SecretString):  # noqa: N803
            if SecretId not in store:
                raise _FakeClientError("not found")
            store[SecretId] = SecretString

        def create_secret(self, Name, SecretString):  # noqa: N803
            store[Name] = SecretString

        def delete_secret(self, SecretId):  # noqa: N803
            if SecretId not in store:
                raise _FakeClientError("not found")
            del store[SecretId]

    fake_boto3 = types.SimpleNamespace(client=lambda *a, **kw: _FakeSecretsManagerClient())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return store


@override_settings(OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND="aws_secrets_manager")
def test_aws_backend_set_then_get(monkeypatch):
    _install_fake_boto3(monkeypatch)
    endpoint = WebhookEndpointFactory(signing_secret="")  # unused for this backend
    backend = get_secret_backend()
    assert isinstance(backend, AWSSecretsManagerBackend)

    backend.set_secret(endpoint, "aws-stored-secret")
    assert backend.get_secret(endpoint) == "aws-stored-secret"


@override_settings(OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND="aws_secrets_manager")
def test_aws_backend_get_missing_returns_blank(monkeypatch):
    _install_fake_boto3(monkeypatch)
    endpoint = WebhookEndpointFactory()
    backend = get_secret_backend()
    assert backend.get_secret(endpoint) == ""


@override_settings(OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND="aws_secrets_manager")
def test_aws_backend_clear_deletes_secret(monkeypatch):
    _install_fake_boto3(monkeypatch)
    endpoint = WebhookEndpointFactory()
    backend = get_secret_backend()
    backend.set_secret(endpoint, "to-be-cleared")
    assert backend.get_secret(endpoint) == "to-be-cleared"

    backend.set_secret(endpoint, "")
    assert backend.get_secret(endpoint) == ""


@override_settings(OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND="aws_secrets_manager")
def test_aws_backend_requires_boto3(monkeypatch):
    # Simulate boto3 not being installed, regardless of whether it actually
    # is in this environment: make the import fail.
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(ImproperlyConfigured):
        get_secret_backend()
