0004. Delivery visibility and audit trail
############################################

Status
******

Accepted

Context
*******

Logging delivery outcomes only to the application log (``logger.info`` /
``logger.exception``) gives operators no way to answer "did this webhook
actually fire for this learner?" without grepping production logs across
however many worker processes handled it, and logs are typically rotated
out within days.

Decision
********

Every delivery attempt — success, retry, exhaustion, or filtered/skipped —
writes a ``WebhookDeliveryAttempt`` row: endpoint, event, a
``correlation_id`` shared by every attempt of the same triggering event,
attempt number, status, HTTP status code, duration, a truncated error
message, and a payload fingerprint (see below). This is registered as a
read-only Django admin view with filters by status/event/endpoint and a
date hierarchy, so support staff can search without shell/DB access.

We do **not** store the full outbound payload in the audit trail. It may
contain PII (the same PII the allow/deny lists exist to control), and an
audit log is exactly the kind of long-retention, broadly-readable table
that PII should not accumulate in by accident. ``payload_fingerprint`` (a
truncated SHA-256 of the shaped payload) lets an operator confirm "this
retry sent the identical payload" without retaining the content.

Consequences
************

* Manual redelivery of an exhausted attempt needs the original payload
  supplied out-of-band (see ADR 0002) since it isn't stored, unless the
  endpoint opted into payload snapshots (ADR 0006).
* The audit table grows with delivery volume; retention is enforced by
  ``purge_old_delivery_attempts`` (manual) and, by default, a scheduled
  Celery task (ADR 0010).
