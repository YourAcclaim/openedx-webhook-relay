"""Test factories."""

# pylint: disable=missing-class-docstring

import factory

from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint


class WebhookEndpointFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WebhookEndpoint

    description = factory.Sequence(lambda n: f"Test endpoint {n}")
    event = "COURSE_PASSING_STATUS_UPDATED"
    webhook_url = "https://receiver.example.com/webhook"
    enabled = True
    signing_secret = "endpoint-specific-secret"
    only_on_passing = True
    timeout_seconds = 5
    max_retries = 3


class WebhookDeliveryAttemptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WebhookDeliveryAttempt

    endpoint = factory.SubFactory(WebhookEndpointFactory)
    event = "COURSE_PASSING_STATUS_UPDATED"
    attempt_number = 1
    status = WebhookDeliveryAttempt.Status.SUCCEEDED
    http_status_code = 200
