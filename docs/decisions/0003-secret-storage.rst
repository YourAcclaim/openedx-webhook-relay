0003. Encrypting signing secrets at rest
##########################################

Status
******

Accepted

Context
*******

Storing ``signing_secret`` as a plain ``CharField`` would let any Django
admin/superuser (or anyone with read access to the LMS database or a DB
backup) read every webhook's shared secret in plaintext, and the Django
admin change form would render it back in a plain text input.

Decision
********

* ``WebhookEndpoint.signing_secret`` uses a custom ``EncryptedCharField``
  (``fields.py``) that encrypts with Fernet (AES-128-CBC + HMAC) before
  writing to the database and decrypts only in Python, never in SQL.
* The encryption key (``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY``) is a
  dedicated Fernet key, deliberately *not* derived from Django's
  ``SECRET_KEY``. Coupling to ``SECRET_KEY`` would mean routine secret-key
  rotation breaks every stored webhook secret; a dedicated key can be
  rotated independently (with a re-encryption migration) and should be
  sourced from a secrets manager (AWS Secrets Manager, Vault, etc.) via
  deployment configuration, never committed to a settings file.
* Django admin never redisplays the decrypted value. The change form
  renders a blank password input; leaving it blank on save keeps the
  existing secret, and a "Clear signing secret" checkbox is the only way
  to remove it. The list view and detail view show only a masked form
  (``WebhookEndpoint.masked_secret``, e.g. ``••••••••cret``).

Alternatives considered
************************

* **Store secrets in an external secrets manager, keep only a reference
  in Django.** More secure in principle, but adds an external dependency
  and network call to every delivery, and most self-hosted Open edX
  operators do not have one wired up. Left as a documented follow-up
  (see README "Suggested improvements") rather than a hard requirement.
* **Hash instead of encrypt.** Not viable — we need the plaintext back to
  compute the outbound HMAC signature.

Consequences
************

* Losing ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY`` makes all stored secrets
  permanently unrecoverable (by design — same trade-off as any
  encryption-at-rest scheme). Back it up in the same secrets manager used
  for other Django/Open edX secrets.
* Rotating the key requires a data migration that decrypts with the old
  key and re-encrypts with the new one; this is not automated by this
  plugin (documented as a follow-up).
