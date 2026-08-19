"""Tests for the redeliver_failed_webhooks management command."""

# pylint: disable=missing-function-docstring

import json
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.tests.factories import (
    WebhookDeliveryAttemptFactory,
    WebhookEndpointFactory,
)

pytestmark = pytest.mark.django_db


def test_dry_run_lists_exhausted_without_requeuing():
    endpoint = WebhookEndpointFactory()
    WebhookDeliveryAttemptFactory(endpoint=endpoint, status=WebhookDeliveryAttempt.Status.EXHAUSTED)
    WebhookDeliveryAttemptFactory(endpoint=endpoint, status=WebhookDeliveryAttempt.Status.SUCCEEDED)

    out = StringIO()
    call_command("redeliver_failed_webhooks", stdout=out)

    output = out.getvalue()
    assert str(endpoint.pk) in output
    assert "Dry run" in output


def test_no_exhausted_deliveries_reports_success():
    out = StringIO()
    call_command("redeliver_failed_webhooks", stdout=out)
    assert "No exhausted deliveries match" in out.getvalue()


def test_requeue_without_payload_file_errors():
    WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
    with pytest.raises(CommandError):
        call_command("redeliver_failed_webhooks", requeue=True, stdout=StringIO())


@mock.patch("openedx_webhook_relay.management.commands.redeliver_failed_webhooks.deliver_webhook")
def test_requeue_with_payload_file_reenqueues(mock_task, tmp_path):
    endpoint = WebhookEndpointFactory()
    attempt = WebhookDeliveryAttemptFactory(
        endpoint=endpoint, status=WebhookDeliveryAttempt.Status.EXHAUSTED
    )

    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"event_metadata": {"id": "evt-1"}}))

    out = StringIO()
    call_command(
        "redeliver_failed_webhooks",
        requeue=True,
        payload_file=str(payload_file),
        stdout=out,
    )

    mock_task.delay.assert_called_once()
    args = mock_task.delay.call_args.args
    assert args[0] == endpoint.pk
    assert args[1] == attempt.event
    assert "Requeued 1" in out.getvalue()


@mock.patch("openedx_webhook_relay.management.commands.redeliver_failed_webhooks.deliver_webhook")
def test_requeue_uses_stored_snapshot_without_payload_file(mock_task):
    endpoint = WebhookEndpointFactory(retain_payload_snapshot=True)
    attempt = WebhookDeliveryAttemptFactory(
        endpoint=endpoint,
        status=WebhookDeliveryAttempt.Status.EXHAUSTED,
        payload_snapshot={"event_metadata": {"id": "evt-1"}},
    )

    out = StringIO()
    call_command("redeliver_failed_webhooks", requeue=True, stdout=out)

    mock_task.delay.assert_called_once_with(
        endpoint.pk, attempt.event, {"event_metadata": {"id": "evt-1"}}, str(attempt.correlation_id)
    )


def test_filters_by_correlation_id():
    endpoint_a = WebhookEndpointFactory()
    endpoint_b = WebhookEndpointFactory()
    attempt_a = WebhookDeliveryAttemptFactory(
        endpoint=endpoint_a, status=WebhookDeliveryAttempt.Status.EXHAUSTED
    )
    attempt_b = WebhookDeliveryAttemptFactory(
        endpoint=endpoint_b, status=WebhookDeliveryAttempt.Status.EXHAUSTED
    )

    out = StringIO()
    call_command(
        "redeliver_failed_webhooks",
        correlation_id=str(attempt_a.correlation_id),
        stdout=out,
    )

    output = out.getvalue()
    assert str(attempt_a.correlation_id) in output
    assert str(attempt_b.correlation_id) not in output
