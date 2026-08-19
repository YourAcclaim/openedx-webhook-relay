"""
Test-only settings overrides, registered via the Open edX plugin
``settings_config`` mechanism (relative_path = settings.test for the
``test`` environment).
"""

from cryptography.fernet import Fernet


def plugin_settings(settings):
    """Ensure tests have a deterministic, valid encryption key."""
    settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
    settings.OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = "test-default-secret"
    settings.CELERY_ALWAYS_EAGER = True
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
