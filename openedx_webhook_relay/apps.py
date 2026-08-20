"""
Django app configuration for openedx_webhook_relay.
"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Events this plugin knows how to relay. Extend as needed; each entry must
# have a matching receiver in receivers.py and signal path recognized by
# openedx-events.
SUPPORTED_EVENTS = [
    "COURSE_PASSING_STATUS_UPDATED",
    "CCX_COURSE_PASSING_STATUS_UPDATED",
]


class OpenedxWebhookRelayConfig(AppConfig):
    """Open edX plugin: asynchronous, signed, audited webhook relay."""

    name = "openedx_webhook_relay"
    verbose_name = "Open edX Webhook Relay"
    default_auto_field = "django.db.models.BigAutoField"

    plugin_app = {
        "settings_config": {
            "lms.djangoapp": {
                "common": {"relative_path": "settings.common"},
                "test": {"relative_path": "settings.test"},
            },
        },
        "signals_config": {
            "lms.djangoapp": {
                "relative_path": "receivers",
                "receivers": [
                    {
                        "receiver_func_name": "course_passing_status_updated_receiver",
                        "signal_path": (
                            "openedx_events.learning.signals.COURSE_PASSING_STATUS_UPDATED"
                        ),
                    },
                    {
                        "receiver_func_name": "ccx_course_passing_status_updated_receiver",
                        "signal_path": (
                            "openedx_events.learning.signals.CCX_COURSE_PASSING_STATUS_UPDATED"
                        ),
                    },
                ],
            },
        },
    }

    def ready(self):
        # Import here (not at module load) so Celery task registration only
        # happens once Django apps are fully loaded, avoiding AppRegistryNotReady.
        # pylint: disable=unused-import,import-outside-toplevel
        from django.core.checks import register

        from openedx_webhook_relay import tasks  # noqa: F401
        from openedx_webhook_relay.checks import check_encryption_key

        # Surfaces a missing/malformed encryption key at deploy time rather than
        # as a 500 the first time someone saves an endpoint.
        register(check_encryption_key)

        logger.info(
            "openedx_webhook_relay ready: relaying %s via async delivery",
            ", ".join(SUPPORTED_EVENTS),
        )
