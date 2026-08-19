"""
Shared retention logic for the ``WebhookDeliveryAttempt`` audit trail.

Both the ``purge_old_delivery_attempts`` management command and the
``tasks.purge_old_delivery_attempts_task`` Celery task (scheduled by default
via ``CELERY_BEAT_SCHEDULE`` — see ``settings/common.py`` and
docs/decisions/0010-scheduled-retention-purge.rst) call into
:func:`purge_old_delivery_attempts` so the two entry points can never drift
out of sync on what "old" means or which rows are eligible.
"""

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.utils import timezone

from openedx_webhook_relay.models import WebhookDeliveryAttempt

DEFAULT_RETENTION_DAYS = 90


@dataclass(frozen=True)
class PurgeResult:
    """Outcome of a (possibly dry-run) purge pass."""

    matched: int
    deleted: int
    cutoff: "timezone.datetime"
    days: int
    status_filter: Optional[str] = None

    @property
    def is_dry_run(self) -> bool:
        return self.matched > 0 and self.deleted == 0


def resolve_retention_days(days: Optional[int] = None) -> int:
    if days is not None:
        return days
    return getattr(settings, "OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def purge_old_delivery_attempts(
    days: Optional[int] = None,
    status: Optional[str] = None,
    dry_run: bool = True,
) -> PurgeResult:
    """
    Delete (or, if ``dry_run``, just count) ``WebhookDeliveryAttempt`` rows
    older than the retention window.

    :param days: Override for the retention window. Defaults to
        ``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS`` (90).
    :param status: Optionally restrict to a single
        ``WebhookDeliveryAttempt.Status`` value.
    :param dry_run: If True (default), only counts matching rows and deletes
        nothing. The management command defaults to this for safety; the
        scheduled Celery task always passes ``dry_run=False``.
    """
    resolved_days = resolve_retention_days(days)
    cutoff = timezone.now() - timezone.timedelta(days=resolved_days)

    queryset = WebhookDeliveryAttempt.objects.filter(created__lt=cutoff)
    if status:
        queryset = queryset.filter(status=status)

    matched = queryset.count()
    if dry_run or not matched:
        return PurgeResult(
            matched=matched, deleted=0, cutoff=cutoff, days=resolved_days, status_filter=status
        )

    deleted, _ = queryset.delete()
    return PurgeResult(
        matched=matched, deleted=deleted, cutoff=cutoff, days=resolved_days, status_filter=status
    )
