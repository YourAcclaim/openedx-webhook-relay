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

How a delivery flows
*********************

The split between processes is the thing worth understanding: the receiver runs
inline on whatever thread emitted the signal, and every network call happens
later, in a Celery worker.

::

  COURSE_PASSING_STATUS_UPDATED          (openedx-events signal)
             |
             v
  +-- lms or lms-worker ------------------------------------+
  |  receivers.py, synchronous on the emitting thread       |
  |    - one indexed query for enabled endpoints            |
  |    - enqueue one task per match; no network I/O here    |
  +---------------------------+-----------------------------+
                              |  deliver_webhook.delay(...)
                              |  one correlation_id per event
                              v
                    Celery broker (Redis)
                              |
                              v
  +-- lms-worker -------------------------------------------+
  |  tasks.deliver_webhook                                  |
  |    1. circuit breaker: skip while open                  |
  |    2. only_on_passing filter                            |
  |    3. PII allowlist / denylist                          |
  |    4. HMAC-SHA256 sign -> X-OpenEdX-Webhook-Signature   |
  |    5. POST with timeout; retry 5xx/429/network          |
  |    6. write WebhookDeliveryAttempt + metrics signal     |
  +---------------------------+-----------------------------+
                              v
                  your receiver (https only)

Two consequences of that shape:

* **No Celery worker means nothing is ever delivered.** The receiver still
  enqueues successfully and logs that it did, so the LMS looks healthy.
* **A successful delivery logs nothing.** Only failures, skips and exhaustion
  are logged; the audit trail (``WebhookDeliveryAttempt``) is the record of
  success. Follow one event across its retries by its ``correlation_id``.

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

In this order. The encryption key has to be working *before* you create an
endpoint, because storing the first signing secret is what first needs it.

**1. Generate and set the encryption key.** A Fernet key is 32 random bytes,
urlsafe-base64 encoded, so no Python is required::

  tutor config save --set OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"

**2. Add a Tutor plugin to inject the setting.** ``settings/common.py``
declares the setting but deliberately supplies no value, so something has to
provide it. ``scripts/tutor_plugin_example.py`` in this repository is a
documented copy; if you are installing from a package rather than a checkout,
write it directly. The ``mkdir -p`` matters — without it the redirect fails
silently::

  mkdir -p "$(tutor plugins printroot)"
  cat > "$(tutor plugins printroot)/openedx-webhook-relay.py" <<'EOF'
  from tutor import hooks

  hooks.Filters.ENV_PATCHES.add_item(
      (
          "openedx-lms-common-settings",
          '''
  OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "{{ OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY }}"
  ''',
      )
  )

  hooks.Filters.CONFIG_DEFAULTS.add_items(
      [
          ("OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY", ""),
      ]
  )
  EOF

  tutor plugins list | grep webhook-relay      # must appear before enabling
  tutor plugins enable openedx-webhook-relay
  tutor config save

**3. Confirm the setting actually rendered.** Do not skip this. A key present
in ``config.yml`` but missing from the rendered settings is the most common
failure, and it surfaces much later as a 500 when saving an endpoint::

  grep -rn OPENEDX_WEBHOOK_RELAY "$(tutor config printroot)/env/apps/openedx/settings/lms/"

Empty output means the plugin is not being applied — revisit step 2.

**4. Add the package**::

  tutor config save \
    --append OPENEDX_EXTRA_PIP_REQUIREMENTS='git+https://github.com/YourAcclaim/openedx-webhook-relay.git@v1.3.0'

**5. Build, then reboot.** Use ``reboot``, not ``restart``:
``docker compose restart`` reuses the existing container and its old image, and
the symptom is ``No installed app with label 'openedx_webhook_relay'``::

  tutor images build openedx
  tutor local reboot

**6. Migrate**::

  tutor local exec lms ./manage.py lms migrate openedx_webhook_relay

**7. Verify.** There is no startup validation of the key, so check it here
rather than discovering it in admin::

  tutor local exec lms ./manage.py lms shell -c "
  from django.conf import settings
  print('key  :', repr(settings.OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY))
  from openedx_webhook_relay.fields import _get_fernet; _get_fernet(); print('fernet: valid')
  from openedx_webhook_relay.tasks import deliver_webhook; print('task :', deliver_webhook.name)
  "

Installing from a private repository
*************************************

``git+https://`` needs credentials the image build does not have. Either embed
a fine-grained read-only token in the URL — noting it is then written into
``config.yml`` *and* the image layer history, so revoke it afterwards — or build
a wheel and install it from a URL the build can reach::

  python -m build --wheel
  # upload the wheel, then reference it directly:
  tutor config save --append OPENEDX_EXTRA_PIP_REQUIREMENTS='https://example.com/openedx_webhook_relay-1.3.0-py3-none-any.whl'

A host path does not work: ``./openedx-webhook-relay`` resolves to ``/`` inside
the container, ``/openedx/requirements/`` does not exist in Tutor v22, and
``tutor mounts add`` on an arbitrary directory passes a build context that no
``COPY`` instruction consumes. All three fail with *Distribution not found*.

Required Django settings
*************************

::

  # MUST be set from a secrets manager in deployment config — never commit
  # this. Generate with:
  #   openssl rand -base64 32 | tr '+/' '-_'
  OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "<fernet-key>"

This is the only setting with no usable default; everything below falls back to
a sensible value. ``manage.py check`` fails if it is missing or malformed.

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

  # Key files keep the keys out of shell history and process listings.
  ./manage.py rotate_encryption_key --old-key-file=old.key --new-key-file=new.key --dry-run
  ./manage.py rotate_encryption_key --old-key-file=old.key --new-key-file=new.key
  # then update OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY to the new key and redeploy
  # promptly: until you do, the rows are encrypted with a key the running
  # process does not have.

Audit trail retention is registered on Celery beat (daily, 03:17 UTC by
default — see ADR 0010). **A beat scheduler has to be running for that to
happen.** Tutor ships no beat container, so on a stock Tutor deployment the
schedule entry exists, nothing reads it, and the table grows without limit.
Either add a beat service, or set
``OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False`` and schedule it yourself::

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
