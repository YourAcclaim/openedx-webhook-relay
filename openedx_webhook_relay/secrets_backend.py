"""
Pluggable secret storage backends for ``WebhookEndpoint`` signing secrets.

By default, secrets live in the plugin's own database, encrypted with
``fields.EncryptedCharField`` (see docs/decisions/0003-secret-storage.rst).
That's the right default for a self-contained Open edX plugin, but some
deployments already run a dedicated secrets manager (AWS Secrets Manager,
Vault, ...) and would rather this plugin not be another place secrets are
stored. This module makes that swappable.

Configure with::

    OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "database"              # default
    OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "aws_secrets_manager"

The backend interface is intentionally tiny: ``get_secret``/``set_secret``
per endpoint. Endpoint identity for the external store is
``WebhookEndpoint.secret_backend_reference`` — a stable UUID assigned at
creation — rather than the primary key, so the external reference doesn't
depend on save ordering (a new, unsaved instance already has a reference it
can use to name/look up the external secret).
"""

import abc
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class SecretBackend(abc.ABC):
    """Interface for reading/writing a WebhookEndpoint's signing secret."""

    @abc.abstractmethod
    def get_secret(self, endpoint) -> str:
        """Return the plaintext signing secret for ``endpoint``, or ''."""

    @abc.abstractmethod
    def set_secret(self, endpoint, plaintext: str) -> None:
        """Persist ``plaintext`` as the signing secret for ``endpoint``."""


class DatabaseSecretBackend(SecretBackend):
    """
    Default backend: secret lives in ``WebhookEndpoint.signing_secret``,
    encrypted at rest by ``fields.EncryptedCharField``. No extra network
    calls, no extra infrastructure dependency.
    """

    def get_secret(self, endpoint) -> str:
        return endpoint.signing_secret or ""

    def set_secret(self, endpoint, plaintext: str) -> None:
        endpoint.signing_secret = plaintext or ""


class AWSSecretsManagerBackend(SecretBackend):
    """
    Secret lives in AWS Secrets Manager; only a reference name is stored in
    the LMS database (``WebhookEndpoint.secret_backend_reference``).

    Requires ``boto3`` (not a hard dependency of this plugin — install it
    yourself if you opt into this backend) and AWS credentials available to
    the LMS process via the normal boto3 credential chain.
    """

    def __init__(self):
        try:
            import boto3  # pylint: disable=import-outside-toplevel
        except ImportError as exc:  # pragma: no cover - exercised via settings, not unit tests
            raise ImproperlyConfigured(
                "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND=aws_secrets_manager requires "
                "boto3. Install it (`pip install boto3`) or switch back to "
                "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND=database."
            ) from exc

        self._client = boto3.client(
            "secretsmanager",
            **getattr(settings, "OPENEDX_WEBHOOK_RELAY_AWS_SECRETS_MANAGER_KWARGS", {}),
        )

    def _secret_name(self, endpoint) -> str:
        prefix = getattr(
            settings, "OPENEDX_WEBHOOK_RELAY_AWS_SECRET_NAME_PREFIX", "openedx-webhook-relay"
        )
        if not endpoint.secret_backend_reference:
            raise ImproperlyConfigured(
                "WebhookEndpoint.secret_backend_reference is unset; it should be "
                "assigned a UUID by the model's default before secrets are read/written."
            )
        return f"{prefix}/{endpoint.secret_backend_reference}"

    def get_secret(self, endpoint) -> str:
        if not endpoint.secret_backend_reference:
            return ""
        try:
            response = self._client.get_secret_value(SecretId=self._secret_name(endpoint))
        except self._client.exceptions.ResourceNotFoundException:
            return ""
        return response.get("SecretString", "") or ""

    def set_secret(self, endpoint, plaintext: str) -> None:
        """
        Set or clear the secret.

        Callers (admin, management commands) only invoke this with an empty
        string to mean "explicitly clear" — "leave untouched" is handled by
        callers simply not calling this method at all. So an empty string
        here unambiguously means: delete the secret (soft delete, subject to
        AWS's normal recovery window).
        """
        name = self._secret_name(endpoint)
        if not plaintext:
            try:
                self._client.delete_secret(SecretId=name)
            except self._client.exceptions.ResourceNotFoundException:
                pass
            return
        try:
            self._client.put_secret_value(SecretId=name, SecretString=plaintext)
        except self._client.exceptions.ResourceNotFoundException:
            self._client.create_secret(Name=name, SecretString=plaintext)


_BACKENDS = {
    "database": DatabaseSecretBackend,
    "aws_secrets_manager": AWSSecretsManagerBackend,
}


def get_secret_backend() -> SecretBackend:
    """Return the configured SecretBackend instance (constructed fresh each call)."""
    backend_name = getattr(settings, "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND", "database")
    try:
        backend_cls = _BACKENDS[backend_name]
    except KeyError as exc:
        raise ImproperlyConfigured(
            f"Unknown OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND={backend_name!r}; "
            f"expected one of {sorted(_BACKENDS)}."
        ) from exc
    return backend_cls()
