0006. Admin bulk redeliver and the opt-in payload snapshot
##############################################################

Status
******

Accepted

Context
*******

ADR 0004 deliberately excludes the outbound payload from
``WebhookDeliveryAttempt`` to avoid retaining PII in a long-lived, broadly
readable audit table. That's the right default, but it means the only way
to redeliver an exhausted attempt is ``redeliver_failed_webhooks
--payload-file=...`` with a payload supplied out-of-band — there's no
"just click a button in admin" path, which is a real operational gap for
teams who page on ``EXHAUSTED`` and want a fast one-click recovery.

Decision
********

Add ``WebhookEndpoint.retain_payload_snapshot`` (default ``False``). When
enabled for a given endpoint, ``WebhookDeliveryAttempt.payload_snapshot``
is populated — but *only* when the attempt's final status is
``EXHAUSTED``, and only with the already PII-filtered (allowlist/denylist
applied) payload, never the raw one. Successful and retrying attempts
never get a snapshot, which bounds how much data this feature can
accumulate to "deliveries that already failed enough to need a human".

With a snapshot present, ``WebhookDeliveryAttemptAdmin`` exposes a
"Requeue selected" bulk action that redelivers directly from admin. Without
retention enabled, the action reports how many selected rows it had to
skip and points at the CLI command instead.

Consequences
************

* This is an explicit, per-endpoint, off-by-default trade-off between
  operational convenience and PII minimization — the operator configuring
  the endpoint is the one deciding whether it applies, not this plugin.
* ``payload_snapshot`` rows still accumulate PII for as long as the
  ``WebhookDeliveryAttempt`` row exists; pair ``retain_payload_snapshot``
  with a shorter retention window via
  ``purge_old_delivery_attempts --status=exhausted`` if this matters for
  your data retention policy.
