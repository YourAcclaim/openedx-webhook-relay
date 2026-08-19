0008. Structured logging and a metrics/alerting hook
########################################################

Status
******

Accepted

Context
*******

The audit trail (ADR 0004) answers "did this webhook fire?" for a specific
event after the fact, from Django admin. Operationally, teams also want
"page me when deliveries start failing" — which needs either a log
pipeline that can alert on structured fields, or a metrics counter feeding
existing alerting (PagerDuty, Grafana, etc.). Neither should be a hard
dependency of this plugin, since every deployment's observability stack is
different.

Decision
********

* Every log line in ``tasks.py``/``receivers.py`` that reports a delivery
  outcome passes structured context via the stdlib logging ``extra=`` kwarg
  (``correlation_id``, ``endpoint_id``, ``event``, ``status``, ...).
  ``logging_utils.JSONFormatter`` renders any logger's output as one JSON
  object per line, picking up those fields automatically — wire it onto
  this plugin's logger namespace if your log pipeline indexes on JSON.
* ``metrics.emit_delivery_metric()`` is called for every recorded
  ``WebhookDeliveryAttempt`` (including CIRCUIT_OPEN and SKIPPED). It
  always fires a plain Django signal, ``webhook_delivery_recorded``, with
  the same fields — any app can connect a receiver without this plugin
  depending on a specific metrics library. It additionally increments a
  counter via ``OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT`` if that setting is
  configured with an object implementing ``.incr(name)``.
* Both hooks are wrapped in broad ``try/except`` — a misbehaving log
  formatter or metrics client must never break webhook delivery.

Consequences
************

* No metrics are emitted anywhere by default; a deployment gets nothing
  until it either wires the JSON formatter into ``LOGGING``, connects a
  ``webhook_delivery_recorded`` receiver, or sets
  ``OPENEDX_WEBHOOK_RELAY_STATSD_CLIENT``. This is intentional — we'd
  rather be explicit than silently assume a specific stack.
* The signal is a reasonable place to plug in what ADR 0006/0007 don't
  cover: e.g. a receiver that pages on-call specifically on
  ``status="exhausted"``, or one that tracks per-endpoint error rates for
  a dashboard, without touching this plugin's code.
