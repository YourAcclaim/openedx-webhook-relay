"""
Manually redeliver exhausted webhook deliveries.

This is the operator-facing half of the dead-letter mechanism described in
docs/decisions/0002-async-delivery-and-retries.rst: WebhookDeliveryAttempt
rows with status EXHAUSTED are never auto-retried again (to avoid hammering
a permanently-broken receiver), but can be requeued on demand once the
underlying issue (e.g. receiver downtime, bad URL) is fixed.

By default the full payload is not stored (to avoid retaining PII in the
audit trail — see docs/decisions/0004-delivery-audit-trail.rst), so
redelivery normally requires the payload supplied out-of-band via
--payload-file. If the endpoint opted into
``retain_payload_snapshot=True`` (docs/decisions/0006-admin-bulk-redeliver.rst),
EXHAUSTED attempts carry their own ``payload_snapshot`` and --payload-file
can be omitted — matched attempts are redelivered using their own stored
snapshot (a mix of snapshotted and non-snapshotted attempts is fine: pass
--payload-file to cover the ones without a snapshot, or narrow the
selection with --correlation-id/--endpoint-id).
"""

import json
import sys

from django.core.management.base import BaseCommand, CommandError

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.tasks import deliver_webhook


class Command(BaseCommand):
    help = "List or requeue EXHAUSTED webhook delivery attempts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--correlation-id",
            help="Only operate on attempts with this correlation ID.",
        )
        parser.add_argument(
            "--endpoint-id",
            type=int,
            help="Only operate on attempts for this WebhookEndpoint ID.",
        )
        parser.add_argument(
            "--requeue",
            action="store_true",
            help="Actually re-enqueue delivery (requires --payload-file).",
        )
        parser.add_argument(
            "--payload-file",
            help=(
                "Path to a JSON file containing the raw payload to redeliver. "
                "Required with --requeue unless every matched attempt already "
                "has a stored payload_snapshot."
            ),
        )

    def handle(self, *args, **options):
        qs = WebhookDeliveryAttempt.objects.filter(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
        if options.get("correlation_id"):
            qs = qs.filter(correlation_id=options["correlation_id"])
        if options.get("endpoint_id"):
            qs = qs.filter(endpoint_id=options["endpoint_id"])

        # Only the most recent exhausted attempt per correlation_id/endpoint
        # pair is actionable; earlier ones are history.
        qs = qs.order_by("-created")

        if not qs.exists():
            self.stdout.write(self.style.SUCCESS("No exhausted deliveries match."))
            return

        for attempt in qs:
            self.stdout.write(
                f"{attempt.created.isoformat()}  correlation_id={attempt.correlation_id}  "
                f"endpoint_id={attempt.endpoint_id}  event={attempt.event}  "
                f"http_status={attempt.http_status_code}  attempts={attempt.attempt_number}"
            )

        if not options.get("requeue"):
            self.stdout.write(self.style.WARNING("Dry run. Pass --requeue --payload-file=... to redeliver."))
            return

        payload_file = options.get("payload_file")
        explicit_payload = None
        if payload_file:
            with open(payload_file, encoding="utf-8") as handle:
                explicit_payload = json.load(handle)

        attempts = list(qs)
        missing_payload = [
            a for a in attempts if a.payload_snapshot is None and explicit_payload is None
        ]
        if missing_payload:
            raise CommandError(
                f"--payload-file not supplied and {len(missing_payload)} matched attempt(s) "
                "have no stored payload_snapshot (the endpoint didn't have "
                "retain_payload_snapshot enabled). Supply --payload-file, or narrow the "
                "selection with --correlation-id/--endpoint-id to attempts that do have one."
            )

        count = 0
        for attempt in attempts:
            payload = explicit_payload if explicit_payload is not None else attempt.payload_snapshot
            deliver_webhook.delay(
                attempt.endpoint_id, attempt.event, payload, str(attempt.correlation_id)
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Requeued {count} deliveries."))
        sys.stdout.flush()
