Change Log
##########

..
   All enhancements and patches to openedx-webhook-relay will be
   documented in this file. It adheres to the structure of
   https://keepachangelog.com, but in reStructuredText instead of
   Markdown, per Open edX conventions.

Unreleased
**********

1.2.0 - 2026-08-18
*******************

Added
=====

* Automatic, scheduled audit trail retention: ``tasks.purge_old_delivery_attempts_task``
  runs on Celery beat by default (03:17 UTC, configurable via
  ``OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR``/``_MINUTE``), so deployments
  no longer need to remember to schedule ``purge_old_delivery_attempts``
  themselves. Disable with ``OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False``
  to manage retention your own way (ADR 0010). The beat registration merges
  into any existing ``CELERY_BEAT_SCHEDULE`` rather than replacing it.
* Retention logic factored into ``retention.py`` (``purge_old_delivery_attempts()``),
  shared by the management command and the new scheduled task so they can't
  drift out of sync.

1.1.0 - 2026-08-18
*******************

Added
=====

* Per-endpoint circuit breaker: after ``circuit_breaker_failure_threshold``
  consecutive exhausted deliveries, an endpoint stops being hit for
  ``circuit_breaker_cooldown_seconds``, then gets one half-open trial
  delivery before fully reopening (ADR 0007).
* Signing secret rotation: ``WebhookEndpoint.signing_secret_previous``,
  dual ``X-OpenEdX-Webhook-Signature[-Previous]`` headers during rotation,
  admin rotation checkboxes, and a ``rotate_signing_secret`` management
  command (ADR 0005).
* Pluggable secret backend (``secrets_backend.py``): default encrypted
  database storage, or an opt-in AWS Secrets Manager backend selected via
  ``OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND`` (ADR 0009).
* ``rotate_encryption_key`` management command to re-encrypt all stored
  secrets under a new ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY`` (ADR 0009).
* ``purge_old_delivery_attempts`` management command for audit trail
  retention, defaulting to ``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS``.
* Opt-in payload snapshots (``WebhookEndpoint.retain_payload_snapshot``,
  ``WebhookDeliveryAttempt.payload_snapshot``) and a "Requeue selected"
  Django admin bulk action for one-click redelivery of exhausted,
  snapshotted attempts (ADR 0006). ``redeliver_failed_webhooks`` also uses
  snapshots when present, making ``--payload-file`` optional for those rows.
* ``logging_utils.JSONFormatter`` for structured, JSON-per-line logging of
  delivery outcomes, and ``metrics.emit_delivery_metric`` /
  ``webhook_delivery_recorded`` Django signal for external
  alerting/metrics integration (ADR 0008).
* New ``WebhookDeliveryAttempt.Status.CIRCUIT_OPEN`` for attempts skipped
  by the circuit breaker.
* ``scripts/generate_encryption_key.sh`` and ``scripts/tutor_plugin_example.py``
  — a key-generation helper and a documented reference Tutor plugin for
  wiring ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY`` in from deployment config.
* Tests for all of the above (secrets backend incl. a faked AWS Secrets
  Manager client, circuit breaker state machine incl. the half-open
  self-retry deadlock scenario, dual-signature headers, payload snapshots,
  metrics signal, structured logging, and all three new management
  commands).

Fixed
=====

* The PII allowlist/denylist logic produced a duplicate nested key for
  bare ``"event_metadata"``/``"data"`` allowlist entries (e.g.
  ``result["event_metadata"]["event_metadata"] = {...}`` instead of
  ``result["event_metadata"] = {...}``), found via manual execution while
  the full Django test suite wasn't runnable in the build sandbox.
  Rewrote ``apply_allowlist``/``apply_denylist`` with explicit
  per-form-of-path branches instead of the previous container/relative-path
  resolution, removing the bug and the need for the old ``_resolve_path``
  helper.

1.0.0 - 2026-08-18
*******************

Initial release. An Open edX plugin for signed, asynchronous, audited
webhook delivery, covering:

Added
=====

* Asynchronous delivery via a Celery task (``tasks.deliver_webhook``);
  signal receivers never perform HTTP I/O.
* Exponential-backoff retries per endpoint (``WebhookEndpoint.max_retries``)
  and a database-backed dead-letter record (``WebhookDeliveryAttempt``
  with ``status=EXHAUSTED``) plus a ``redeliver_failed_webhooks``
  management command.
* Encryption at rest for ``signing_secret`` (``EncryptedCharField``,
  Fernet) and a write-only, masked Django admin form.
* ``WebhookDeliveryAttempt`` audit trail model and read-only admin view
  (filters, search, date hierarchy) for full delivery visibility.
* HMAC-SHA256 request signing, PII allowlist/denylist filtering, and a
  pass-only filter for course-passing events.
* Standard Open edX repo scaffolding: CI workflow, pinned requirement
  tiers, pre-commit hooks, pylintrc, tox, CODEOWNERS, architecture
  decision records, this changelog.
* Test suite covering models, encrypted fields, security/serialization
  helpers, Celery task retry/exhaustion paths, receivers, admin masking,
  and the management command (≥90% coverage gate in CI).
