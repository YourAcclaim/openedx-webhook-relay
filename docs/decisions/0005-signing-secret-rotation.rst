0005. Signing secret rotation
###############################

Status
******

Accepted

Context
*******

Rotating a shared HMAC secret is normally an atomic-cutover problem: the
sender and receiver must agree on the new secret at the same instant, or
every request in between fails signature verification. That's an awkward
coordination problem across two independently deployed systems (the LMS
and whatever consumes the webhook).

Decision
********

``WebhookEndpoint`` gained a second field, ``signing_secret_previous``.
While it's set, every outbound request carries *two* signature headers:
``X-OpenEdX-Webhook-Signature`` (current secret) and
``X-OpenEdX-Webhook-Signature-Previous`` (previous secret). The receiver
can be updated to accept either header during the rotation window, then
the operator clears ``signing_secret_previous`` once the receiver has
confirmed it's validated at least one request with the new secret.

Admin and the ``rotate_signing_secret`` management command both default to
moving the current secret into ``signing_secret_previous`` when a new one
is set (the "keep old secret active" checkbox / default CLI behavior),
rather than requiring a separate step.

This only applies to the "database" secret backend. If
``OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND`` is an external backend (e.g. AWS
Secrets Manager), rotation for that backend's secret is out of scope for
this plugin — use that backend's own versioning/rotation tooling. Storing
the "previous" value locally for an externally-backed secret would
partially defeat the point of moving secrets out of this database.

Consequences
************

* Every signed request is very slightly larger (one extra header) while a
  rotation is in progress. Negligible.
* Receivers need their own two-secret verification logic during rotation
  windows — document this in integration guides for downstream consumers
  (e.g. Credly).
* Forgetting to clear ``signing_secret_previous`` after a rotation leaves
  the old secret valid indefinitely. There's no automatic expiry — this is
  a manual, operator-driven step by design (matches how most HMAC rotation
  guides recommend doing this).
