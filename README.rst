openedx-webhook-relay
######################

A signed, asynchronous webhook relay for Open edX events, built for
Credly badge integrations but usable for any ``openedx-events`` consumer.

Purpose
*******

This plugin listens to selected ``openedx-events`` signals and delivers
**signed JSON** to external HTTPS endpoints, with retries, a circuit
breaker, a delivery audit trail, and encrypted-at-rest (or externally
managed) secrets. See ``docs/decisions/`` for the reasoning behind each
design choice.

Features
********

* **Asynchronous delivery** — signal receivers never make HTTP calls; a
  Celery task (``tasks.deliver_webhook``) does, off the request/signal path.
* **Retries with backoff** — configurable per endpoint
  (``max_retries``, ``timeout_seconds``), exponential backoff with a
  configurable ceiling.
* **Circuit breaker** — an endpoint that fails ``circuit_breaker_failure_threshold``
  times in a row stops being hit at all for ``circuit_breaker_cooldown_seconds``,
  then gets a single half-open trial before fully reopening delivery.
* **Delivery audit trail** — every attempt (success, retry, exhaustion,
  filtered/skipped, or circuit-open) is recorded in ``WebhookDeliveryAttempt``
  and visible in a read-only Django admin view with filters and a date hierarchy.
* **Dead-letter handling** — exhausted deliveries are queryable and can be
  redelivered via ``./manage.py redeliver_failed_webhooks`` or, for endpoints
  that opted in to payload snapshots, a one-click "Requeue selected" admin action.
* **Secrets encrypted at rest by default**, with a pluggable backend
  (``secrets_backend.py``) to move secrets into AWS Secrets Manager instead.
* **Signing secret rotation** — a "previous secret" window so receivers
  aren't forced to cut over to a new HMAC secret atomically.
* **Encryption key rotation** — ``rotate_encryption_key`` re-encrypts every
  stored secret under a new Fernet key.
* **Audit trail retention, scheduled by default** — ``purge_old_delivery_attempts``
  enforces a configurable retention window, and runs automatically on Celery
  beat (no cron setup required) unless disabled.
* **Structured logging + metrics hook** — JSON-formattable log lines and a
  ``webhook_delivery_recorded`` signal for external alerting.
* **HMAC-SHA256 signing** via ``X-OpenEdX-Webhook-Signature: sha256=...``.
* **PII allowlist / denylist** — control which payload fields leave the LMS.
* **Pass-only filter** — skip events where ``is_passing`` is false.

Supported events
*****************

* ``COURSE_PASSING_STATUS_UPDATED``
* ``CCX_COURSE_PASSING_STATUS_UPDATED``

(See CONTRIBUTING.rst for how to add another event.)

Installation (Tutor)
*********************

::

  tutor config save \
    --append OPENEDX_EXTRA_PIP_REQUIREMENTS='git+https://github.com/YourAcclaim/openedx-webhook-relay.git@v1.2.0'

Or for local development::

  tutor config save \
    --append OPENEDX_EXTRA_PIP_REQUIREMENTS='/path/to/openedx-webhook-relay'

Then rebuild and migrate::

  tutor images build openedx
  tutor local restart
  tutor local exec lms ./manage.py lms migrate openedx_webhook_relay

Required Django settings
*************************

::

  # MUST be set from a secrets manager in deployment config — never commit
  # this. Generate with:
  #   ./scripts/generate_encryption_key.sh
  OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "<fernet-key>"

See ``scripts/tutor_plugin_example.py`` for a documented reference Tutor
plugin that wires this setting in from Tutor config instead of hardcoding it.

Optional Django settings
**************************

