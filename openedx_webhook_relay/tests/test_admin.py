"""
Tests for admin: secret masking/write-only behavior and the read-only
delivery-attempt audit view.
"""

# pylint: disable=invalid-name,missing-function-docstring,redefined-outer-name

from unittest import mock

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.urls import reverse

from openedx_webhook_relay.admin import (
    WebhookDeliveryAttemptAdmin,
    WebhookEndpointAdmin,
    WebhookEndpointAdminForm,
)
from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint
from openedx_webhook_relay.tests.factories import (
    WebhookDeliveryAttemptFactory,
    WebhookEndpointFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    User = get_user_model()
    user = User.objects.create_superuser("admin", "admin@example.com", "password")
    client.force_login(user)
    return client


def _base_form_data(**overrides):
    data = {
        "description": "Credly",
        "event": "COURSE_PASSING_STATUS_UPDATED",
        "webhook_url": "https://receiver.example.com/webhook",
        "enabled": "on",
        "signing_secret": "",
        "clear_signing_secret": "",
        "rotate_keeping_previous": "on",
        "clear_previous_signing_secret": "",
        "custom_headers": "{}",
        "only_on_passing": "on",
        "timeout_seconds": "10",
        "max_retries": "5",
        "retain_payload_snapshot": "",
        "circuit_breaker_enabled": "on",
        "circuit_breaker_failure_threshold": "5",
        "circuit_breaker_cooldown_seconds": "300",
        "pii_allowlist": "[]",
        "pii_denylist": "[]",
    }
    data.update(overrides)
    return data


def test_form_keeps_existing_secret_when_field_left_blank():
    endpoint = WebhookEndpointFactory(signing_secret="original-secret")
    form = WebhookEndpointAdminForm(data=_base_form_data(), instance=endpoint)

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.signing_secret == "original-secret"


def test_form_updates_secret_when_new_value_provided():
    endpoint = WebhookEndpointFactory(signing_secret="original-secret")
    form = WebhookEndpointAdminForm(
        data=_base_form_data(signing_secret="brand-new-secret"), instance=endpoint
    )

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.signing_secret == "brand-new-secret"


def test_form_clears_secret_when_checkbox_set():
    endpoint = WebhookEndpointFactory(signing_secret="original-secret")
    form = WebhookEndpointAdminForm(
        data=_base_form_data(clear_signing_secret="on"), instance=endpoint
    )

    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.signing_secret == ""


def test_secret_widget_never_renders_existing_value():
    endpoint = WebhookEndpointFactory(signing_secret="original-secret")
    form = WebhookEndpointAdminForm(instance=endpoint)
    rendered = str(form["signing_secret"])
    assert "original-secret" not in rendered
    assert 'type="password"' in rendered


def test_admin_change_page_does_not_leak_secret(admin_client):
    endpoint = WebhookEndpointFactory(signing_secret="totally-secret-value")
    url = reverse("admin:openedx_webhook_relay_webhookendpoint_change", args=[endpoint.pk])

    response = admin_client.get(url)

    assert response.status_code == 200
    assert b"totally-secret-value" not in response.content


def test_admin_list_page_shows_masked_secret_not_plaintext(admin_client):
    WebhookEndpointFactory(signing_secret="totally-secret-value")
    url = reverse("admin:openedx_webhook_relay_webhookendpoint_changelist")

    response = admin_client.get(url)

    assert response.status_code == 200
    assert b"totally-secret-value" not in response.content


def test_delivery_attempt_admin_is_read_only(admin_client):
    attempt = WebhookDeliveryAttemptFactory()
    change_url = reverse(
        "admin:openedx_webhook_relay_webhookdeliveryattempt_change", args=[attempt.pk]
    )
    add_url = reverse("admin:openedx_webhook_relay_webhookdeliveryattempt_add")

    change_response = admin_client.get(change_url)
    add_response = admin_client.get(add_url)

    # No editable fields should be rendered; Django still returns 200 for
    # change view but the admin should not expose add.
    assert add_response.status_code == 403
    assert change_response.status_code == 200


def test_delivery_attempt_admin_list_filters_by_status(admin_client):
    WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.SUCCEEDED)
    WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
    url = reverse("admin:openedx_webhook_relay_webhookdeliveryattempt_changelist")

    response = admin_client.get(url, {"status": WebhookDeliveryAttempt.Status.EXHAUSTED})

    assert response.status_code == 200
    assert b"Exhausted" in response.content


