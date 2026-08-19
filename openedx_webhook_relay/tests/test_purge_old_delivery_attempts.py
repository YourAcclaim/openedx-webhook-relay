"""Tests for the purge_old_delivery_attempts management command."""

# pylint: disable=missing-function-docstring

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.tests.factories import WebhookDeliveryAttemptFactory

pytestmark = pytest.mark.django_db


def _age(attempt, days):
    WebhookDeliveryAttempt.objects.filter(pk=attempt.pk).update(
        created=timezone.now() - timedelta(days=days)
    )


def test_dry_run_reports_count_without_deleting():
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)

    out = StringIO()
    call_command("purge_old_delivery_attempts", days=90, stdout=out)

    assert "Would delete 1" in out.getvalue()
    assert WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()


def test_yes_flag_actually_deletes():
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)
    recent = WebhookDeliveryAttemptFactory()

    out = StringIO()
    call_command("purge_old_delivery_attempts", days=90, yes=True, stdout=out)

    assert "Deleted 1" in out.getvalue()
    assert not WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()
    assert WebhookDeliveryAttempt.objects.filter(pk=recent.pk).exists()


def test_no_matching_rows_reports_success():
    recent = WebhookDeliveryAttemptFactory()
    assert recent  # keep it recent (created=now by default)

    out = StringIO()
    call_command("purge_old_delivery_attempts", days=90, yes=True, stdout=out)
    assert "No delivery attempts older than 90 days" in out.getvalue()


def test_status_filter_narrows_selection():
    old_succeeded = WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.SUCCEEDED)
    old_exhausted = WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
    _age(old_succeeded, days=120)
    _age(old_exhausted, days=120)

    out = StringIO()
    call_command(
        "purge_old_delivery_attempts", days=90, status="exhausted", yes=True, stdout=out
    )

    assert "Deleted 1" in out.getvalue()
    assert WebhookDeliveryAttempt.objects.filter(pk=old_succeeded.pk).exists()
    assert not WebhookDeliveryAttempt.objects.filter(pk=old_exhausted.pk).exists()


def test_defaults_to_configured_retention_setting(settings):
    settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 30
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=45)

    out = StringIO()
    call_command("purge_old_delivery_attempts", yes=True, stdout=out)

    assert "Deleted 1" in out.getvalue()
