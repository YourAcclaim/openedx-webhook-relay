"""
Custom model fields.

``EncryptedCharField`` stores its value encrypted at rest (Fernet /
AES-128-CBC + HMAC) so signing secrets are never persisted in plaintext in
the database and are never rendered in plaintext by Django admin.

Design notes (see docs/decisions/0003-secret-storage.rst):

* Encryption key comes from ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY``, a
  dedicated Fernet key — *not* derived from ``SECRET_KEY`` — so rotating the
  Django secret key does not lock out existing webhook secrets.
* Decryption only happens in Python (``from_db_value``); it is never done in
  SQL, so the plaintext never appears in query logs.
* ``ValueMasker`` gives admin/UI code a one-way masked representation
  (``"••••1234"``) without ever exposing the full secret back to the browser.
"""

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

logger = logging.getLogger(__name__)

_ENCRYPTED_PREFIX = "enc::"


class SecretConfigurationError(ImproperlyConfigured):
    """Raised when OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY is missing/invalid."""


def _get_fernet() -> Fernet:
    key = getattr(settings, "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY", None)
    if not key:
        raise SecretConfigurationError(
            "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY must be set to a valid "
            "Fernet key (generate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`) before storing or "
            "reading webhook signing secrets."
        )
    if isinstance(key, str):
        key = key.encode("utf-8")
    try:
        return Fernet(key)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise SecretConfigurationError(
            "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


def mask_secret(plaintext: str) -> str:
    """Return a display-safe masked form, e.g. 'super-secret' -> '••••cret'."""
    if not plaintext:
        return ""
    tail = plaintext[-4:] if len(plaintext) > 4 else plaintext[-1:]
    return f"{'•' * 8}{tail}"


class EncryptedCharField(models.CharField):
    """
    CharField that transparently encrypts on write and decrypts on read.

    Ciphertext is stored with a fixed prefix so plaintext legacy rows (from
    a pre-encryption migration) can be detected and reported, rather than
    silently mis-decrypted.
    """

    description = "Char field that is encrypted at rest"

    def __init__(self, *args, **kwargs):
        # Fernet ciphertext is base64 and considerably longer than the
        # plaintext; give ourselves generous headroom.
        kwargs.setdefault("max_length", 1024)
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value):
        """Encrypt ``value`` on its way to the database."""
        # pylint-django does not resolve CharField.get_prep_value through the
        # Field/CharField MRO, so it reports a false no-member here.
        # pylint: disable=no-member
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        if value.startswith(_ENCRYPTED_PREFIX):
            # Already encrypted (e.g. re-saving a loaded instance unchanged).
            return value
        token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        return f"{_ENCRYPTED_PREFIX}{token}"

    def from_db_value(self, value, expression, connection):  # pylint: disable=unused-argument
        """Decrypt ``value`` as it is loaded from the database."""
        if value in (None, ""):
            return value
        if not value.startswith(_ENCRYPTED_PREFIX):
            logger.warning(
                "EncryptedCharField encountered a plaintext value for %s; "
                "run the secret-encryption migration/backfill.",
                self.name,
            )
            return value
        token = value[len(_ENCRYPTED_PREFIX):]
        try:
            return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            logger.error(
                "Failed to decrypt %s: token invalid for the configured "
                "encryption key. Was OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY "
                "rotated without re-encrypting existing rows?",
                self.name,
            )
            return ""

    def to_python(self, value):
        return value
