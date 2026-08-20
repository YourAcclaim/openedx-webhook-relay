"""
Tests for the async delivery task: signing, retries, exhaustion, and the
skip/filter path. Network calls are mocked with ``responses`` so no real
HTTP traffic happens.

Tasks are invoked with ``.apply()`` rather than as a plain function call.
Calling a bound Celery task directly (``deliver_webhook(...)``) marks the
request as ``called_directly``, which makes ``self.retry()`` re-raise
immediately instead of looping — that would make every retry test a false
negative.

``.apply()`` alone is not enough either: under ``task_always_eager`` Celery
runs the task exactly once and propagates ``Retry`` instead of re-executing
it. Multi-attempt behaviour is therefore driven explicitly by ``_run``
below, which re-applies the task with an incremented ``retries`` count.
"""

# pylint: disable=missing-function-docstring

import logging
import uuid
from datetime import timedelta

import pytest
import responses
from celery.exceptions import Retry
from django.utils import timezone

from openedx_webhook_relay.metrics import webhook_delivery_recorded
from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint
from openedx_webhook_relay.security import verify_signature
from openedx_webhook_relay.tasks import deliver_webhook
from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

pytestmark = pytest.mark.django_db

RAW_PAYLOAD = {
    "openedx_events.learning.data.CoursePassingStatusData": {
        "is_passing": True,
        "user": {"pii": {"email": "learner@example.com"}},
    },
    "event_metadata": {"id": "evt-1"},
}


#: Upper bound on how many times ``_run`` will re-apply the task before
#: declaring the retry loop runaway. Higher than any ``max_retries`` used here.
_MAX_EAGER_ATTEMPTS = 10


def _correlation_id():
    return str(uuid.uuid4())


def _run(*args):
    """
    Run deliver_webhook through its full retry sequence.

    Celery's eager mode invokes a task exactly once and propagates ``Retry``
    rather than re-executing it, so the retry loop has to be driven from
    here. Each pass re-applies the task with an incremented ``retries``
    count, which is what the task derives ``attempt_number`` from
    (``self.request.retries + 1``).

    Swallowing the ``Retry`` and returning after a single ``apply()`` — as
    this helper used to do — meant the retry tests only ever observed one
    attempt, so they asserted call counts that could never be reached.
    """
    for retries in range(_MAX_EAGER_ATTEMPTS):
        try:
            return deliver_webhook.apply(args=args, retries=retries).get()
        except Retry:
            continue
    raise AssertionError(
        f"deliver_webhook was still retrying after {_MAX_EAGER_ATTEMPTS} attempts; "
        "either max_retries is not being honoured or the attempt cap is too low."
    )


@responses.activate
def test_successful_delivery_is_signed_and_recorded():
    endpoint = WebhookEndpointFactory(signing_secret="shhh")
    responses.add(responses.POST, endpoint.webhook_url, status=200, body="ok")

    correlation_id = _correlation_id()
    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, correlation_id)

    sent = responses.calls[0].request
    signature = sent.headers["X-OpenEdX-Webhook-Signature"]
    assert verify_signature(sent.body, "shhh", signature)
    assert sent.headers["X-OpenEdX-Webhook-Correlation-Id"] == correlation_id

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.SUCCEEDED
    assert attempt.http_status_code == 200
    assert attempt.correlation_id == uuid.UUID(correlation_id)
    assert attempt.duration_ms is not None


@responses.activate
def test_only_on_passing_skips_and_makes_no_http_call():
    endpoint = WebhookEndpointFactory(only_on_passing=True)
    failing_payload = {
        "openedx_events.learning.data.CoursePassingStatusData": {"is_passing": False},
        "event_metadata": {"id": "evt-2"},
    }

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", failing_payload, _correlation_id())

    assert len(responses.calls) == 0
    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.SKIPPED


@responses.activate
def test_disabled_endpoint_is_dropped_without_recording():
    endpoint = WebhookEndpointFactory(enabled=False)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 0
    assert not WebhookDeliveryAttempt.objects.filter(endpoint=endpoint).exists()


@responses.activate
def test_deleted_endpoint_is_dropped_without_error():
    endpoint = WebhookEndpointFactory()
    endpoint_id = endpoint.pk
    endpoint.delete()

    # Should not raise even though the endpoint no longer exists.
    _run(endpoint_id, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())
    assert len(responses.calls) == 0


