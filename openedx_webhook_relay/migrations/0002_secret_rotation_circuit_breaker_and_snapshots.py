import uuid

from django.db import migrations, models

import openedx_webhook_relay.fields


class Migration(migrations.Migration):

    dependencies = [
        ("openedx_webhook_relay", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="webhookendpoint",
            name="signing_secret_previous",
            field=openedx_webhook_relay.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "Previous signing secret, kept during a rotation window. While set, "
                    "outbound requests carry a second signature "
                    "(X-OpenEdX-Webhook-Signature-Previous) computed with this value so the "
                    "receiver can verify with either secret. Clear once the receiver has cut "
                    "over. See docs/decisions/0005-signing-secret-rotation.rst."
                ),
                max_length=1024,
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="secret_backend_reference",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text=(
                    "Stable identifier used to name/locate this endpoint's secret in an "
                    "external secret store when OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND is not "
                    "'database'. Assigned once at creation; unused with the default backend."
                ),
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="retain_payload_snapshot",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Opt-in: store the PII-filtered outbound payload on EXHAUSTED delivery "
                    "attempts for this endpoint, so admin's 'Requeue selected' action can "
                    "redeliver without needing a payload file. Off by default because it "
                    "retains a copy of whatever PII this endpoint's allowlist/denylist let "
                    "through. See docs/decisions/0006-admin-bulk-redeliver.rst."
                ),
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="circuit_breaker_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="circuit_breaker_failure_threshold",
            field=models.PositiveSmallIntegerField(
                default=5,
                help_text="Consecutive exhausted deliveries before the circuit opens.",
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="circuit_breaker_cooldown_seconds",
            field=models.PositiveIntegerField(
                default=300,
                help_text="How long the circuit stays open before allowing a trial delivery.",
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="circuit_state",
            field=models.CharField(
                choices=[
                    ("closed", "Closed (normal)"),
                    ("open", "Open (skipping deliveries)"),
                    ("half_open", "Half-open (trial delivery in flight)"),
                ],
                default="closed",
                editable=False,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="consecutive_failures",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="circuit_opened_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name="webhookendpoint",
            name="signing_secret",
            field=openedx_webhook_relay.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "Shared secret for HMAC-SHA256 signing. Encrypted at rest; not shown "
                    "again after saving. Leave blank to use "
                    "OPENEDX_WEBHOOK_RELAY_DEFAULT_SECRET from Django settings. Ignored when "
                    "OPENEDX_WEBHOOK_RELAY_SECRET_BACKEND is not 'database' — see "
                    "secret_backend_reference."
                ),
                max_length=1024,
            ),
        ),
        migrations.AddField(
            model_name="webhookdeliveryattempt",
            name="payload_snapshot",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    "PII-filtered outbound payload, retained only when the endpoint has "
                    "retain_payload_snapshot=True and this attempt is EXHAUSTED. Lets admin's "
                    "'Requeue selected' action redeliver without a manually-supplied payload "
                    "file."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="webhookdeliveryattempt",
            name="status",
            field=models.CharField(
                choices=[
                    ("succeeded", "Succeeded"),
                    ("retrying", "Retrying"),
                    ("exhausted", "Exhausted (needs attention)"),
                    ("skipped", "Skipped (filtered out)"),
                    ("circuit_open", "Skipped (circuit breaker open)"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
    ]
