"""
Async delivery of webhook payloads via Celery.

This module is designed around the biggest structural risks a naive
webhook-relay implementation would have:

1. The signal receiver never makes a network call itself — it only decides
   *whether* a webhook applies (cheap DB lookup) and hands off to
   ``deliver_webhook.delay()``. A slow or unreachable receiver endpoint can
   no longer block the request/worker thread that raised the openedx-events
   signal.
2. Delivery failures are retried with exponential backoff + jitter, up to
   each endpoint's configured ``max_retries``. When retries are exhausted,
   the event is not silently dropped: a ``WebhookDeliveryAttempt`` row with
   status ``EXHAUSTED`` is written and logged at ERROR level, acting as this
   plugin's dead-letter record. Operators redeliver with
   ``./manage.py redeliver_failed_webhooks``.
3. A per-endpoint circuit breaker stops hammering a receiver that's
   persistently down (docs/decisions/0007-circuit-breaker.rst).
4. Every attempt is reported through ``metrics.emit_delivery_metric`` for
   external alerting, and log lines carry structured ``extra`` fields for
   JSON log pipelines (docs/decisions/0008-structured-logging-and-metrics.rst).
5. The audit trail this module writes doesn't grow forever unattended:
   ``purge_old_delivery_attempts_task`` is registered on Celery beat by
   default to enforce ``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS``
   (docs/decisions/0010-scheduled-retention-purge.rst).

We retry explicitly (via ``self.retry(...)``) rather than relying purely on
Celery's ``autoretry_for``, because the retry ceiling is per-endpoint
(``WebhookEndpoint.max_retries``), not a single static task-level constant.
"""

import logging
import time

import requests
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings

from openedx_webhook_relay.metrics import emit_delivery_metric
from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint
from openedx_webhook_relay.retention import purge_old_delivery_attempts
from openedx_webhook_relay.security import (
    apply_allowlist,
    apply_denylist,
    payload_fingerprint,
    serialize_payload,
    should_send_passing_event,
    sign_payload,
)

logger = get_task_logger(__name__)
django_logger = logging.getLogger(__name__)

# Backoff schedule if RETRY_BACKOFF setting isn't overridden: 30s, 60s, 120s...
DEFAULT_RETRY_BACKOFF_SECONDS = 30
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 900

PREVIOUS_SIGNATURE_HEADER_SUFFIX = "-Previous"


class RetryableDeliveryError(Exception):
    """Raised for responses/errors that should trigger a retry."""

    def __init__(self, message, http_status_code=None):
        super().__init__(message)
        self.http_status_code = http_status_code


def _shape_and_check(raw_payload: dict, endpoint: WebhookEndpoint):
    """Apply PII allow/deny lists and the only-on-passing filter."""
    payload = raw_payload.copy()
    if endpoint.pii_allowlist:
        payload = apply_allowlist(payload, endpoint.pii_allowlist)
    if endpoint.pii_denylist:
        apply_denylist(payload, endpoint.pii_denylist)

    return payload, should_send_passing_event(payload, endpoint.only_on_passing)


def _backoff_seconds(attempt_number: int) -> int:
    base = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS
    )
    ceiling = getattr(
        settings,
        "OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_MAX_SECONDS",
        DEFAULT_RETRY_BACKOFF_MAX_SECONDS,
    )
    return min(base * (2 ** (attempt_number - 1)), ceiling)


def _record_attempt(*, endpoint, event, correlation_id, attempt_number, status,
                     http_status_code=None, duration_ms=None, error_message="",
                     fingerprint="", payload=None):
    """
    Write the audit-trail row and fan out to the metrics hook.

    ``payload`` is only ever persisted (as ``payload_snapshot``) when the
    endpoint has opted in via ``retain_payload_snapshot`` AND the outcome is
    EXHAUSTED — see docs/decisions/0006-admin-bulk-redeliver.rst for why
    this is opt-in and scoped that narrowly.
    """
    # One keyword-only parameter per audit column; grouping them into an object
    # would only move the same fan-out to the call sites.
    # pylint: disable=too-many-arguments
    snapshot = None
    if (
        payload is not None
        and status == WebhookDeliveryAttempt.Status.EXHAUSTED
        and endpoint is not None
        and getattr(endpoint, "retain_payload_snapshot", False)
    ):
        snapshot = payload

    attempt = WebhookDeliveryAttempt.objects.create(
        endpoint_id=endpoint.pk if endpoint is not None else None,
        event=event,
        correlation_id=correlation_id,
        attempt_number=attempt_number,
        status=status,
        http_status_code=http_status_code,
        duration_ms=duration_ms,
        error_message=error_message[:4000] if error_message else "",
        payload_fingerprint=fingerprint,
        payload_snapshot=snapshot,
    )

    emit_delivery_metric(
        status=status,
        endpoint_id=endpoint.pk if endpoint is not None else None,
        event=event,
        correlation_id=str(correlation_id),
        http_status_code=http_status_code,
    )
    return attempt