@responses.activate
def test_retryable_failure_retries_then_exhausts():
    endpoint = WebhookEndpointFactory(max_retries=3)
    responses.add(responses.POST, endpoint.webhook_url, status=503, body="unavailable")
    responses.add(responses.POST, endpoint.webhook_url, status=503, body="unavailable")
    responses.add(responses.POST, endpoint.webhook_url, status=503, body="unavailable")

    correlation_id = _correlation_id()
    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, correlation_id)

    assert len(responses.calls) == 3

    attempts = list(
        WebhookDeliveryAttempt.objects.filter(endpoint=endpoint).order_by("attempt_number")
    )
    assert [a.attempt_number for a in attempts] == [1, 2, 3]
    assert [a.status for a in attempts[:2]] == [
        WebhookDeliveryAttempt.Status.RETRYING,
        WebhookDeliveryAttempt.Status.RETRYING,
    ]
    assert attempts[-1].status == WebhookDeliveryAttempt.Status.EXHAUSTED
    assert attempts[-1].http_status_code == 503
    assert all(a.correlation_id == uuid.UUID(correlation_id) for a in attempts)


@responses.activate
def test_connection_error_is_retried(monkeypatch):
    # pylint: disable=import-outside-toplevel
    import requests

    endpoint = WebhookEndpointFactory(max_retries=2)

    call_count = {"n": 0}

    def _raise_connection_error(*args, **kwargs):  # pylint: disable=unused-argument
        call_count["n"] += 1
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("openedx_webhook_relay.tasks.requests.post", _raise_connection_error)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert call_count["n"] == 2
    # Order explicitly: the model defaults to ["-created"] (newest first), so an
    # unordered [-1] picks the *first* attempt rather than the last.
    attempts = list(
        WebhookDeliveryAttempt.objects.filter(endpoint=endpoint).order_by("attempt_number")
    )
    assert attempts[-1].status == WebhookDeliveryAttempt.Status.EXHAUSTED
    assert "boom" in attempts[-1].error_message


@responses.activate
def test_non_retryable_client_error_exhausts_immediately():
    endpoint = WebhookEndpointFactory(max_retries=5)
    responses.add(responses.POST, endpoint.webhook_url, status=422, body="bad payload")

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 1
    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.EXHAUSTED
    assert attempt.attempt_number == 1
    assert attempt.http_status_code == 422


@responses.activate
def test_pii_allowlist_is_applied_before_signing():
    endpoint = WebhookEndpointFactory(
        signing_secret="shhh",
        pii_allowlist=["data.is_passing", "event_metadata"],
    )
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    sent_body = responses.calls[0].request.body
    assert b"learner@example.com" not in sent_body
    assert b"is_passing" in sent_body


@responses.activate
def test_custom_headers_are_forwarded():
    endpoint = WebhookEndpointFactory(custom_headers={"X-Source": "lms"})
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert responses.calls[0].request.headers["X-Source"] == "lms"


@responses.activate
def test_payload_fingerprint_is_recorded():
    endpoint = WebhookEndpointFactory()
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.payload_fingerprint
    assert len(attempt.payload_fingerprint) == 16


# --- Dual signature during secret rotation --------------------------------


@responses.activate
def test_previous_secret_produces_second_signature_header():
    endpoint = WebhookEndpointFactory(
        signing_secret="new-secret", signing_secret_previous="old-secret"
    )
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    sent = responses.calls[0].request
    assert verify_signature(sent.body, "new-secret", sent.headers["X-OpenEdX-Webhook-Signature"])
    assert verify_signature(
        sent.body, "old-secret", sent.headers["X-OpenEdX-Webhook-Signature-Previous"]
    )


@responses.activate
def test_no_previous_secret_header_when_not_rotating():
    endpoint = WebhookEndpointFactory(signing_secret="only-secret")
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    sent = responses.calls[0].request
    assert "X-OpenEdX-Webhook-Signature-Previous" not in sent.headers


# --- Circuit breaker -------------------------------------------------------


@responses.activate
def test_open_circuit_skips_delivery_with_no_http_call():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now(),
        circuit_breaker_cooldown_seconds=300,
    )

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 0
    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.CIRCUIT_OPEN


@responses.activate
def test_half_open_trial_success_closes_circuit():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now() - timedelta(seconds=301),
        circuit_breaker_cooldown_seconds=300,
        consecutive_failures=5,
    )
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 1
    endpoint.refresh_from_db()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.CLOSED
    assert endpoint.consecutive_failures == 0


