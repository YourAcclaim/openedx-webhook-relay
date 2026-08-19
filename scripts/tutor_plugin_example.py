"""
Reference Tutor plugin for installing openedx-webhook-relay.

This is a *documented example*, not a hack — copy it into your Tutor
plugins directory (``~/.local/share/tutor-plugins/openedx-webhook-relay.py``
for local dev, or bake it into your deployment's plugin set) and adjust the
pip requirement / settings for your environment.

Local development (editable install from a checked-out copy)::

    tutor mounts add /path/to/openedx-webhook-relay
    tutor config save --append OPENEDX_EXTRA_PIP_REQUIREMENTS='/path/to/openedx-webhook-relay'

Production (pinned release, encryption key from a secrets manager — never
commit a real key)::

    tutor config save \\
      --append OPENEDX_EXTRA_PIP_REQUIREMENTS='git+https://github.com/YourAcclaim/openedx-webhook-relay.git@v1.3.0' \\
      --set OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY="$(cat /run/secrets/webhook_relay_key)"

Then, with this file enabled (``tutor plugins enable openedx-webhook-relay``)::

    tutor images build openedx
    tutor local restart
    tutor local exec lms ./manage.py lms migrate openedx_webhook_relay
"""

from tutor import hooks

hooks.Filters.ENV_PATCHES.add_item(
    (
        "openedx-lms-common-settings",
        """
# Provided by OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY in Tutor config (see
# docs/decisions/0003-secret-storage.rst) — do not hardcode a real key here.
OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY = "{{ OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY }}"
""",
    )
)

hooks.Filters.CONFIG_DEFAULTS.add_items(
    [
        ("OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY", ""),
    ]
)
