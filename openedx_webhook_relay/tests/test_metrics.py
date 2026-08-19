"""Tests for the metrics/alerting hook."""

# pylint: disable=missing-function-docstring

from django.test import override_settings

from openedx_webhook_relay.metrics import emit_delivery_metric, webhook_delivery_recorded


def test_emit_delivery_metric_fires_signal():
    received = []
    webhook_delivery_recorded.connect(received.append)
    try:
        emit_delivery_metric(
            status="succeeded", endpoint_id=1, event="COURSE_PASSING_STATUS_UPDATED",
            correlation_id="abc", http_status_code=200,
        )
    finally:
        webhook_delivery_recorded.disconnect(received.append)
    assert len(received) == 1


def test_emit_delivery_metric_survives_broken_signal_receiver():
    def _boom(sender, **kwargs):  # pylint: disable=unused-argument
        raise RuntimeError("receiver is broken")

    webhook_delivery_recorded.connect(_boom)
    try:
        # Must not raise even though the receiver blows up.
        emit_delivery_metric(
            status="exhausted", endpoint_id=1, event="COURSE_PASSING_STATUS_UPDATED",
            correlation_id="abc",
        )
    finally:
        webhook_delivery_recorded.disconnect(_boom)


def test_emit_delivery_metric_calls_statsd_client_when_configured():
    class _FakeStatsdClient:
        def __init__(self):
            self.calls = []

        def incr(self, name):
            self.calls.append(name)

    client = _FakeStatsdClient()
    with override_settings(OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT=client):
        emit_delivery_metric(
            status="exhausted", endpoint_id=1, event="COURSE_PASSING_STATUS_UPDATED",
            correlation_id="abc",
        )
    assert client.calls == ["openedx_webhook_relay.delivery.exhausted"]


def test_emit_delivery_metric_survives_broken_statsd_client():
    class _BrokenStatsdClient:
        def incr(self, name):
            raise ConnectionError("statsd is down")

    with override_settings(OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT=_BrokenStatsdClient()):
        # Must not raise.
        emit_delivery_metric(
            status="exhausted", endpoint_id=1, event="COURSE_PASSING_STATUS_UPDATED",
            correlation_id="abc",
        )


def test_emit_delivery_metric_noop_when_no_statsd_client_configured():
    with override_settings(OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT=None):
        emit_delivery_metric(
            status="succeeded", endpoint_id=1, event="COURSE_PASSING_STATUS_UPDATED",
            correlation_id="abc",
        )  # just must not raise