@responses.activate
def test_half_open_trial_failure_reopens_circuit_and_still_resolves():
    """
    The trial's own retries (attempt_number > 1) must not be blocked by the
    circuit's own HALF_OPEN state — otherwise the delivery would wedge
    forever instead of resolving to EXHAUSTED. See
    docs/decisions/0007-circuit-breaker.rst.
    """
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now() - timedelta(seconds=301),
        circuit_breaker_cooldown_seconds=300,
        consecutive_failures=5,
        max_retries=2,
    )
    responses.add(responses.POST, endpoint.webhook_url, status=503)
    responses.add(responses.POST, endpoint.webhook_url, status=503)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    # Both attempts of the trial actually ran (not short-circuited).
    assert len(responses.calls) == 2
    attempts = list(
        WebhookDeliveryAttempt.objects.filter(endpoint=endpoint).order_by("attempt_number")
    )
    assert [a.status for a in attempts] == [
        WebhookDeliveryAttempt.Status.RETRYING,
        WebhookDeliveryAttempt.Status.EXHAUSTED,
    ]
    endpoint.refresh_from_db()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.OPEN
    assert endpoint.consecutive_failures == 6


@responses.activate
def test_concurrent_new_delivery_is_skipped_while_trial_in_flight():
    endpoint = WebhookEndpointFactory(circuit_state=WebhookEndpoint.CircuitState.HALF_OPEN)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 0
    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.CIRCUIT_OPEN


@responses.activate
def test_circuit_breaker_disabled_bypasses_open_state():
    endpoint = WebhookEndpointFactory(
        circuit_breaker_enabled=False,
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now(),
    )
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert len(responses.calls) == 1


# --- Opt-in payload snapshot -----------------------------------------------


@responses.activate
def test_payload_snapshot_stored_only_when_endpoint_opts_in_and_exhausted():
    endpoint = WebhookEndpointFactory(retain_payload_snapshot=True, max_retries=1)
    responses.add(responses.POST, endpoint.webhook_url, status=422)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.EXHAUSTED
    assert attempt.payload_snapshot is not None
    assert attempt.payload_snapshot["event_metadata"] == {"id": "evt-1"}


@responses.activate
def test_payload_snapshot_not_stored_when_opted_out():
    endpoint = WebhookEndpointFactory(retain_payload_snapshot=False, max_retries=1)
    responses.add(responses.POST, endpoint.webhook_url, status=422)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.payload_snapshot is None


@responses.activate
def test_payload_snapshot_not_stored_for_successful_delivery_even_if_opted_in():
    endpoint = WebhookEndpointFactory(retain_payload_snapshot=True)
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.SUCCEEDED
    assert attempt.payload_snapshot is None


# --- Metrics signal ----------------------------------------------------


@responses.activate
def test_webhook_delivery_recorded_signal_fires_for_every_attempt():
    endpoint = WebhookEndpointFactory()
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    received = []

    def _receiver(sender, **kwargs):  # pylint: disable=unused-argument
        received.append(kwargs)

    webhook_delivery_recorded.connect(_receiver)
    try:
        _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())
    finally:
        webhook_delivery_recorded.disconnect(_receiver)

    assert len(received) == 1
    assert received[0]["status"] == WebhookDeliveryAttempt.Status.SUCCEEDED
    assert received[0]["endpoint_id"] == endpoint.pk


@responses.activate
def test_unsigned_delivery_logs_a_warning(settings, caplog):
    """
    No endpoint secret and no default secret means no signature header.

    The delivery still succeeds, which is exactly why it has to be loud: the
    receiver has nothing to verify and the audit row looks healthy.
    """
    settings.OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = ""
    endpoint = WebhookEndpointFactory(signing_secret="")
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    with caplog.at_level(logging.WARNING, logger="openedx_webhook_relay.tasks"):
        _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert "UNSIGNED" in caplog.text
    sent = responses.calls[0].request
    assert "X-OpenEdX-Webhook-Signature" not in sent.headers

    attempt = WebhookDeliveryAttempt.objects.get(endpoint=endpoint)
    assert attempt.status == WebhookDeliveryAttempt.Status.SUCCEEDED


@responses.activate
def test_signed_delivery_logs_no_unsigned_warning(settings, caplog):
    settings.OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = ""
    endpoint = WebhookEndpointFactory(signing_secret="shhh")
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    with caplog.at_level(logging.WARNING, logger="openedx_webhook_relay.tasks"):
        _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    assert "UNSIGNED" not in caplog.text
    assert "X-OpenEdX-Webhook-Signature" in responses.calls[0].request.headers


@responses.activate
def test_default_secret_signs_when_endpoint_has_none(settings):
    """The fallback secret still signs, so it must not warn."""
    settings.OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = "fallback-secret"
    endpoint = WebhookEndpointFactory(signing_secret="")
    responses.add(responses.POST, endpoint.webhook_url, status=200)

    _run(endpoint.pk, "COURSE_PASSING_STATUS_UPDATED", RAW_PAYLOAD, _correlation_id())

    sent = responses.calls[0].request
    assert verify_signature(
        sent.body, "fallback-secret", sent.headers["X-OpenEdX-Webhook-Signature"]
    )
