0009. Encryption key rotation and pluggable secret backends
################################################################

Status
******

Accepted

Context
*******

ADR 0003 established local Fernet encryption for signing secrets, keyed by
a dedicated ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY`` — but shipped no way
to actually rotate that key, and no way to opt out of local storage
entirely for deployments that already run a secrets manager.

Decision
********

**Key rotation.** ``rotate_encryption_key`` decrypts every
``WebhookEndpoint``'s ``signing_secret``/``signing_secret_previous`` under
the old key (fetched with ``override_settings`` so
``EncryptedCharField.from_db_value`` decrypts correctly at query time),
then re-encrypts and saves each row under the new key (same trick, applied
at save time instead). Doing it row-by-row in a single ``save()`` per row
means a crash mid-rotation leaves each already-processed row fully
readable under the new key and each not-yet-processed row fully readable
under the old key — never a half-encrypted, unreadable value.

**Pluggable secret backend.** ``secrets_backend.py`` defines a tiny
``get_secret``/``set_secret`` interface. The default
(``DatabaseSecretBackend``) is what ADR 0003 already described. An
optional ``AWSSecretsManagerBackend`` stores the secret in AWS Secrets
Manager instead, keyed by ``WebhookEndpoint.secret_backend_reference`` — a
UUID assigned at creation, not the primary key, specifically so the
external reference doesn't depend on save ordering (a brand-new,
unsaved instance already has a valid reference to use). Selected via
``OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND``; ``boto3`` is only imported (and
required) when that backend is actually selected, so it stays an optional
dependency for everyone else.

Consequences
************

* Key rotation requires downtime-free coordination only in the sense that
  the deployment's ``OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY`` setting must be
  flipped to the new key *after* the command finishes and *before* the
  next deploy/restart reads settings — there's a window where the DB holds
  ciphertext under the new key but the running process still has the old
  key configured. Run the command, then redeploy with the new key
  promptly; don't leave a long gap.
* Signing-secret rotation (ADR 0005)'s ``signing_secret_previous`` field is
  local-only and not mirrored to an external secret backend — using an
  external backend means rotation for *that* secret is that backend's
  responsibility, not this plugin's.
* Switching ``OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND`` after endpoints
  already have secrets in the old backend requires a manual migration
  (read from old backend, ``set_secret`` into new backend) — not automated
  by this plugin, since the two backends' failure/rollback semantics
  differ enough that a generic migrator would be misleading.
