0002. Asynchronous delivery, retries, and dead-lettering
##########################################################

Status
******

Accepted

Context
*******

Calling ``requests.post()`` directly inside a Django signal receiver would
run it on whatever process/thread raised the openedx-events signal (an LMS
request, or the tasks worker processing course grading). A slow or
unreachable receiver endpoint would then add its own network latency — or
a hang — directly onto that critical path, and a single failed POST would
silently drop the event with nothing but a log line.

Decision
********

* Signal receivers (``receivers.py``) do the minimum possible synchronous
  work: one indexed query for enabled endpoints matching the event, then
  ``deliver_webhook.delay(...)`` per match. No network I/O happens on the
  signal-sending thread.
* ``tasks.deliver_webhook`` is a Celery task that performs the HTTP POST,
  with exponential backoff (30s, 60s, 120s, ... capped at 15 minutes by
  default, both configurable) up to the destination's
  ``WebhookEndpoint.max_retries``.
* Retries apply to connection errors, timeouts, 5xx, and 429. 4xx (other
  than 429) is treated as non-retryable — it usually means a
  misconfigured URL or a rejected signature, which a retry won't fix.
* When retries are exhausted, we do **not** use a broker-native dead-letter
  queue (e.g. a second Celery/RabbitMQ queue). Open edX's default Celery
  setup varies a lot by deployment (Tutor vs. native, single broker vs.
  per-queue), and a DLQ that only some deployments have configured is
  worse than no DLQ. Instead, exhaustion is recorded as a
  ``WebhookDeliveryAttempt`` row with ``status=EXHAUSTED`` — a
  database-backed, always-available equivalent that every deployment gets
  "for free", and is directly queryable/actionable from Django admin (see
  ADR 0004) or ``./manage.py redeliver_failed_webhooks``.

Consequences
************

* Delivery latency increases slightly (an extra queue hop) compared to the
  old synchronous call — acceptable given the reliability gain.
* This plugin requires Celery to be configured in the deployment (already
  true for any real Open edX install; ``CELERY_ALWAYS_EAGER`` is set in
  ``settings.test`` so unit tests run without a broker).
* Manual redelivery requires the operator to supply the original payload
  (see ADR 0003 for why we don't retain it) or to have the upstream system
  re-emit the event.
