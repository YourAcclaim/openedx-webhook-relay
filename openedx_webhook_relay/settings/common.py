"""
Common settings for openedx_webhook_relay, registered via the Open edX
plugin ``settings_config`` mechanism.
"""

from celery.schedules import crontab


def plugin_settings(settings):
    """Register plugin defaults on the Django settings object."""

    # Fernet key used to encrypt/decrypt WebhookEndpoint.signing_secret.
    # MUST be set explicitly in deployment config (Tutor plugin / ansible
    # vars) from a secrets manager — there is deliberately no baked-in
    # default, so forgetting to set it fails loudly instead of silently
    # storing secrets with a well-known key.
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY", None
    )

    settings.OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET", ""
    )
    settings.OPENEDX_WEBHOOK_RELAY_SIGNATURE_HEADER = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_SIGNATURE_HEADER", "X-OpenEdX-Webhook-Signature"
    )
    settings.OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_SECONDS = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_SECONDS", 30
    )
    settings.OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_MAX_SECONDS = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_MAX_SECONDS", 900
    )

    # Where signing secrets are stored: "database" (default, encrypted
    # locally) or "aws_secrets_manager" (requires boto3; see secrets_backend.py).
    settings.OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND", "database"
    )
    settings.OPENEDX_WEBHOOK_RELAY_AWS_SECRET_NAME_PREFIX = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_AWS_SECRET_NAME_PREFIX", "openedx-webhook-relay"
    )
    settings.OPENEDX_WEBHOOK_RELAY_AWS_SECRETS_MANAGER_KWARGS = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_AWS_SECRETS_MANAGER_KWARGS", {}
    )

    # Optional statsd-like client (must implement .incr(name)) for the
    # metrics hook in metrics.py. None means metrics are only emitted via
    # the webhook_delivery_recorded Django signal.
    settings.OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT", None
    )

    # Audit trail retention window, in days, used by both the
    # purge_old_delivery_attempts management command's default (can be
    # overridden per-invocation with --days) and the scheduled Celery task
    # below.
    settings.OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS", 90
    )

    # Whether to register the automatic retention purge on Celery beat (see
    # docs/decisions/0010-scheduled-retention-purge.rst). Set to False if
    # your deployment wants to schedule ``purge_old_delivery_attempts``
    # itself (a different cron, a different tool, or no purge at all).
    settings.OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED", True
    )
    # Off-peak, deliberately non-round time so this doesn't line up with
    # every other plugin's on-the-hour cron entry.
    settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR", 3
    )
    settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_MINUTE = getattr(
        settings, "OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_MINUTE", 17
    )

    if settings.OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED:
        # Merge into whatever CELERY_BEAT_SCHEDULE edx-platform or other
        # plugins already populated, rather than overwriting it — several
        # plugins register beat entries this way and stomping on the dict
        # would silently drop unrelated scheduled tasks.
        beat_schedule = dict(getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {})
        beat_schedule["openedx-webhook-relay-purge-old-delivery-attempts"] = {
            "task": "openedx_webhook_relay.tasks.purge_old_delivery_attempts_task",
            "schedule": crontab(
                hour=settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR,
                minute=settings.OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_MINUTE,
            ),
        }
        settings.CELERY_BEAT_SCHEDULE = beat_schedule
