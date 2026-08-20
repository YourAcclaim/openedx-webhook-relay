"""
Tests for the shared retention logic in ``retention.py``, used by both the
``purge_old_delivery_attempts`` management command and
``tasks.purge_old_delivery_attempts_task``.
"""

# pylint: disable=missing-function-docstring

from datetime import timedelta

import pytest
from django.utils import timezone

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.retention import purge_old_delivery_attempts, resolve_retention_days
from openedx_webhook_relay.tests.factories import WebhookDeliveryAttemptFactory

pytestmark = pytest.mark.django_db


def _age(attempt, days):
    WebhookDeliveryAttempt.objects.filter(pk=attempt.pk).update(
        created=timezone.now() - timedelta(days=days)
    )


def test_dry_run_counts_but_does_not_delete():
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)

    result = purge_old_delivery_attempts(days=90, dry_run=True)

    assert result.matched == 1
    assert result.deleted == 0
    assert WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()


def test_dry_run_false_actually_deletes():
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)
    recent = WebhookDeliveryAttemptFactory()

    result = purge_old_delivery_attempts(days=90, dry_run=False)

    assert result.matched == 1
    assert result.deleted == 1
    assert not WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()
    assert WebhookDeliveryAttempt.objects.filter(pk=recent.pk).exists()


def test_status_filter_narrows_selection():
    old_succeeded = WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.SUCCEEDED)
    old_exhausted = WebhookDeliveryAttemptFactory(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
    _age(old_succeeded, days=120)
    _age(old_exhausted, days=120)

    result = purge_old_delivery_attempts(days=90, status="exhausted", dry_run=False)

    assert result.deleted == 1
    assert WebhookDeliveryAttempt.objects.filter(pk=old_succeeded.pk).exists()
    assert not WebhookDeliveryAttempt.objects.filter(pk=old_exhausted.pk).exists()


def test_no_matching_rows_returns_zero_without_error():
    recent = WebhookDeliveryAttemptFactory()
    assert recent  # keep it recent (created=now by default)

    result = purge_old_delivery_attempts(days=90, dry_run=False)

    assert result.matched == 0
    assert result.deleted == 0


def test_resolve_retention_days_prefers_explicit_override(settings):
    settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 30
    assert resolve_retention_days(days=7) == 7


def test_resolve_retention_days_falls_back_to_setting(settings):
    settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 30
    assert resolve_retention_days(days=None) == 30


def test_resolve_retention_days_default_is_ninety(settings):
    del settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS
    assert resolve_retention_days(days=None) == 90
