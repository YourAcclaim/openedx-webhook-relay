"""Shared pytest fixtures."""

import pytest
from celery import current_app

# Ensure the default Celery app used by @shared_task runs tasks inline,
# synchronously, and re-raises exceptions — matching production behavior
# closely enough for retry/exhaustion assertions while requiring no broker.
current_app.conf.task_always_eager = True
current_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def _celery_eager():
    current_app.conf.task_always_eager = True
    current_app.conf.task_eager_propagates = True
    yield


@pytest.fixture
def webhook_endpoint(db):  # pylint: disable=unused-argument
    from openedx_webhook_relay.tests.factories import WebhookEndpointFactory

    return WebhookEndpointFactory()
