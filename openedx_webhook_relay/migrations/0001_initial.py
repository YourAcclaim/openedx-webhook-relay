import uuid

import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
from django.db import migrations, models

import openedx_webhook_relay.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WebhookEndpoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created", model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name="created")),
                ("modified", model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name="modified")),
                ("description", models.CharField(blank=True, default="", help_text="Human-readable label for this webhook.", max_length=255)),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("COURSE_PASSING_STATUS_UPDATED", "Course Passing Status Updated"),
                            ("CCX_COURSE_PASSING_STATUS_UPDATED", "Ccx Course Passing Status Updated"),
                        ],
                        db_index=True,
                        help_text="Open edX event that triggers this webhook.",
                        max_length=64,
                    ),
                ),
                ("webhook_url", models.URLField(help_text="HTTPS endpoint that receives signed JSON payloads.", max_length=512)),
                ("enabled", models.BooleanField(db_index=True, default=True)),
                (
                    "signing_secret",
                    openedx_webhook_relay.fields.EncryptedCharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Shared secret for HMAC-SHA256 signing. Encrypted at rest; not shown "
                            "again after saving. Leave blank to use "
                            "OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET from Django settings."
                        ),
                        max_length=1024,
                    ),
                ),
                (
                    "custom_headers",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Optional extra *non-secret* headers as JSON, e.g. "
                            '{"X-Source": "lms"}. Never put credentials here — they would be '
                            "stored and displayed in plaintext. Use signing_secret for anything "
                            "sensitive."
                        ),
                    ),
                ),
                ("only_on_passing", models.BooleanField(default=True, help_text="For course passing events, send only when is_passing is true.")),
                ("timeout_seconds", models.PositiveSmallIntegerField(default=10, help_text="Per-request HTTP timeout in seconds.")),
                (
                    "max_retries",
                    models.PositiveSmallIntegerField(
                        default=5,
                        help_text=(
                            "Maximum delivery attempts (including the first) before this "
                            "webhook's delivery is marked exhausted and surfaced for manual "
                            "redelivery."
                        ),
                    ),
                ),
                (
                    "pii_allowlist",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            "If non-empty, keep only these payload paths. Example: "
                            '["data.user.pii.email", "data.user.pii.username", '
                            '"data.is_passing", "data.course.course_key", "event_metadata"].'
                        ),
                    ),
                ),
                (
                    "pii_denylist",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            "Remove these paths before sending, applied after the allowlist. "
                            'Example: ["data.user.pii.name"].'
                        ),
                    ),
                ),
            ],
            options={
                "verbose_name": "Webhook endpoint",
                "ordering": ["event", "description"],
            },
        ),
        migrations.CreateModel(
            name="WebhookDeliveryAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created", model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name="created")),
                ("modified", model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name="modified")),
                (
                    "correlation_id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        help_text="Stable ID shared by every attempt for the same triggering event.",
                    ),
                ),
                ("event", models.CharField(db_index=True, max_length=64)),
                ("attempt_number", models.PositiveSmallIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("succeeded", "Succeeded"),
                            ("retrying", "Retrying"),
                            ("exhausted", "Exhausted (needs attention)"),
                            ("skipped", "Skipped (filtered out)"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("http_status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("payload_fingerprint", models.CharField(blank=True, default="", max_length=32)),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="delivery_attempts",
                        to="openedx_webhook_relay.webhookendpoint",
                    ),
                ),
            ],
            options={
                "verbose_name": "Webhook delivery attempt",
                "ordering": ["-created"],
            },
        ),
        migrations.AddIndex(
            model_name="webhookdeliveryattempt",
            index=models.Index(fields=["endpoint", "status"], name="owr_wda_endpoint_status_idx"),
        ),
        migrations.AddIndex(
            model_name="webhookdeliveryattempt",
            index=models.Index(fields=["correlation_id"], name="owr_wda_correlation_id_idx"),
        ),
    ]
