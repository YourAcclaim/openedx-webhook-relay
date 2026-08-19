"""
Rotate a single endpoint's signing secret from the command line (the CLI
counterpart to the admin "rotate" checkboxes) — useful for scripted/CI-driven
rotations that shouldn't require clicking through admin.

See docs/decisions/0005-signing-secret-rotation.rst.

Usage::

    # Move the current secret to "previous" and set a new one. Outbound
    # requests will carry both signatures until you clear the previous one.
    ./manage.py rotate_signing_secret --endpoint-id=3 --new-secret-file=/path/to/new-secret.txt

    # Once the receiver has confirmed it's using the new secret:
    ./manage.py rotate_signing_secret --endpoint-id=3 --clear-previous

The new secret is read from a file rather than accepted as a plain argument
so it doesn't end up in shell history or process listings.
"""

from django.core.management.base import BaseCommand, CommandError

from openedx_webhook_relay.models import WebhookEndpoint
from openedx_webhook_relay.secrets_backend import DatabaseSecretBackend, get_secret_backend


class Command(BaseCommand):
    help = "Rotate (or finish rotating) a WebhookEndpoint's signing secret."

    def add_arguments(self, parser):
        parser.add_argument("--endpoint-id", type=int, required=True)
        parser.add_argument(
            "--new-secret-file",
            help="Path to a file containing the new plaintext secret (no trailing newline needed).",
        )
        parser.add_argument(
            "--clear-previous",
            action="store_true",
            help="Stop sending the previous secret's signature; use once the receiver has cut over.",
        )
        parser.add_argument(
            "--no-keep-previous",
            action="store_true",
            help="When setting a new secret, discard the old one immediately instead of keeping "
            "it active for a rotation window.",
        )

    def handle(self, *args, **options):
        try:
            endpoint = WebhookEndpoint.objects.get(pk=options["endpoint_id"])
        except WebhookEndpoint.DoesNotExist as exc:
            raise CommandError(f"No WebhookEndpoint with id={options['endpoint_id']}.") from exc

        new_secret_file = options.get("new_secret_file")
        clear_previous = options.get("clear_previous")

        if not new_secret_file and not clear_previous:
            raise CommandError("Pass --new-secret-file, --clear-previous, or both.")

        backend = get_secret_backend()

        if new_secret_file:
            with open(new_secret_file, encoding="utf-8") as handle:
                new_secret = handle.read().strip()
            if not new_secret:
                raise CommandError(f"{new_secret_file} is empty.")

            if not options["no_keep_previous"] and isinstance(backend, DatabaseSecretBackend):
                old_secret = backend.get_secret(endpoint)
                if old_secret:
                    endpoint.signing_secret_previous = old_secret

            backend.set_secret(endpoint, new_secret)
            self.stdout.write(self.style.SUCCESS(f"Set new signing secret for endpoint {endpoint.pk}."))

        if clear_previous:
            endpoint.signing_secret_previous = ""
            self.stdout.write(self.style.SUCCESS(f"Cleared previous signing secret for endpoint {endpoint.pk}."))

        endpoint.save()
