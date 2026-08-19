"""
Open edX event receivers.

These functions run synchronously on the signal-sending thread (LMS request
or whatever process emits the openedx-events signal), so they are kept
deliberately minimal: one cheap, indexed DB query to find matching enabled
endpoints, then an async enqueue per match. No network I/O happens here —
see tasks.deliver_webhook for the actual HTTP delivery.
"""

import logging
import uuid

from attrs import asdict

from openedx_webhook_relay.models import WebhookEndpoint
from openedx_webhook_relay.serializers import value_serializer
from openedx_webhook_relay.tasks import deliver_webhook

logger = logging.getLogger(__name__)


def _process_event(event_name: str, data, **kwargs):
    """Find enabled endpoints for this event and enqueue delivery for each."""
    endpoint_ids = list(
        WebhookEndpoint.objects.filter(enabled=True, event=event_name).values_list("pk", flat=True)
    )
    if not endpoint_ids:
        return

    correlation_id = str(uuid.uuid4())
    data_type = str(type(data)).split("'")[1]
    raw_payload = {
        data_type: asdict(data, value_serializer=value_serializer),
        "event_metadata": asdict(kwargs.get("metadata")),
    }

    for endpoint_id in endpoint_ids:
        deliver_webhook.delay(endpoint_id, event_name, raw_payload, correlation_id)

    logger.info(
        "Enqueued %s webhook delivery task(s) for event=%s correlation_id=%s",
        len(endpoint_ids), event_name, correlation_id,
        extra={
            "event": event_name,
            "correlation_id": correlation_id,
            "endpoint_count": len(endpoint_ids),
        },
    )


def course_passing_status_updated_receiver(course_passing_status, **kwargs):
    """Handle COURSE_PASSING_STATUS_UPDATED."""
    _process_event("COURSE_PASSING_STATUS_UPDATED", course_passing_status, **kwargs)


def ccx_course_passing_status_updated_receiver(course_passing_status, **kwargs):
    """Handle CCX_COURSE_PASSING_STATUS_UPDATED."""
    _process_event("CCX_COURSE_PASSING_STATUS_UPDATED", course_passing_status, **kwargs)
