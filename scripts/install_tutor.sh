#!/usr/bin/env bash
#
# Install this plugin into an existing Tutor deployment, asserting each step.
#
# The point of this script is the assertions, not the commands. Every failure
# this install can produce is silent and late: a plugin file that never got
# written, a setting that never rendered into LMS settings, `restart` reusing
# the previous image. Each of those surfaces hours later as a 500 in admin or
# "No installed app with label 'openedx_webhook_relay'". Here they stop the
# script where they happen, with the reason.
#
# Run it from a checkout, not from an installed package: `scripts/` is not
# shipped by `setup.py` (find_packages() only collects Python packages), and in
# any case this runs *before* the plugin is installed.
#
# Usage:
#
#   OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY="$(your-secret-store get ...)" \
#     scripts/install_tutor.sh [--yes] [--verify-only]
#
# Required:
#   OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY
#       The Fernet key. Deliberately NOT generated here: a script that mints a
#       key silently produces a different one on every run, and if nobody
#       captures it, every stored signing secret becomes unreadable. Generate
#       once, per environment, with:
#           openssl rand -base64 32 | tr '+/' '-_'
#       and keep it in your secrets store.
#
# Optional:
#   RELAY_PIP_REQUIREMENT   Full pip requirement to install. Overrides RELAY_VERSION.
#   RELAY_VERSION           Tag to install, e.g. v1.4.0. Defaults to the version
#                           declared in this checkout.
#
# Flags:
#   --yes           Do not prompt before the rebuild and reboot.
#   --verify-only   Run the checks against the current deployment and change
#                   nothing. Useful after an upgrade, or to diagnose an install.
#
# What this script does NOT do: create the endpoint and its signing secret.
# That secret is shared with the receiver and needs coordinating with them, so
# it is a deliberate human step. See README.rst.
set -euo pipefail

REPO_URL="https://github.com/YourAcclaim/openedx-webhook-relay.git"
PLUGIN_NAME="openedx-webhook-relay"
SETTING="OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY"

ASSUME_YES=0
VERIFY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    -h|--help) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

step()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()    { printf '    \033[32mok\033[0m  %s\n' "$*"; }
fail()  { printf '    \033[31mFAILED\033[0m  %s\n' "$*" >&2; exit 1; }
note()  { printf '    %s\n' "$*"; }

# ---------------------------------------------------------------- preflight

step "Preflight"

command -v tutor >/dev/null || fail "tutor is not on PATH."
ok "tutor $(tutor --version 2>/dev/null | head -1)"

CONFIG_ROOT="$(tutor config printroot)"
[ -d "$CONFIG_ROOT" ] || fail "tutor config root $CONFIG_ROOT does not exist. Is Tutor set up?"
ok "config root: $CONFIG_ROOT"

if [ "$VERIFY_ONLY" -eq 0 ]; then
  KEY="${OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY:-}"
  [ -n "$KEY" ] || fail "$SETTING is not set. See the header of this script."

  # Cheap shape check so a truncated paste fails here rather than at the first
  # encrypt. A Fernet key is urlsafe-base64 of 32 bytes: 44 chars ending '='.
  if ! printf '%s' "$KEY" | grep -Eq '^[A-Za-z0-9_-]{43}=$'; then
    fail "$SETTING does not look like a Fernet key (want 44 chars of urlsafe base64 ending '='; got ${#KEY} chars)."
  fi
  ok "encryption key provided (${#KEY} chars, ends '${KEY: -1}')"

  if [ -n "${RELAY_PIP_REQUIREMENT:-}" ]; then
    REQUIREMENT="$RELAY_PIP_REQUIREMENT"
  else
    VERSION="${RELAY_VERSION:-}"
    if [ -z "$VERSION" ]; then
      HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
      DECLARED="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' \
        "$HERE/openedx_webhook_relay/__init__.py" 2>/dev/null || true)"
      [ -n "$DECLARED" ] || fail "Could not read the version from this checkout. Set RELAY_VERSION or RELAY_PIP_REQUIREMENT."
      VERSION="v$DECLARED"
      note "no RELAY_VERSION given; using this checkout's version: $VERSION"
      note "that tag must exist on the remote, or the build will fail to resolve it"
    fi
    REQUIREMENT="git+${REPO_URL}@${VERSION}"
  fi
  ok "install source: $REQUIREMENT"
fi

# ------------------------------------------------------------------- install

if [ "$VERIFY_ONLY" -eq 0 ]; then

  step "1/6  Set the encryption key"
  # Before endpoints: saving the first signing secret is what first needs it.
  tutor config save --set "${SETTING}=${KEY}" >/dev/null
  grep -q "^${SETTING}:" "$CONFIG_ROOT/config.yml" || fail "$SETTING did not land in config.yml."
  ok "$SETTING written to config.yml"

  step "2/6  Install and enable the Tutor plugin"
  PLUGIN_ROOT="$(tutor plugins printroot)"
  # mkdir -p matters: without it the redirect below fails silently and
  # `tutor plugins enable` then reports the plugin is not installed.
  mkdir -p "$PLUGIN_ROOT"
  cat > "$PLUGIN_ROOT/${PLUGIN_NAME}.py" <<'PLUGIN_EOF'
from tutor import hooks

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-lms-common-settings",
        """
OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "{{ OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY }}"
""",
    )
)

