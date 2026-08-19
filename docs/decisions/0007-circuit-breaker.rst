0007. Per-endpoint circuit breaker
#####################################

Status
******

Accepted

Context
*******

Retries (ADR 0002) handle transient failures well but do nothing for a
receiver that's down for an extended period: every matching event still
pays the full retry cost (up to ``max_retries`` attempts each) before
landing in ``EXHAUSTED``. At any real event volume that's a lot of wasted
outbound requests against a host that isn't going to answer.

Decision
********

Each ``WebhookEndpoint`` tracks ``consecutive_failures``. After
``circuit_breaker_failure_threshold`` (default 5) consecutive
``EXHAUSTED`` outcomes, the circuit opens: new deliveries are skipped
outright (recorded as ``WebhookDeliveryAttempt.Status.CIRCUIT_OPEN``, no
HTTP call at all) for ``circuit_breaker_cooldown_seconds`` (default 300).
After the cooldown, exactly one trial delivery is let through
(``HALF_OPEN``); success fully resets the breaker, failure reopens it and
restarts the cooldown.

Implementation note: the breaker check only runs on the *first* attempt of
a delivery chain (``attempt_number == 1``). A half-open trial's own
retries (attempt_number > 1) always proceed regardless of circuit state —
gating those too would mean the trial's own retry sees
``circuit_state=HALF_OPEN`` and blocks itself, wedging the breaker
permanently open with no delivery ever resolving it. Concurrent *new*
deliveries arriving while a trial is in flight are still correctly
skipped, since ``try_acquire_delivery_slot()`` returns ``False`` for any
caller while state is ``HALF_OPEN``, and the OPEN→HALF_OPEN transition
itself is a conditional UPDATE so only one caller ever wins the trial slot.

Consequences
************

* This is per-endpoint, not global — one broken receiver doesn't affect
  delivery to others.
* Circuit breaking is orthogonal to retries: retries still happen (up to
  ``max_retries``) within a single delivery chain before it counts as one
  "consecutive failure" toward the breaker's threshold.
* Can be disabled per endpoint (``circuit_breaker_enabled=False``) for
  receivers where "always try" is preferred to "stop trying after N
  failures" (e.g. very low-volume, high-importance endpoints).
