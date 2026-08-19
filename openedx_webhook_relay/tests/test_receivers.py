"""
Tests for signal receivers.

The key property under test: receivers never perform HTTP I/O themselves —
they only look up matching enabled endpoints and enqueue
``tasks.deliver_webhook``. We patch ``deliver_webhook.delay`` so a call to a
receiver is guaranteed to make zero network requests even if the patch were
removed by mistake (the patched function raises if actually reached with
unexpected args, and ``responses.activate`` in test_tasks.py separately
proves the task itself is what performs I/O).
"""

# pylint: disable=missing-function-docstring

from unittest import mock

import attr
import pytest

from openedx_webhook_relay.receivers import (
    ccx_course_passing_status_updated_receiver,
    course_passing_status_updated_receiver,
)
from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

pytestmark = pytest.mark.django_db


@attr.s(frozen=True)
class _FakeCoursePassingStatusData:
    is_passing = attr.ib(default=True)
    course_id = attr.ib(default="course-v1:Org+Course+Run")


@attr.s(frozen=True)
class _FakeEventMetadata:
    event_id = attr.ib(default="evt-1")


@mock.patch("openedx_webhook_relay.receivers.deliver_webhook")
def test_receiver_enqueues_one_task_per_matching_enabled_endpoint(mock_task):
    matching = [
        WebhookEndpointFactory(event="COURSE_PASSING_STATUS_UPDATED"),
        WebhookEndpointFactory(event="COURSE_PASSING_STATUS_UPDATED"),
    ]
    WebhookEndpointFactory(event="COURSE_PASSING_STATUS_UPDATED", enabled=False)
    WebhookEndpointFactory(event="CCX_COURSE_PASSING_STATUS_UPDATED")

    course_passing_status_updated_receiver(
        course_passing_status=_FakeCoursePassingStatusData(),
        metadata=_FakeEventMetadata(),
    )

    assert mock_task.delay.call_count == 2
    called_endpoint_ids = {call.args[0] for call in mock_task.delay.call_args_list}
    assert called_endpoint_ids == {e.pk for e in matching}

    # All enqueued calls for the same triggering event share one correlation id.
    correlation_ids = {call.args[3] for call in mock_task.delay.call_args_list}
    assert len(correlation_ids) == 1

    # Event name and raw payload shape are passed through untouched (task
    # applies PII filtering later, not the receiver).
    for call in mock_task.delay.call_args_list:
        assert call.args[1] == "COURSE_PASSING_STATUS_UPDATED"
        payload = call.args[2]
        assert "event_metadata" in payload


@mock.patch("openedx_webhook_relay.receivers.deliver_webhook")
def test_receiver_no_op_when_no_endpoints_match(mock_task):
    WebhookEndpointFactory(event="CCX_COURSE_PASSING_STATUS_UPDATED")

    course_passing_status_updated_receiver(
        course_passing_status=_FakeCoursePassingStatusData(),
        metadata=_FakeEventMetadata(),
    )

    mock_task.delay.assert_not_called()


@mock.patch("openedx_webhook_relay.receivers.deliver_webhook")
def test_receiver_ignores_disabled_endpoints(mock_task):
    WebhookEndpointFactory(event="COURSE_PASSING_STATUS_UPDATED", enabled=False)

    course_passing_status_updated_receiver(
        course_passing_status=_FakeCoursePassingStatusData(),
        metadata=_FakeEventMetadata(),
    )

    mock_task.delay.assert_not_called()


@mock.patch("openedx_webhook_relay.receivers.deliver_webhook")
def test_ccx_receiver_dispatches_to_ccx_event_name(mock_task):
    endpoint = WebhookEndpointFactory(event="CCX_COURSE_PASSING_STATUS_UPDATED")

    ccx_course_passing_status_updated_receiver(
        course_passing_status=_FakeCoursePassingStatusData(),
        metadata=_FakeEventMetadata(),
    )

    mock_task.delay.assert_called_once()
    args = mock_task.delay.call_args.args
    assert args[0] == endpoint.pk
    assert args[1] == "CCX_COURSE_PASSING_STATUS_UPDATED"
