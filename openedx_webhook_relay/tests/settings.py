"""
Standalone Django settings for running this plugin's test suite without a
full edx-platform checkout.

In a real LMS deployment, ``settings/common.py`` and ``settings/test.py``
are wired in automatically by Open edX's plugin settings mechanism
(``plugin_app["settings_config"]`` in apps.py). Standalone unit tests don't
go through that mechanism, so this module sets the equivalent values
directly.
"""

from cryptography.fernet import Fernet

SECRET_KEY = "test-secret-key-not-for-production"  # noqa: S105

DEBUG = True
USE_TZ = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "openedx_webhook_relay",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ROOT_URLCONF = "openedx_webhook_relay.tests.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- openedx_webhook_relay settings (normally set by settings/common.py) ---
OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = "test-default-secret"
OPENEDX_WEBHOOK_RELAY_SIGNATURE_HEADER = "X-OpenEdX-Webhook-Signature"
OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_SECONDS = 1
OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_MAX_SECONDS = 5
OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "database"
OPENEDX_WEBHOOK_RELAY_AWS_SECRET_NAME_PREFIX = "openedx-webhook-relay-test"
OPENEDX_WEBHOOK_RELAY_AWS_SECRETS_MANAGER_KWARGS = {}
OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT = None
OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 90

# --- Celery: run tasks synchronously/inline in tests ---
CELERY_ALWAYS_EAGER = True
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
