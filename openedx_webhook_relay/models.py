"""
Models for the webhook relay: destination configuration and delivery audit trail.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel

from openedx_webhook_relay.apps import SUPPORTED_EVENTS
from openedx_webhook_relay.fields import EncryptedCharField, mask_secret


class WebhookEndpoint(TimeStampedModel):
    """
    A destination webhook: which event triggers it, where it's sent, how
    it's signed, and what PII controls apply.

    .. no_pii:
    """

    EVENT_CHOICES = [(event, event.replace("_", " ").title()) for event in SUPPORTED_EVENTS]

    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Human-readable label for this webhook."),
    )
    event = models.CharField(
        max_length=64,
        choices=EVENT_CHOICES,
        db_index=True,
        help_text=_("Open edX event that triggers this webhook."),
    )
    webhook_url = models.URLField(
        max_length=512,
        help_text=_("HTTPS endpoint that receives signed JSON payloads."),
    )
    enabled = models.BooleanField(default=True, db_index=True)

    # --- Security ---------------------------------------------------------
    # Stored encrypted at rest (see fields.EncryptedCharField) and never
    # rendered in plaintext by admin. See docs/decisions/0003-secret-storage.rst.
    signing_secret = EncryptedCharField(
        max_length=1024,
        blank=True,
        default="",
        help_text=_(
            "Shared secret for HMAC-SHA256 signing. Encrypted at rest; not "
            "shown again after saving. Leave blank to use "
            "OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET from Django settings. "
            "Ignored when OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND is not "
            "'database' — see secret_backend_reference."
        ),
    )
    signing_secret_previous = EncryptedCharField(
        max_length=1024,
        blank=True,
        default="",
        help_text=_(
            "Previous signing secret, kept during a rotation window. While "
            "set, outbound requests carry a second signature "
            "(X-OpenEdX-Webhook-Signature-Previous) computed with this "
            "value so the receiver can verify with either secret. Clear "
            "once the receiver has cut over. See "
            "docs/decisions/0005-signing-secret-rotation.rst."
        ),
    )
    secret_backend_reference = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        help_text=_(
            "Stable identifier used to name/locate this endpoint's secret "
            "in an external secret store when "
            "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND is not 'database'. "
            "Assigned once at creation; unused with the default backend."
        ),
    )
    custom_headers = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            'Optional extra *non-secret* headers as JSON, e.g. '
            '{"X-Source": "lms"}. Never put credentials here — they would '
            "be stored and displayed in plaintext. Use signing_secret for "
            "anything sensitive."
        ),
    )

    # --- Delivery -----------------------------------------------------------
    only_on_passing = models.BooleanField(
        default=True,
        help_text=_("For course passing events, send only when is_passing is true."),
    )
    timeout_seconds = models.PositiveSmallIntegerField(
        default=10,
        help_text=_("Per-request HTTP timeout in seconds."),
    )
    max_retries = models.PositiveSmallIntegerField(
        default=5,
        help_text=_(
            "Maximum delivery attempts (including the first) before this "
            "webhook's delivery is marked exhausted and surfaced for "
            "manual redelivery."
        ),
    )
    retain_payload_snapshot = models.BooleanField(
        default=False,
        help_text=_(
            "Opt-in: store the PII-filtered outbound payload on EXHAUSTED "
            "delivery attempts for this endpoint, so admin's 'Requeue "
            "selected' action can redeliver without needing a payload file. "
            "Off by default because it retains a copy of whatever PII this "
            "endpoint's allowlist/denylist let through. See "
            "docs/decisions/0006-admin-bulk-redeliver.rst."
        ),
    )

    # --- Circuit breaker ---------------------------------------------------
    # Stops hammering a receiver that's persistently down: after
    # `circuit_breaker_failure_threshold` consecutive EXHAUSTED deliveries,
    # new deliveries are skipped (not even attempted) until
    # `circuit_breaker_cooldown_seconds` have passed, at which point a single
    # trial delivery is allowed through (half-open) to test recovery.
    class CircuitState(models.TextChoices):
        """Circuit-breaker states for an endpoint."""

        CLOSED = "closed", _("Closed (normal)")
        OPEN = "open", _("Open (skipping deliveries)")
        HALF_OPEN = "half_open", _("Half-open (trial delivery in flight)")

    circuit_breaker_enabled = models.BooleanField(default=True)
    circuit_breaker_failure_threshold = models.PositiveSmallIntegerField(
        default=5,
        help_text=_("Consecutive exhausted deliveries before the circuit opens."),
    )
    circuit_breaker_cooldown_seconds = models.PositiveIntegerField(
        default=300,
        help_text=_("How long the circuit stays open before allowing a trial delivery."),
    )
    circuit_state = models.CharField(
        max_length=16,
        choices=CircuitState.choices,
        default=CircuitState.CLOSED,
        editable=False,
    )
    consecutive_failures = models.PositiveIntegerField(default=0, editable=False)
    circuit_opened_at = models.DateTimeField(null=True, blank=True, editable=False)

    # --- PII controls ---------------------------------------------------
    # dot-separated paths relative to payload root. Empty allowlist means
    # send everything (after event-specific shaping); denylist is applied
    # after the allowlist.
    pii_allowlist = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "If non-empty, keep only these payload paths. Example: "
            '["data.user.pii.email", "data.user.pii.username", '
            '"data.is_passing", "data.course.course_key", "event_metadata"].'
        ),
    )
    pii_denylist = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Remove these paths before sending, applied after the "
            'allowlist. Example: ["data.user.pii.name"].'
        ),
    )

    class Meta:
        verbose_name = "Webhook endpoint"
        ordering = ["event", "description"]

    def clean(self):
        super().clean()
        if self.webhook_url and not self.webhook_url.startswith("https://"):
            raise ValidationError(
                {"webhook_url": _("Webhook URL must use https:// in production.")}
            )

        if self.custom_headers is not None and not isinstance(self.custom_headers, dict):
            raise ValidationError({"custom_headers": _("Must be a JSON object.")})

        for field_name in ("pii_allowlist", "pii_denylist"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, list):
                raise ValidationError({field_name: _("Must be a JSON array of strings.")})

    def effective_secret(self) -> str:
        """Return the current signing secret via the configured secret backend."""
        # pylint: disable=import-outside-toplevel
        from openedx_webhook_relay.secrets_backend import get_secret_backend

        secret = get_secret_backend().get_secret(self)
        if secret:
            return secret

        from django.conf import settings  # pylint: disable=import-outside-toplevel

        return getattr(settings, "OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET", "") or ""

    def effective_previous_secret(self) -> str:
        """
        Return the previous signing secret, if a rotation is in progress.

        Unlike ``effective_secret``, this always comes from the local
        (encrypted) field regardless of secret backend — rotation with an
        external secrets manager is out of scope for v1 (see
        docs/decisions/0005-signing-secret-rotation.rst).
        """
        return self.signing_secret_previous or ""

    @property
    def masked_secret(self) -> str:
        """Display-safe representation for admin (never the plaintext secret)."""
        if not self.signing_secret:
            return _("(using default secret)")
        return mask_secret(self.signing_secret)

    @property
    def masked_previous_secret(self) -> str:
        """Display-safe representation of the previous secret, if any."""
        if not self.signing_secret_previous:
            return _("(none)")
        return mask_secret(self.signing_secret_previous)

    # --- Circuit breaker -----------------------------------------------

    def try_acquire_delivery_slot(self) -> bool:
        """
        Decide whether a delivery attempt should proceed right now.

        Uses a conditional UPDATE for the OPEN → HALF_OPEN transition so
        that when several workers notice the cooldown has elapsed at once,
        only one of them wins the trial delivery; the rest back off.
        """
        if not self.circuit_breaker_enabled or self.circuit_state == self.CircuitState.CLOSED:
            return True

        if self.circuit_state == self.CircuitState.HALF_OPEN:
            return False  # a trial delivery is already in flight

        # OPEN: allow exactly one trial once the cooldown has elapsed.
        if self.circuit_opened_at is None:
            return True  # inconsistent state; fail open rather than wedge shut forever

        elapsed = (timezone.now() - self.circuit_opened_at).total_seconds()
        if elapsed < self.circuit_breaker_cooldown_seconds:
            return False

        updated = WebhookEndpoint.objects.filter(
            pk=self.pk, circuit_state=self.CircuitState.OPEN
        ).update(circuit_state=self.CircuitState.HALF_OPEN)
        if updated:
            self.circuit_state = self.CircuitState.HALF_OPEN
        return bool(updated)

    def record_circuit_success(self) -> None:
        """Delivery succeeded: fully reset the breaker."""
        WebhookEndpoint.objects.filter(pk=self.pk).update(
            circuit_state=self.CircuitState.CLOSED,
            consecutive_failures=0,
            circuit_opened_at=None,
        )
        self.circuit_state = self.CircuitState.CLOSED
        self.consecutive_failures = 0
        self.circuit_opened_at = None

    def record_circuit_failure(self) -> None:
        """Delivery exhausted its retries: count it, opening the circuit if past threshold."""
        WebhookEndpoint.objects.filter(pk=self.pk).update(
            consecutive_failures=models.F("consecutive_failures") + 1
        )
        self.refresh_from_db(fields=["consecutive_failures"])
        if self.consecutive_failures >= self.circuit_breaker_failure_threshold:
            now = timezone.now()
            WebhookEndpoint.objects.filter(pk=self.pk).update(
                circuit_state=self.CircuitState.OPEN, circuit_opened_at=now
            )
            self.circuit_state = self.CircuitState.OPEN
            self.circuit_opened_at = now

    def __str__(self):
        label = self.description or self.event
        return f"{label} → {self.webhook_url}"


class WebhookDeliveryAttempt(TimeStampedModel):
    """
    Immutable audit record of a single delivery attempt.

    This is the plugin's delivery-visibility / dead-letter mechanism: every
    attempt (success, retryable failure, or final exhaustion) is recorded
    here so operators can answer "did this webhook fire?" without grepping
    application logs, and can manually redeliver exhausted events.

    The full payload is intentionally *not* stored (it may contain PII);
    ``payload_fingerprint`` lets you confirm identical retries.

    .. no_pii:
    """

    class Status(models.TextChoices):
        """Terminal and in-flight states for a delivery attempt."""

        SUCCEEDED = "succeeded", _("Succeeded")
        RETRYING = "retrying", _("Retrying")
        EXHAUSTED = "exhausted", _("Exhausted (needs attention)")
        SKIPPED = "skipped", _("Skipped (filtered out)")
        CIRCUIT_OPEN = "circuit_open", _("Skipped (circuit breaker open)")

    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        db_index=True,
        help_text=_("Stable ID shared by every attempt for the same triggering event."),
    )
    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
    )
    event = models.CharField(max_length=64, db_index=True)
    attempt_number = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    http_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    payload_fingerprint = models.CharField(max_length=32, blank=True, default="")
    payload_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text=_(
            "PII-filtered outbound payload, retained only when the endpoint "
            "has retain_payload_snapshot=True and this attempt is EXHAUSTED. "
            "Lets admin's 'Requeue selected' action redeliver without a "
            "manually-supplied payload file."
        ),
    )

    class Meta:
        verbose_name = "Webhook delivery attempt"
        ordering = ["-created"]
        # Names are given explicitly and must match those created in
        # 0001_initial. Without them Django derives its own hashed names, sees a
        # mismatch against the migrated state, and asks for a rename migration
        # on every `makemigrations` run.
        indexes = [
            models.Index(fields=["endpoint", "status"], name="owr_wda_endpoint_status_idx"),
            models.Index(fields=["correlation_id"], name="owr_wda_correlation_id_idx"),
        ]

    def __str__(self):
        return f"[{self.status}] {self.event} → {self.endpoint_id} (attempt {self.attempt_number})"