hooks.Filters.CONFIG_DEFAULTS.add_items(
    [
        ("OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY", ""),
    ]
)
PLUGIN_EOF
  [ -s "$PLUGIN_ROOT/${PLUGIN_NAME}.py" ] || fail "plugin file was not written to $PLUGIN_ROOT."
  ok "plugin file: $PLUGIN_ROOT/${PLUGIN_NAME}.py"

  tutor plugins list 2>/dev/null | grep -q "$PLUGIN_NAME" \
    || fail "tutor does not see the plugin. Check $PLUGIN_ROOT and the file's syntax."
  ok "tutor lists the plugin"

  tutor plugins enable "$PLUGIN_NAME" >/dev/null
  tutor config save >/dev/null
  ok "plugin enabled and environment rendered"

  step "3/6  Confirm the setting reached LMS settings"
  # The failure this catches: present in config.yml, absent from the rendered
  # settings, because the plugin was not applied. It otherwise surfaces much
  # later as a 500 when saving an endpoint.
  RENDERED="$CONFIG_ROOT/env/apps/openedx/settings/lms/"
  if ! grep -rq "$SETTING" "$RENDERED" 2>/dev/null; then
    fail "$SETTING is not in the rendered LMS settings ($RENDERED). The Tutor plugin is not being applied."
  fi
  ok "$SETTING present in rendered LMS settings"

  step "4/6  Add the pip requirement"
  # --append would duplicate the entry on a re-run, so check first.
  if grep -Fq "$REQUIREMENT" "$CONFIG_ROOT/config.yml"; then
    ok "requirement already present, leaving it alone"
  else
    tutor config save --append "OPENEDX_EXTRA_PIP_REQUIREMENTS=${REQUIREMENT}" >/dev/null
    grep -Fq "$REQUIREMENT" "$CONFIG_ROOT/config.yml" || fail "requirement did not land in config.yml."
    ok "requirement appended"
  fi

  step "5/6  Rebuild and reboot"
  note "The rebuild takes a while, and the reboot causes brief downtime."
  note "Deliveries in flight are re-queued (the task is acks_late), so the"
  note "receiver may see an event twice. Disable endpoints first if it is not"
  note "idempotent."
  if [ "$ASSUME_YES" -eq 0 ]; then
    read -r -p "    Continue? [y/N] " reply
    case "$reply" in y|Y|yes|YES) ;; *) echo "    aborted"; exit 1 ;; esac
  fi
  tutor images build openedx
  # reboot, not restart: `docker compose restart` reuses the old image.
  tutor local reboot
  ok "image rebuilt and containers recreated"
fi

# -------------------------------------------------------------- verification

step "$([ "$VERIFY_ONLY" -eq 1 ] && echo 'Verify' || echo '6/6  Verify')"

tutor local exec lms pip show openedx-webhook-relay >/tmp/owr-pipshow 2>&1 \
  || fail "the package is not installed in the running container. If you used 'restart' rather than 'reboot', the container is on the previous image."
ok "installed: $(sed -n 's/^Version: //p' /tmp/owr-pipshow)"
rm -f /tmp/owr-pipshow

tutor local exec lms ./manage.py lms shell -c "
from django.conf import settings
from openedx_webhook_relay.fields import _get_fernet
from openedx_webhook_relay.tasks import deliver_webhook
assert getattr(settings, '${SETTING}', None), '${SETTING} is empty in the running process'
_get_fernet()
print('task', deliver_webhook.name)
" >/dev/null 2>&1 || fail "the key is missing/malformed in the running process, or the plugin is not registered. Run: tutor local exec lms ./manage.py lms shell -c \"from openedx_webhook_relay.fields import _get_fernet; _get_fernet()\""
ok "encryption key valid and delivery task registered"

if [ "$VERIFY_ONLY" -eq 0 ]; then
  tutor local exec lms ./manage.py lms migrate openedx_webhook_relay
fi
tutor local exec lms ./manage.py lms showmigrations openedx_webhook_relay \
  | grep -q '\[ \]' && fail "unapplied migrations remain. Run: tutor local exec lms ./manage.py lms migrate openedx_webhook_relay"
ok "migrations applied"

# ------------------------------------------------------------------ next steps

cat <<'NEXT'

Plugin installed and verified. Two things this script deliberately leaves to you:

  1. Create the endpoint, in Django admin under "Webhook endpoints".
     Needs an https:// URL and a signing secret, which is shared with the
     receiver and so needs coordinating with them. Generate one with:
         python3 -c "import secrets; print(secrets.token_urlsafe(32))"

     Then confirm it was stored encrypted -- a blank secret means webhooks
     send UNSIGNED and still record as succeeded:
         tutor local exec mysql mysql -u root \
           -p"$(tutor config printvalue MYSQL_ROOT_PASSWORD)" openedx \
           -e "SELECT id, LEFT(signing_secret,14) FROM openedx_webhook_relay_webhookendpoint;"
     You want a value starting 'enc::'.

  2. Decide how the audit trail gets purged. The plugin registers a Celery
     beat entry, but Tutor ships no beat container, so nothing dispatches it
     and the table grows unbounded with no error. Either add a beat service,
     or set OPENEDX_WEBHOOK_RELAY_AUTO_PURGE_ENABLED = False and schedule
     purge_old_delivery_attempts yourself. See README.rst.

NEXT