::

  OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET = ""                          # fallback signing secret
  OPENEDX_WEBHOOK_RELAY_SIGNATURE_HEADER = "X-OpenEdX-Webhook-Signature"
  OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_SECONDS = 30
  OPENEDX_WEBHOOK_RELAY_RETRY_BACKOFF_MAX_SECONDS = 900
  OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS = 90

  # Retention purge is scheduled on Celery beat automatically (ADR 0010).
  # Set the first to False to manage retention yourself (own cron/beat entry,
  # or none at all) instead of relying on this plugin's default schedule.
  OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = True
  OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR = 3      # UTC hour, 0-23
  OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_MINUTE = 17

  # Secret storage backend. "database" (default) or "aws_secrets_manager"
  # (requires `pip install -r requirements/aws.in`). See secrets_backend.py.
  OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND = "database"
  OPENEDX_WEBHOOK_RELAY_AWS_SECRET_NAME_PREFIX = "openedx-webhook-relay"
  OPENEDX_WEBHOOK_RELAY_AWS_SECRETS_MANAGER_KWARGS = {}   # extra boto3.client() kwargs

  # Any object with .incr(name) — statsd, dogstatsd, edx-platform's
  # dogstats_wrapper, or a test double. None disables the counter (the
  # webhook_delivery_recorded Django signal always fires regardless).
  OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT = None

Admin configuration
********************

1. Open **LMS Django admin** → **openedx_webhook_relay** → **Webhook endpoints**.
2. Add an endpoint:

   * **Event:** ``COURSE_PASSING_STATUS_UPDATED``
   * **Webhook URL:** ``https://your-receiver.example/webhook/course-passing-status``
   * **Signing secret:** shared with your receiver (write-only; not shown again)
   * **Only on passing:** enabled (recommended)
   * **PII allowlist** (Credly example)::

       [
         "data.user.pii.email",
         "data.user.pii.username",
         "data.is_passing",
         "data.course.course_key",
         "event_metadata"
       ]

3. Optionally tune **Circuit breaker** (failure threshold / cooldown) and
   **Delivery → Retain payload snapshot** (enables one-click admin
   redelivery for this endpoint, at the cost of retaining PII-filtered
   payloads on failed attempts — see ADR 0006).
4. Check delivery history under **Webhook delivery attempts** — filter by
   status, endpoint, or event; search by correlation ID; use **Requeue
   selected** on exhausted rows that have a stored snapshot.

Receiver verification
**********************

Verify the signature on your webhook receiver. During a secret rotation
(see below), a request may carry a second header,
``X-OpenEdX-Webhook-Signature-Previous`` — accept either::

  import hmac, hashlib

  def verify(body: bytes, secret: str, header_value: str) -> bool:
      expected = "sha256=" + hmac.new(
          secret.encode(), body, hashlib.sha256
      ).hexdigest()
      return hmac.compare_digest(expected, header_value or "")

Operating
*********

Redeliver stuck webhooks::

  # See what's stuck
  ./manage.py redeliver_failed_webhooks

  # Redeliver a specific correlation ID once the receiver is fixed. If the
  # endpoint has retain_payload_snapshot=True, --payload-file can be omitted.
  ./manage.py redeliver_failed_webhooks --correlation-id=<uuid> \
      --requeue --payload-file=/tmp/captured-payload.json

Rotate a signing secret without an atomic cutover::

  ./manage.py rotate_signing_secret --endpoint-id=3 --new-secret-file=/path/to/new-secret.txt
  # ... once the receiver confirms it validated with the new secret ...
  ./manage.py rotate_signing_secret --endpoint-id=3 --clear-previous

Rotate the database encryption key::

  ./manage.py rotate_encryption_key --old-key="$(cat old.key)" --new-key="$(cat new.key)"
  # then update OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY to the new key and redeploy

Audit trail retention runs automatically via Celery beat (daily, 03:17 UTC
by default — see ADR 0010). Run it manually for a one-off cleanup or a
non-default window::

  ./manage.py purge_old_delivery_attempts --days=90 --yes

Development
************

::

  pip install -r requirements/dev.txt
  pre-commit install
  make test-quality

See CONTRIBUTING.rst for details and docs/decisions/ for the architecture
decision records behind this design.

Suggested improvements (not yet implemented)
***********************************************

* **Automated secret-backend migration** — a management command to move
  existing secrets from the database backend into an external one (and
  back), rather than a manual one-off script.
* **Admin action to force-close/open a circuit breaker** — currently only
  automatic (threshold/cooldown-driven); an operator override would help
  during a known, already-fixed outage.
* **Per-endpoint delivery rate limiting** — independent from the circuit
  breaker, for receivers that need a maximum requests/second regardless of
  failure state.
* **OpenTelemetry tracing** — the metrics hook (ADR 0008) covers
  counters/signals; distributed tracing spans around delivery attempts
  would help correlate with receiver-side traces.

License
********

AGPL-3.0-or-later
