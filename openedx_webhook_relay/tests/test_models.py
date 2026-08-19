"""Unit tests for WebhookEndpoint and WebhookDeliveryAttempt models."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint
from openedx_webhook_relay.tests.factories import (
    WebhookDeliveryAttemptFactory,
    WebhookEndpointFactory,
)

pytestmark = pytest.mark.django_db


def test_str_uses_description_when_present():
    endpoint = WebhookEndpointFactory(description="Credly badges")
    assert str(endpoint) == "Credly badges → https://receiver.example.com/webhook"


def test_str_falls_back_to_event_when_no_description():
    endpoint = WebhookEndpointFactory(description="")
    assert str(endpoint) == "COURSE_PASSING_STATUS_UPDATED → https://receiver.example.com/webhook"


def test_clean_rejects_non_https_url():
    endpoint = WebhookEndpointFactory.build(webhook_url="http://insecure.example.com")
    with pytest.raises(ValidationError):
        endpoint.clean()


def test_clean_accepts_https_url():
    endpoint = WebhookEndpointFactory.build(webhook_url="https://secure.example.com")
    endpoint.clean()  # should not raise


def test_clean_rejects_non_dict_custom_headers():
    endpoint = WebhookEndpointFactory.build(custom_headers=["not", "a", "dict"])
    with pytest.raises(ValidationError):
        endpoint.clean()


@pytest.mark.parametrize("field_name", ["pii_allowlist", "pii_denylist"])
def test_clean_rejects_non_list_pii_fields(field_name):
    endpoint = WebhookEndpointFactory.build(**{field_name: {"not": "a list"}})
    with pytest.raises(ValidationError):
        endpoint.clean()


def test_effective_secret_prefers_endpoint_secret():
    endpoint = WebhookEndpointFactory(signing_secret="specific-secret")
    assert endpoint.effective_secret() == "specific-secret"


@override_settings(OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET="fallback-secret")
def test_effective_secret_falls_back_to_settings():
    endpoint = WebhookEndpointFactory(signing_secret="")
    assert endpoint.effective_secret() == "fallback-secret"


def test_signing_secret_round_trips_through_the_database():
    endpoint = WebhookEndpointFactory(signing_secret="round-trip-me")
    endpoint.refresh_from_db()
    assert endpoint.signing_secret == "round-trip-me"


def test_masked_secret_never_exposes_plaintext():
    endpoint = WebhookEndpointFactory(signing_secret="do-not-leak-this")
    masked = endpoint.masked_secret
    assert "do-not-leak-this" not in masked
    assert masked.endswith("this")


def test_masked_secret_when_no_secret_set():
    endpoint = WebhookEndpointFactory(signing_secret="")
    assert endpoint.masked_secret == "(using default secret)"


def test_delivery_attempt_str():
    attempt = WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.SUCCEEDED)
    assert "succeeded" in str(attempt)
    assert attempt.event in str(attempt)


def test_delivery_attempts_ordered_most_recent_first():
    endpoint = WebhookEndpointFactory()
    first = WebhookDeliveryAttemptFactory(endpoint=endpoint, attempt_number=1)
    second = WebhookDeliveryAttemptFactory(endpoint=endpoint, attempt_number=2)
    ordered = list(WebhookDeliveryAttempt.objects.filter(endpoint=endpoint))
    assert ordered[0].pk == second.pk
    assert ordered[1].pk == first.pk


# --- Signing secret rotation --------------------------------------------


def test_effective_previous_secret_blank_by_default():
    endpoint = WebhookEndpointFactory()
    assert endpoint.effective_previous_secret() == ""


def test_effective_previous_secret_round_trips():
    endpoint = WebhookEndpointFactory(signing_secret_previous="old-secret")
    endpoint.refresh_from_db()
    assert endpoint.effective_previous_secret() == "old-secret"


def test_masked_previous_secret_when_unset():
    endpoint = WebhookEndpointFactory()
    assert endpoint.masked_previous_secret == "(none)"


def test_masked_previous_secret_never_exposes_plaintext():
    endpoint = WebhookEndpointFactory(signing_secret_previous="old-plaintext-secret")
    masked = endpoint.masked_previous_secret
    assert "old-plaintext-secret" not in masked
    assert masked.endswith("cret")


def test_secret_backend_reference_assigned_before_first_save():
    endpoint = WebhookEndpointFactory.build()
    assert endpoint.secret_backend_reference is not None


def test_secret_backend_reference_is_unique_per_endpoint():
    a = WebhookEndpointFactory()
    b = WebhookEndpointFactory()
    assert a.secret_backend_reference != b.secret_backend_reference


# --- Circuit breaker -----------------------------------------------------


def test_try_acquire_delivery_slot_true_when_closed():
    endpoint = WebhookEndpointFactory()
    assert endpoint.try_acquire_delivery_slot() is True


def test_try_acquire_delivery_slot_true_when_breaker_disabled_even_if_open():
    endpoint = WebhookEndpointFactory(
        circuit_breaker_enabled=False,
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now(),
    )
    assert endpoint.try_acquire_delivery_slot() is True


def test_try_acquire_delivery_slot_false_when_open_and_within_cooldown():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now(),
        circuit_breaker_cooldown_seconds=300,
    )
    assert endpoint.try_acquire_delivery_slot() is False


def test_try_acquire_delivery_slot_transitions_to_half_open_after_cooldown():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now() - timedelta(seconds=301),
        circuit_breaker_cooldown_seconds=300,
    )
    assert endpoint.try_acquire_delivery_slot() is True
    endpoint.refresh_from_db()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.HALF_OPEN


def test_try_acquire_delivery_slot_false_when_already_half_open():
    endpoint = WebhookEndpointFactory(circuit_state=WebhookEndpoint.CircuitState.HALF_OPEN)
    assert endpoint.try_acquire_delivery_slot() is False


def test_only_one_caller_wins_the_half_open_trial():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.OPEN,
        circuit_opened_at=timezone.now() - timedelta(seconds=301),
        circuit_breaker_cooldown_seconds=300,
    )
    same_endpoint_other_worker = WebhookEndpoint.objects.get(pk=endpoint.pk)

    first_result = endpoint.try_acquire_delivery_slot()
    second_result = same_endpoint_other_worker.try_acquire_delivery_slot()

    assert first_result is True
    assert second_result is False


def test_record_circuit_failure_increments_and_opens_at_threshold():
    endpoint = WebhookEndpointFactory(circuit_breaker_failure_threshold=3)
    endpoint.record_circuit_failure()
    endpoint.record_circuit_failure()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.CLOSED
    endpoint.record_circuit_failure()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.OPEN
    endpoint.refresh_from_db()
    assert endpoint.consecutive_failures == 3
    assert endpoint.circuit_opened_at is not None


def test_record_circuit_success_fully_resets():
    endpoint = WebhookEndpointFactory(
        circuit_state=WebhookEndpoint.CircuitState.HALF_OPEN,
        consecutive_failures=5,
        circuit_opened_at=timezone.now(),
    )
    endpoint.record_circuit_success()
    endpoint.refresh_from_db()
    assert endpoint.circuit_state == WebhookEndpoint.CircuitState.CLOSED
    assert endpoint.consecutive_failures == 0
    assert endpoint.circuit_opened_at is None
