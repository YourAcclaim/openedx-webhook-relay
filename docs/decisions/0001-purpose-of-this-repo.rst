0001. Purpose of this repo
###########################

Status
******

Accepted

Context
*******

``openedx-webhooks`` (the upstream community plugin) relays ``openedx-events``
signals to external HTTP endpoints but has no request signing, no PII
controls, and logs full payloads. Credly badge issuance needs to consume
Open edX course-passing events without those gaps.

Decision
********

Maintain a small, focused Open edX plugin, ``openedx-webhook-relay``, that:

* Listens only to the events it explicitly supports
  (``COURSE_PASSING_STATUS_UPDATED``, ``CCX_COURSE_PASSING_STATUS_UPDATED``).
* Signs every outbound payload with HMAC-SHA256.
* Lets operators allowlist/denylist which payload fields leave the LMS.
* Delivers asynchronously with retry and a durable delivery audit trail
  (see ADR 0002 and 0003).

This is a minimal, additive approach: a small, standalone LMS-side plugin,
rather than forking or modifying upstream ``openedx-webhooks``.

Consequences
************

* New events require a code change (a new receiver + signal registration),
  not just an admin config change. This is intentional — it keeps the
  event-to-payload mapping reviewable and typed rather than fully dynamic.
* This plugin owns its own Celery queue and delivery bookkeeping; it does
  not depend on or modify ``openedx-webhooks``.