# --- Secret rotation via save_model ---------------------------------------


def _endpoint_admin():
    return WebhookEndpointAdmin(WebhookEndpoint, django_admin.site)


def test_save_model_moves_old_secret_to_previous_on_rotation():
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    form = WebhookEndpointAdminForm(
        data=_base_form_data(signing_secret="new-secret", rotate_keeping_previous="on"),
        instance=endpoint,
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _endpoint_admin().save_model(request=None, obj=obj, form=form, change=True)

    obj.refresh_from_db()
    assert obj.signing_secret == "new-secret"
    assert obj.signing_secret_previous == "old-secret"


def test_save_model_discards_old_secret_when_not_keeping_previous():
    endpoint = WebhookEndpointFactory(signing_secret="old-secret")
    form = WebhookEndpointAdminForm(
        data=_base_form_data(signing_secret="new-secret", rotate_keeping_previous=""),
        instance=endpoint,
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _endpoint_admin().save_model(request=None, obj=obj, form=form, change=True)

    obj.refresh_from_db()
    assert obj.signing_secret == "new-secret"
    assert obj.signing_secret_previous == ""


def test_save_model_clear_previous_checkbox_clears_it():
    endpoint = WebhookEndpointFactory(
        signing_secret="current", signing_secret_previous="stale-old-secret"
    )
    form = WebhookEndpointAdminForm(
        data=_base_form_data(clear_previous_signing_secret="on"),
        instance=endpoint,
    )
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)

    _endpoint_admin().save_model(request=None, obj=obj, form=form, change=True)

    obj.refresh_from_db()
    assert obj.signing_secret == "current"
    assert obj.signing_secret_previous == ""


def test_previous_secret_status_masked_in_admin_help_text():
    endpoint = WebhookEndpointFactory(signing_secret_previous="rotation-in-progress-secret")
    form = WebhookEndpointAdminForm(instance=endpoint)
    help_text = form.fields["clear_previous_signing_secret"].help_text
    assert "rotation-in-progress-secret" not in help_text
    assert "cret" in help_text  # masked tail is present


# --- Bulk requeue action ----------------------------------------------------


def _attempt_admin():
    return WebhookDeliveryAttemptAdmin(WebhookDeliveryAttempt, django_admin.site)


@mock.patch("openedx_webhook_relay.admin.deliver_webhook")
def test_requeue_selected_uses_snapshot_when_present(mock_task):
    endpoint = WebhookEndpointFactory(retain_payload_snapshot=True)
    attempt = WebhookDeliveryAttemptFactory(
        endpoint=endpoint,
        status=WebhookDeliveryAttempt.Status.EXHAUSTED,
        payload_snapshot={"event_metadata": {"id": "evt-1"}},
    )
    queryset = WebhookDeliveryAttempt.objects.filter(pk=attempt.pk)

    fake_request = mock.Mock()
    _attempt_admin().requeue_selected(fake_request, queryset)

    mock_task.delay.assert_called_once_with(
        endpoint.pk, attempt.event, {"event_metadata": {"id": "evt-1"}}, str(attempt.correlation_id)
    )


@mock.patch("openedx_webhook_relay.admin.deliver_webhook")
def test_requeue_selected_skips_attempts_without_snapshot(mock_task):
    attempt = WebhookDeliveryAttemptFactory(
        status=WebhookDeliveryAttempt.Status.EXHAUSTED, payload_snapshot=None
    )
    queryset = WebhookDeliveryAttempt.objects.filter(pk=attempt.pk)

    fake_request = mock.Mock()
    _attempt_admin().requeue_selected(fake_request, queryset)

    mock_task.delay.assert_not_called()


@mock.patch("openedx_webhook_relay.admin.deliver_webhook")
def test_requeue_selected_ignores_non_exhausted_attempts(mock_task):
    attempt = WebhookDeliveryAttemptFactory(
        status=WebhookDeliveryAttempt.Status.SUCCEEDED, payload_snapshot={"a": 1}
    )
    queryset = WebhookDeliveryAttempt.objects.filter(pk=attempt.pk)

    fake_request = mock.Mock()
    _attempt_admin().requeue_selected(fake_request, queryset)

    mock_task.delay.assert_not_called()
