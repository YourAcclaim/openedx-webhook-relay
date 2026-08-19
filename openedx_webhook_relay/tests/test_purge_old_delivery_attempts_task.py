"""
Tests for the scheduled ``purge_old_delivery_attempts_task`` Celery task
(docs/decisions/0010-scheduled-retention-purge.rst).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from openedx_webhook_relay.models import WebhookDeliveryAttempt
from openedx_webhook_relay.tasks import purge_old_delivery_attempts_task
from openedx_webhook_relay.tests.factories import WebhookDeliveryAttemptFactory

pytestmark = pytest.mark.django_db


def _age(attempt, days):
    WebhookDeliveryAttempt.objects.filter(pk=attempt.pk).update(
        created=timezone.now() - timedelta(days=days)
    )


def _run():
    return purge_old_delivery_attempts_task.apply().get()


def test_task_deletes_rows_past_retention(settings):
    settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 90
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)
    recent = WebhookDeliveryAttemptFactory()

    result = _run()

    assert result == {"deleted": 1, "days": 90}
    assert not WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()
    assert WebhookDeliveryAttempt.objects.filter(pk=recent.pk).exists()


def test_task_is_a_noop_when_nothing_is_old_enough():
    recent = WebhookDeliveryAttemptFactory()

    result = _run()

    assert result == {"deleted": 0, "days": 90}
    assert WebhookDeliveryAttempt.objects.filter(pk=recent.pk).exists()


def test_task_skips_entirely_when_auto_purge_disabled(settings):
    settings.OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False
    old = WebhookDeliveryAttemptFactory()
    _age(old, days=120)

    result = _run()

    assert result == {"skipped": True}
    # The kill switch means "don't delete", not "don't exist as a task" —
    # it should never touch rows when disabled, even if invoked directly.
    assert WebhookDeliveryAttempt.objects.filter(pk=old.pk).exists()
