"""
Tests for openedx_webhook_relay/settings/common.py's Celery beat schedule
wiring (docs/decisions/0010-scheduled-retention-purge.rst).
"""

from celery.schedules import crontab

from openedx_webhook_relay.settings.common import plugin_settings


def test_registers_beat_entry_by_default(settings):
    # tests/settings.py doesn't define CELERY_BEAT_SCHEDULE at all — this
    # mirrors a fresh LMS settings module that has no other plugin
    # registering beat entries yet.
    plugin_settings(settings)

    entry = settings.CELERY_BEAT_SCHEDULE["openedx-webhook-relay-purge-old-delivery-attempts"]
    assert entry["task"] == "openedx_webhook_relay.tasks.purge_old_delivery_attempts_task"
    assert entry["schedule"] == crontab(hour=3, minute=17)


def test_preserves_existing_unrelated_beat_entries(settings):
    settings.CELERY_BEAT_SCHEDULE = {
        "some-other-plugins-task": {"task": "other.tasks.thing", "schedule": crontab(minute=0)},
    }

    plugin_settings(settings)

    assert "some-other-plugins-task" in settings.CELERY_BEAT_SCHEDULE
    assert "openedx-webhook-relay-purge-old-delivery-attempts" in settings.CELERY_BEAT_SCHEDULE


def test_auto_purge_disabled_registers_no_beat_entry():
    class FakeSettings:
        OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False

    fake_settings = FakeSettings()
    plugin_settings(fake_settings)

    assert not hasattr(fake_settings, "CELERY_BEAT_SCHEDULE")


def test_schedule_hour_and_minute_are_configurable(settings):
    settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR = 5
    settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_MINUTE = 45

    plugin_settings(settings)

    entry = settings.CELERY_BEAT_SCHEDULE["openedx-webhook-relay-purge-old-delivery-attempts"]
    assert entry["schedule"] == crontab(hour=5, minute=45)
