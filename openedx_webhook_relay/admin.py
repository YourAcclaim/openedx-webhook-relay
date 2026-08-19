"""
Django admin for webhook endpoint configuration and delivery audit trail.
"""

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from openedx_webhook_relay.models import WebhookDeliveryAttempt, WebhookEndpoint
from openedx_webhook_relay.secrets_backend import DatabaseSecretBackend, get_secret_backend
from openedx_webhook_relay.tasks import deliver_webhook


class WebhookEndpointAdminForm(forms.ModelForm):
    """
    Write-only secret input, routed through the configured secret backend.

    ``signing_secret`` and ``signing_secret_previous`` are excluded from
    Django's auto-generated model fields (see ``Meta.exclude``) and
    re-declared here as password inputs that never render an existing
    value — the browser never receives a plaintext secret, not even the
    admin's own. All persistence goes through ``save_model`` below, not
    through ``ModelForm.save()``'s normal field-by-field assignment, so the
    same code path works whether secrets live in this plugin's encrypted
    column or in an external secrets manager (see secrets_backend.py).
    """

    signing_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=WebhookEndpoint._meta.get_field("signing_secret").help_text,
    )
    clear_signing_secret = forms.BooleanField(
        required=False,
        label="Clear signing secret",
        help_text="Remove the stored secret and fall back to the site-wide default.",
    )
    rotate_keeping_previous = forms.BooleanField(
        required=False,
        initial=True,
        label="Keep old secret active during rotation",
        help_text=(
            "When setting a new signing secret, keep signing the previous "
            "value too (as a second header) until you clear it below. "
            "Recommended so the receiver isn't forced to cut over "
            "atomically. Database secret backend only."
        ),
    )
    clear_previous_signing_secret = forms.BooleanField(
        required=False,
        label="Clear previous signing secret",
        help_text="Stop sending the old secret's signature — use once the receiver has cut over.",
    )

    class Meta:
        model = WebhookEndpoint
        # The previous secret is surfaced read-only via previous_secret_status;
        # every other model field is intentionally editable here, so `exclude`
        # (rather than an explicit `fields` list needing manual upkeep) is the
        # intended semantic.
        # pylint: disable=modelform-uses-exclude
        exclude = ("signing_secret_previous",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["clear_previous_signing_secret"].help_text += (
                f" Currently: {self.instance.masked_previous_secret}."
            )

    def clean(self):
        cleaned = super().clean()
        raw_new_secret = cleaned.get("signing_secret") or ""
        # Stashed as form attributes (not cleaned_data) so save_model can
        # tell "explicitly set" apart from "left blank, keep existing" —
        # both end up looking identical in cleaned_data after the restore
        # logic below.
        self._new_secret_provided = bool(raw_new_secret)  # pylint: disable=attribute-defined-outside-init
        self._old_secret_before_edit = (  # pylint: disable=attribute-defined-outside-init
            self.instance.signing_secret if self.instance.pk else ""
        )

        if cleaned.get("clear_signing_secret"):
            cleaned["signing_secret"] = ""
        elif not raw_new_secret:
            cleaned["signing_secret"] = self._old_secret_before_edit
        return cleaned


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    """Admin for webhook endpoints, with write-only secret handling."""

    form = WebhookEndpointAdminForm
    list_display = (
        "description",
        "event",
        "webhook_url",
        "enabled",
        "only_on_passing",
        "secret_status",
        "circuit_state_display",
        "modified",
    )
    list_filter = ("enabled", "event", "only_on_passing", "circuit_state")
    search_fields = ("description", "webhook_url")
    readonly_fields = (
        "created",
        "modified",
        "secret_status",
        "previous_secret_status",
        "secret_backend_reference",
        "circuit_state",
        "consecutive_failures",
        "circuit_opened_at",
    )
    fieldsets = (
        (None, {"fields": ("description", "event", "webhook_url", "enabled")}),
        (
            "Security",
            {
                "fields": (
                    "secret_status",
                    "signing_secret",
                    "clear_signing_secret",
                    "rotate_keeping_previous",
                    "previous_secret_status",
                    "clear_previous_signing_secret",
                    "secret_backend_reference",
                    "custom_headers",
                ),
                "description": (
                    "Secrets are routed through the configured "
                    "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND and never displayed "
                    "in plaintext after saving. custom_headers must not contain credentials."
                ),
            },
        ),
        (
            "Delivery",
            {
                "fields": (
                    "only_on_passing",
                    "timeout_seconds",
                    "max_retries",
                    "retain_payload_snapshot",
                )
            },
        ),
        (
            "Circuit breaker",
            {
                "fields": (
                    "circuit_breaker_enabled",
                    "circuit_breaker_failure_threshold",
                    "circuit_breaker_cooldown_seconds",
                    "circuit_state",
                    "consecutive_failures",
                    "circuit_opened_at",
                ),
            },
        ),
        ("Filtering & PII", {"fields": ("pii_allowlist", "pii_denylist")}),
        ("Audit", {"fields": ("created", "modified")}),
    )

    @admin.display(description="Signing secret")
    def secret_status(self, obj):
        """Masked current signing secret, or a dash for unsaved rows."""
        if not obj.pk:
            return "—"
        return obj.masked_secret

    @admin.display(description="Previous signing secret")
    def previous_secret_status(self, obj):
        """Masked previous signing secret, or a dash for unsaved rows."""
        if not obj.pk:
            return "—"
        return obj.masked_previous_secret

    @admin.display(description="Circuit")
    def circuit_state_display(self, obj):
        """Human-readable circuit-breaker state."""
        return obj.get_circuit_state_display()

    def save_model(self, request, obj, form, change):
        """Route secret writes through the configured backend, rotating if asked."""
        backend = get_secret_backend()
        new_secret_provided = getattr(form, "_new_secret_provided", False)
        old_secret = getattr(form, "_old_secret_before_edit", "")
        clear = form.cleaned_data.get("clear_signing_secret", False)
        rotate_keeping_previous = form.cleaned_data.get("rotate_keeping_previous", False)
        clear_previous = form.cleaned_data.get("clear_previous_signing_secret", False)

        if new_secret_provided:
            if (
                rotate_keeping_previous
                and old_secret
                and isinstance(backend, DatabaseSecretBackend)
            ):
                obj.signing_secret_previous = old_secret
            backend.set_secret(obj, form.cleaned_data.get("signing_secret", ""))
        elif clear:
            backend.set_secret(obj, "")

        if clear_previous:
            obj.signing_secret_previous = ""

        super().save_model(request, obj, form, change)


@admin.register(WebhookDeliveryAttempt)
class WebhookDeliveryAttemptAdmin(admin.ModelAdmin):
    """
    Read-only delivery audit trail — the "did this webhook fire?" view.

    Nothing here is editable: attempts are written exclusively by
    tasks.deliver_webhook so this log can be trusted as an accurate record.
    The one interactive feature is the "Requeue selected" bulk action,
    which only works for EXHAUSTED attempts that captured a payload
    snapshot (endpoint.retain_payload_snapshot=True) — see
    docs/decisions/0006-admin-bulk-redeliver.rst.
    """

    actions = ["requeue_selected"]
    list_display = (
        "created",
        "event",
        "endpoint",
        "status_badge",
        "attempt_number",
        "http_status_code",
        "duration_ms",
        "has_snapshot",
        "correlation_id",
    )
    list_filter = ("status", "event", "endpoint")
    search_fields = ("correlation_id", "endpoint__webhook_url", "error_message")
    date_hierarchy = "created"
    readonly_fields = (
        "endpoint",
        "event",
        "correlation_id",
        "attempt_number",
        "status",
        "http_status_code",
        "duration_ms",
        "error_message",
        "payload_fingerprint",
        "payload_snapshot",
        "created",
        "modified",
    )

    STATUS_COLORS = {
        WebhookDeliveryAttempt.Status.SUCCEEDED: "#1a7f37",
        WebhookDeliveryAttempt.Status.RETRYING: "#9a6700",
        WebhookDeliveryAttempt.Status.EXHAUSTED: "#cf222e",
        WebhookDeliveryAttempt.Status.SKIPPED: "#57606a",
        WebhookDeliveryAttempt.Status.CIRCUIT_OPEN: "#8250df",
    }

    @admin.display(description="Status")
    def status_badge(self, obj):
        """Colour-coded delivery status for the changelist."""
        color = self.STATUS_COLORS.get(obj.status, "#57606a")
        return format_html(
            '<span style="color:{}; font-weight:600;">{}</span>', color, obj.get_status_display()
        )

    @admin.display(description="Snapshot", boolean=True)
    def has_snapshot(self, obj):
        """True when a payload snapshot was retained for this attempt."""
        return obj.payload_snapshot is not None

    @admin.action(description="Requeue selected exhausted deliveries")
    def requeue_selected(self, request, queryset):
        """Re-enqueue exhausted attempts that still have a payload snapshot."""
        exhausted = queryset.filter(status=WebhookDeliveryAttempt.Status.EXHAUSTED)
        requeued = 0
        skipped_no_snapshot = 0

        for attempt in exhausted:
            if attempt.payload_snapshot is None:
                skipped_no_snapshot += 1
                continue
            deliver_webhook.delay(
                attempt.endpoint_id,
                attempt.event,
                attempt.payload_snapshot,
                str(attempt.correlation_id),
            )
            requeued += 1

        if requeued:
            self.message_user(
                request, f"Requeued {requeued} deliver(y/ies).", level=messages.SUCCESS
            )
        if skipped_no_snapshot:
            self.message_user(
                request,
                f"Skipped {skipped_no_snapshot} attempt(s) with no stored payload snapshot "
                "(enable retain_payload_snapshot on the endpoint before the fact, or use "
                "`./manage.py redeliver_failed_webhooks --requeue --payload-file=...`).",
                level=messages.WARNING,
            )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Retain full history; use data retention tooling/management commands
        # for GDPR-driven purges instead of ad hoc admin deletes.
        return request.user.is_superuser
