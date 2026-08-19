"""
Purge old WebhookDeliveryAttempt rows.

The audit trail (docs/decisions/0004-delivery-audit-trail.rst) grows
unbounded by design — every attempt is recorded. That's the point for
recent history, but old rows eventually need a retention policy. This
command deletes attempts older than a cutoff, defaulting to
``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS`` (90 days).

Usage::

    ./manage.py purge_old_delivery_attempts                 # dry run, uses configured retention
    ./manage.py purge_old_delivery_attempts --days=30 --yes  # actually delete, 30-day window
    ./manage.py purge_old_delivery_attempts --status=succeeded --yes

As of docs/decisions/0010-scheduled-retention-purge.rst, this is also run
automatically on a schedule via Celery beat (``tasks.purge_old_delivery_attempts_task``),
so most deployments won't need to run this by hand. It remains available for
one-off cleanups, non-default retention windows, or deployments that set
``OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False`` to manage their own cron
instead.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.retention import purge_old_delivery_attempts


class Command(BaseCommand):
    """Delete delivery-attempt audit rows past the retention window."""

    help = "Delete WebhookDeliveryAttempt rows older than a retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Delete attempts older than this many days. Defaults to "
                "OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS (currently "
                f"{getattr(settings, 'OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS', 90)})."
            ),
        )
        parser.add_argument(
            "--status",
            choices=[c.value for c in WebhookDeliveryAttempt.Status],
            help="Only purge attempts with this status (default: all statuses).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually delete. Without this flag, only reports the count.",
        )

    def handle(self, *args, **options):
        result = purge_old_delivery_attempts(
            days=options["days"],
            status=options.get("status"),
            dry_run=not options["yes"],
        )

        if not result.matched:
            self.stdout.write(
                self.style.SUCCESS(f"No delivery attempts older than {result.days} days.")
            )
            return

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Would delete {result.matched} delivery attempt(s) older than "
                    f"{result.days} days (cutoff: {result.cutoff.isoformat()}). "
                    "Pass --yes to actually delete."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Deleted {result.deleted} delivery attempt(s)."))
