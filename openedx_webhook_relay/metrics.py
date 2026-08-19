"""
Dependency-free metrics/alerting hook for webhook deliveries.

Two extension points, both optional and best-effort (a broken metrics
client must never break delivery):

1. ``webhook_delivery_recorded`` — a plain Django signal, fired for every
   ``WebhookDeliveryAttempt`` written. Any app (including this LMS's own
   monitoring glue) can connect a receiver without this plugin depending on
   a specific metrics stack.
2. ``OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT`` — an optional Django setting
   holding any object with an ``.incr(name)`` method (statsd, dogstatsd,
   edx-platform's ``dogstats_wrapper``, or a test double). If set, a
   counter is incremented per (status, event) combination.

See docs/decisions/0008-structured-logging-and-metrics.rst.
"""

import logging

from django.conf import settings
from django.dispatch import Signal

logger = logging.getLogger(__name__)

#: Sent for every recorded delivery attempt (success, retry, exhaustion,
#: skip, or circuit-open). Receivers get keyword args: status, endpoint_id,
#: event, correlation_id, http_status_code.
webhook_delivery_recorded = Signal()

METRIC_NAME_PREFIX = "openedx_webhook_relay.delivery"


def emit_delivery_metric(*, status, endpoint_id, event, correlation_id, http_status_code=None):
    """Fire the signal and, if configured, increment a statsd-like counter."""
    try:
        webhook_delivery_recorded.send(
            sender=None,
            status=status,
            endpoint_id=endpoint_id,
            event=event,
            correlation_id=correlation_id,
            http_status_code=http_status_code,
        )
    except Exception:  # pylint: disable=broad-except
        # A receiver misbehaving must never break delivery.
        logger.exception("webhook_delivery_recorded signal receiver raised; ignoring.")

    client = getattr(settings, "OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT", None)
    if client is None:
        return

    metric_name = f"{METRIC_NAME_PREFIX}.{status}"
    try:
        client.incr(metric_name)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to increment metric %s via configured statsd client.", metric_name)
