"""
Rotate OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY: decrypt every stored
signing secret with the old key and re-encrypt it with the new one.

See docs/decisions/0003-secret-storage.rst and 0009-key-rotation.rst.

Usage::

    ./manage.py rotate_encryption_key \\
        --old-key-file=old.key \\
        --new-key-file=new.key \\
        [--dry-run]

The keys are read from files rather than accepted as plain arguments so they
do not end up in shell history or process listings — matching
``rotate_signing_secret --new-secret-file``. The inline ``--old-key`` /
``--new-key`` forms are still accepted for backwards compatibility, but note
they must be written as ``--old-key=<key>``: Fernet keys are urlsafe base64
and roughly 1.5% of them begin with ``-``, which argparse would otherwise
read as the start of another option.

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

from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from openedx_webhook_relay.models import WebhookEndpoint

_KEY_SETTING = "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY"
_MISSING = object()


@contextmanager
def using_encryption_key(key):
    """
    Temporarily resolve ``_KEY_SETTING`` to ``key``.

    ``EncryptedCharField`` looks the key up per call (``fields._get_fernet``),
    so reassigning the setting is enough to control which key encrypts or
    decrypts a given row.

    Deliberately not ``django.test.utils.override_settings``: that is test
    scaffolding, and it broadcasts ``setting_changed`` to every installed app,
    which is an unwanted side effect in a command that rewrites production
    secrets.
    """
    previous = getattr(settings, _KEY_SETTING, _MISSING)
    setattr(settings, _KEY_SETTING, key)
    try:
        yield
    finally:
        if previous is _MISSING:
            delattr(settings, _KEY_SETTING)
        else:
            setattr(settings, _KEY_SETTING, previous)


class Command(BaseCommand):
    """Re-encrypt stored signing secrets under a new Fernet key."""

    help = "Re-encrypt all WebhookEndpoint signing secrets under a new encryption key."

    def add_arguments(self, parser):
        for name, described in (("old", "Current"), ("new", "New")):
            group = parser.add_mutually_exclusive_group(required=True)
            group.add_argument(
                f"--{name}-key-file",
                help=f"Path to a file containing the {described.lower()} Fernet key. "
                "Preferred: keeps the key out of shell history and process listings.",
            )
            group.add_argument(
                f"--{name}-key",
                help=f"{described} Fernet key (base64). Visible in shell history and `ps` "
                f"output, so prefer --{name}-key-file. Must be written as "
                f"--{name}-key=<key>, since Fernet keys may begin with '-'.",
            )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change without saving."
        )

    def _resolve_key(self, options, name):
        """
        Return the ``name`` ("old"/"new") key from its file, else the inline option.

        argparse guarantees exactly one of the pair is set.
        """
        path = options.get(f"{name}_key_file")
        if not path:
            return options[f"{name}_key"], f"--{name}-key"

        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CommandError(f"Could not read --{name}-key-file {path!r}: {exc}") from exc
        if not key:
            raise CommandError(f"--{name}-key-file {path!r} is empty.")
        return key, f"--{name}-key-file"

    def handle(self, *args, **options):
        old_key, old_label = self._resolve_key(options, "old")
        new_key, new_label = self._resolve_key(options, "new")
        dry_run = options["dry_run"]

        for label, key in ((old_label, old_key), (new_label, new_key)):
            try:
                Fernet(key.encode("utf-8") if isinstance(key, str) else key)
            except (ValueError, TypeError) as exc:
                raise CommandError(f"{label} is not a valid Fernet key.") from exc

        # Force the query to execute now, while OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
        # is overridden to the OLD key, so EncryptedCharField.from_db_value
        # decrypts every row's signing_secret with the right key up front.
        with using_encryption_key(old_key):
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
                f"{old_label}. Double check the key before proceeding — nothing was written."
            )

        self.stdout.write(f"{len(endpoints)} endpoint(s) have a secret to re-encrypt.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: no changes saved."))
            return

        with using_encryption_key(new_key):
            for endpoint in endpoints:
                endpoint.save(update_fields=["signing_secret", "signing_secret_previous"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Re-encrypted {len(endpoints)} endpoint(s). Now update "
                "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY to the new key in your deployment "
                "configuration and restart."
            )
        )
