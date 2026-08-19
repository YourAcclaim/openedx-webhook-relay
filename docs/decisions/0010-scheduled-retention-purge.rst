0010. Scheduled retention purge
##################################

Status
******

Accepted

Context
*******

``purge_old_delivery_attempts`` (see docs/decisions/0004-delivery-audit-trail.rst)
enforces retention on the ``WebhookDeliveryAttempt`` audit trail, but as a
management command it only runs when a human or an external cron/CI job
invokes it. In practice, that means retention only happens if someone
remembers to schedule it during deployment setup — an easy thing to skip,
and a silent one to skip: nothing breaks immediately, the audit table just
grows a little more every day until someone notices, usually much later
than they'd like.

Decision
********

The retention logic is factored out of the management command into
``retention.purge_old_delivery_attempts()``, and a Celery task,
``tasks.purge_old_delivery_attempts_task``, calls it with ``dry_run=False``.
That task is registered on ``settings.CELERY_BEAT_SCHEDULE`` by default from
``settings/common.py``, at a deliberately off-peak, non-round time
(03:17, configurable via ``OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR`` /
``_MINUTE``) so retention happens without any deployment-specific setup.

The registration merges into whatever ``CELERY_BEAT_SCHEDULE`` already
exists rather than replacing it, since other plugins and edx-platform
itself may already populate that dict — overwriting it would silently drop
unrelated scheduled tasks.

Deployments that want a different retention mechanism (their own cron
entry, a different tool, or no automatic purge at all) can set
``OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False``, which skips the beat
registration entirely. The management command remains available either
way for one-off cleanups or non-default retention windows.

Consequences
************

* Retention is enforced without any action required at deployment time,
  closing the gap where a deployment simply never got around to scheduling
  the management command.
* This depends on the deployment actually running a Celery beat scheduler
  process. Every mainline Open edX deployment already does (edx-platform
  and other plugins rely on beat for their own periodic tasks), but a
  from-scratch, non-standard deployment without beat running would need to
  either start one or fall back to the management command on its own cron.
* The retention window and the purge schedule are two independent
  settings (``OPENEDX_WEBHOOK_RELAY_AUDIT_RETENTION_DAYS`` vs.
  ``OPENEDX_WEBHOOK_RELAY_PURGE_SCHEDULE_HOUR``/``_MINUTE``) — changing how
  long to keep rows doesn't require changing how often the purge runs, and
  vice versa.
