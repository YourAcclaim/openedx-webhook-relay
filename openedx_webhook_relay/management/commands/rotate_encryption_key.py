"""
Rotate OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY: decrypt every stored
signing secret with the old key and re-encrypt it with the new one.

See docs/decisions/0003-secret-storage.rst and 0009-key-rotation.rst.

Usage::

    ./manage.py rotate_encryption_key \\
        --old-key="$(cat old.key)" \\
        --new-key="$(cat new.key)" \\
        [--dry-run]

Then update the deployment's OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY setting
to the new key and restart. Doing it in this order (rewrite rows, then
flip the setting) means a crash mid-rotation leaves rows readable by
*either* the old key (if the command didn't reach them yet) or the new key
(if it did) — never in a half-written, unreadable state, because each row
is fully re-encrypted in a single ``save()``.

Only applies to the "database" secret backend (fields.EncryptedCharField).
If OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND is set to an external backend,
secrets live outside this database and are unaffected by this command —
rotate them via that backend's own tooling.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from openedx_webhook_relay.models import WebhookEndpoint


class Command(BaseCommand):
    """Re-encrypt stored signing secrets under a new Fernet key."""

    help = "Re-encrypt all WebhookEndpoint signing secrets under a new encryption key."

    def add_arguments(self, parser):
        parser.add_argument("--old-key", required=True, help="Current Fernet key (base64).")
        parser.add_argument(
            "--new-key", required=True, help="New Fernet key (base64) to rotate to."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change without saving."
        )

    def handle(self, *args, **options):
        old_key = options["old_key"]
        new_key = options["new_key"]
        dry_run = options["dry_run"]

        for label, key in (("--old-key", old_key), ("--new-key", new_key)):
            try:
                Fernet(key.encode("utf-8") if isinstance(key, str) else key)
            except (ValueError, TypeError) as exc:
                raise CommandError(f"{label} is not a valid Fernet key.") from exc

        # Force the query to execute now, while OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
        # is overridden to the OLD key, so EncryptedCharField.from_db_value
        # decrypts every row's signing_secret with the right key up front.
        with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=old_key):
            # Any endpoint with a current or previous secret needs rewriting.
            endpoints = [
                e for e in WebhookEndpoint.objects.all()
                if e.signing_secret or e.signing_secret_previous
            ]
            decrypt_errors = []
            for endpoint in endpoints:
                try:
                    _ = endpoint.signing_secret, endpoint.signing_secret_previous
                except InvalidToken:  # pragma: no cover - from_db_value already swallows this
                    decrypt_errors.append(endpoint.pk)

        if decrypt_errors:
            raise CommandError(
                f"Failed to decrypt secrets for endpoint id(s) {decrypt_errors} with "
                "--old-key. Double check the key before proceeding — nothing was written."
            )

        self.stdout.write(f"{len(endpoints)} endpoint(s) have a secret to re-encrypt.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: no changes saved."))
            return

        with override_settings(OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY=new_key):
            for endpoint in endpoints:
                endpoint.save(update_fields=["signing_secret", "signing_secret_previous"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Re-encrypted {len(endpoints)} endpoint(s). Now update "
                "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY to --new-key in your deployment "
                "configuration and restart."
            )
        )