def _log_extra(*, endpoint_id, event, correlation_id, status, **more):
    extra = {
        "endpoint_id": endpoint_id,
        "event": event,
        "correlation_id": str(correlation_id),
        "status": status,
    }
    extra.update(more)
    return extra


@shared_task(bind=True, acks_late=True)
def deliver_webhook(self, endpoint_id, event, raw_payload, correlation_id):
    """
    Deliver ``raw_payload`` for ``event`` to the ``WebhookEndpoint`` identified
    by ``endpoint_id``, signing it and recording an audit-trail entry.

    Retries on connection errors, timeouts, and 5xx/429 responses, up to the
    endpoint's ``max_retries``. Does NOT retry 4xx (other than 429) —
    those indicate a misconfigured receiver that a retry won't fix.
    """
    # A linear delivery path with several distinct early exits (disabled
    # endpoint, open circuit, filtered payload, each failure mode).
    # pylint: disable=too-many-locals,too-many-return-statements
    try:
        endpoint = WebhookEndpoint.objects.get(pk=endpoint_id, enabled=True)
    except WebhookEndpoint.DoesNotExist:
        django_logger.warning(
            "deliver_webhook: endpoint %s no longer exists or is disabled; dropping.",
            endpoint_id,
            extra=_log_extra(
                endpoint_id=endpoint_id,
                event=event,
                correlation_id=correlation_id,
                status="dropped",
            ),
        )
        return None

    attempt_number = self.request.retries + 1
    payload, should_send = _shape_and_check(raw_payload, endpoint)
    fingerprint = payload_fingerprint(payload)

    if not should_send:
        _record_attempt(
            endpoint=endpoint,
            event=event,
            correlation_id=correlation_id,
            attempt_number=attempt_number,
            status=WebhookDeliveryAttempt.Status.SKIPPED,
            fingerprint=fingerprint,
        )
        return None

    # Only gate the *first* attempt of a delivery chain on the circuit
    # breaker. Once a delivery is accepted (including the single half-open
    # trial), its own retries (attempt_number > 1) must be allowed to run
    # to completion regardless of circuit_state — otherwise a half-open
    # trial's own retry would see circuit_state=HALF_OPEN and block itself,
    # leaving the breaker stuck and the delivery neither retried nor
    # resolved. Concurrent *new* deliveries are still correctly blocked
    # while a trial is in flight, since try_acquire_delivery_slot() returns
    # False for other callers while state is HALF_OPEN.
    if attempt_number == 1 and not endpoint.try_acquire_delivery_slot():
        _record_attempt(
            endpoint=endpoint,
            event=event,
            correlation_id=correlation_id,
            attempt_number=attempt_number,
            status=WebhookDeliveryAttempt.Status.CIRCUIT_OPEN,
            fingerprint=fingerprint,
        )
        django_logger.warning(
            "Webhook delivery skipped: circuit breaker open for endpoint=%s event=%s "
            "correlation_id=%s",
            endpoint_id, event, correlation_id,
            extra=_log_extra(
                endpoint_id=endpoint_id, event=event, correlation_id=correlation_id,
                status=WebhookDeliveryAttempt.Status.CIRCUIT_OPEN,
            ),
        )
        return None

    payload_bytes = serialize_payload(payload)
    header_name = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_SIGNATURE_HEADER", "X-OpenEdX-Webhook-Signature"
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-OpenEdX-Webhook-Correlation-Id": str(correlation_id),
        **(endpoint.custom_headers or {}),
    }
    secret = endpoint.effective_secret()
    if secret:
        headers[header_name] = sign_payload(payload_bytes, secret)

    # Dual-signature support during a secret rotation window: a receiver
    # can accept either signature until it has confirmed the new secret and
    # the operator clears signing_secret_previous. See
    # docs/decisions/0005-signing-secret-rotation.rst.
    previous_secret = endpoint.effective_previous_secret()
    if previous_secret:
        headers[f"{header_name}{PREVIOUS_SIGNATURE_HEADER_SUFFIX}"] = sign_payload(
            payload_bytes, previous_secret
        )

    start = time.monotonic()
    try:
        response = requests.post(
            endpoint.webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=endpoint.timeout_seconds,
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return _fail_or_retry(
            self, endpoint, event, correlation_id, attempt_number, fingerprint, payload,
            duration_ms, error_message=str(exc), http_status_code=None, exc=exc,
        )

    duration_ms = int((time.monotonic() - start) * 1000)

    if response.status_code >= 500 or response.status_code == 429:
        exc = RetryableDeliveryError(
            f"Retryable HTTP {response.status_code} from {endpoint.webhook_url}",
            http_status_code=response.status_code,
        )
        return _fail_or_retry(
            self, endpoint, event, correlation_id, attempt_number, fingerprint, payload,
            duration_ms, error_message=response.text[:2000],
            http_status_code=response.status_code, exc=exc,
        )

    if response.status_code >= 400:
        # Non-retryable client error (bad URL, auth, payload rejected, etc.):
        # record as exhausted immediately rather than pretending it's "in progress".
        _record_attempt(
            endpoint=endpoint,
            event=event,
            correlation_id=correlation_id,
            attempt_number=attempt_number,
            status=WebhookDeliveryAttempt.Status.EXHAUSTED,
            http_status_code=response.status_code,
            duration_ms=duration_ms,
            error_message=response.text[:2000],
            fingerprint=fingerprint,
            payload=payload,
        )
        endpoint.record_circuit_failure()
        django_logger.error(
            "Webhook delivery non-retryable failure: event=%s endpoint=%s status=%s "
            "correlation_id=%s",
            event, endpoint_id, response.status_code, correlation_id,
            extra=_log_extra(
                endpoint_id=endpoint_id,
                event=event,
                correlation_id=correlation_id,
                status=WebhookDeliveryAttempt.Status.EXHAUSTED,
                http_status_code=response.status_code,
            ),
        )
        return None

    _record_attempt(
        endpoint=endpoint,
        event=event,
        correlation_id=correlation_id,
        attempt_number=attempt_number,
        status=WebhookDeliveryAttempt.Status.SUCCEEDED,
        http_status_code=response.status_code,
        duration_ms=duration_ms,
        fingerprint=fingerprint,
    )
    endpoint.record_circuit_success()
    return None


def _fail_or_retry(task, endpoint, event, correlation_id, attempt_number, fingerprint, payload,
                    duration_ms, *, error_message, http_status_code, exc):
    """Record this failed attempt and either schedule a retry or mark it exhausted."""
    # Threads the full delivery context through to the audit row.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    if attempt_number < endpoint.max_retries:
        _record_attempt(
            endpoint=endpoint,
            event=event,
            correlation_id=correlation_id,
            attempt_number=attempt_number,
            status=WebhookDeliveryAttempt.Status.RETRYING,
            http_status_code=http_status_code,
            duration_ms=duration_ms,
            error_message=error_message,
            fingerprint=fingerprint,
        )
        raise task.retry(
            exc=exc,
            countdown=_backoff_seconds(attempt_number),
            max_retries=endpoint.max_retries,
        )

    _record_attempt(
        endpoint=endpoint,
        event=event,
        correlation_id=correlation_id,
        attempt_number=attempt_number,
        status=WebhookDeliveryAttempt.Status.EXHAUSTED,
        http_status_code=http_status_code,
        duration_ms=duration_ms,
        error_message=error_message,
        fingerprint=fingerprint,
        payload=payload,
    )
    endpoint.record_circuit_failure()
    django_logger.error(
        "Webhook delivery EXHAUSTED after %s attempts: event=%s endpoint=%s "
        "correlation_id=%s. Redeliver with: "
        "./manage.py redeliver_failed_webhooks --correlation-id=%s",
        attempt_number, event, endpoint.pk, correlation_id, correlation_id,
        extra=_log_extra(
            endpoint_id=endpoint.pk, event=event, correlation_id=correlation_id,
            status=WebhookDeliveryAttempt.Status.EXHAUSTED, attempt_number=attempt_number,
        ),
    )


@shared_task
def purge_old_delivery_attempts_task():
    """
    Delete ``WebhookDeliveryAttempt`` rows past the configured retention
    window (``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS``).

    Registered on ``CELERY_BEAT_SCHEDULE`` by default in
    ``settings/common.py`` — see docs/decisions/0010-scheduled-retention-purge.rst.
    Set ``OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False`` to disable the
    schedule registration and manage retention with your own cron/beat entry
    (or the ``purge_old_delivery_attempts`` management command) instead.

    Unlike the management command, this always deletes (``dry_run=False``)
    — a scheduled job that only ever logs a count is not a retention policy.
    """
    if not getattr(settings, "OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED", True):
        django_logger.debug(
            "purge_old_delivery_attempts_task: OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED "
            "is False; skipping (this shouldn't normally run since the beat schedule entry "
            "isn't registered in that case, but the task guards itself regardless)."
        )
        return {"skipped": True}

    result = purge_old_delivery_attempts(dry_run=False)
    if result.deleted:
        django_logger.info(
            "purge_old_delivery_attempts_task: deleted %s delivery attempt(s) older than "
            "%s days (cutoff=%s).",
            result.deleted, result.days, result.cutoff.isoformat(),
            extra={"deleted": result.deleted, "retention_days": result.days},
        )
    else:
        django_logger.debug(
            "purge_old_delivery_attempts_task: no delivery attempts older than %s days.",
            result.days,
        )
    return {"deleted": result.deleted, "days": result.days}
