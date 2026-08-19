"""
openedx-webhook-relay
======================

Open edX plugin that relays selected ``openedx-events`` signals to
external HTTPS endpoints as signed JSON webhooks.

Originally built for Credly badge integrations; kept event-agnostic so any
consumer can register a :class:`~openedx_webhook_relay.models.WebhookEndpoint`.
"""

__version__ = "1.3.0"
