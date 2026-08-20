"""
Deploy-time validation of this plugin's configuration.

Without these, a missing or malformed ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY``
survives startup and only surfaces later — as a 500 in Django admin when
saving an endpoint, or as a crashed delivery task. Both happen long after the
deploy that caused them, in a process nobody is watching.

Registered from ``apps.OpenedxWebhookRelayConfig.ready``, so they run with
``manage.py check`` (and therefore with ``migrate``, ``runserver`` and most
other management commands).
"""

import base64

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.checks import Error
from django.core.checks import Warning as CheckWarning

KEY_SETTING = "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY"

#: Deployments that store no signing secrets locally do not need the key, so a
#: missing key is only an error for the backend that encrypts them in the DB.
_LOCAL_BACKEND = "database"


def check_encryption_key(app_configs, **kwargs):  # pylint: disable=unused-argument
    """Validate that the encryption key is present and a usable Fernet key."""
    errors = []
    key = getattr(settings, KEY_SETTING, None)
    backend = getattr(settings, "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND", _LOCAL_BACKEND)

    if not key:
        message = f"{KEY_SETTING} is not set."
        hint = (
            "Signing secrets are encrypted at rest with this key, so storing or "
            "reading one will fail without it. Generate a key with "
            "`openssl rand -base64 32 | tr '+/' '-_'` and set it from your "
            "deployment's secrets store. Rotating it later requires the "
            "rotate_encryption_key command, not an edit."
        )
        # Only fatal where secrets actually live in this database.
        if backend == _LOCAL_BACKEND:
            errors.append(Error(message, hint=hint, id="openedx_webhook_relay.E001"))
        else:
            errors.append(
                CheckWarning(
                    f"{message} Not required by the "
                    f"{backend!r} secret backend, but rotation stores the previous "
                    "signing secret locally and will fail.",
                    hint=hint,
                    id="openedx_webhook_relay.W001",
                )
            )
        return errors

    try:
        Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError, base64.binascii.Error):
        errors.append(
            Error(
                f"{KEY_SETTING} is set but is not a valid Fernet key.",
                hint=(
                    "A Fernet key is 32 random bytes, urlsafe-base64 encoded — 44 "
                    "characters ending in '='. Check for a truncated or "
                    "whitespace-padded value."
                ),
                id="openedx_webhook_relay.E002",
            )
        )
    return errors
