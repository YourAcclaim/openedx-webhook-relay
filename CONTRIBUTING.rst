Contributing
############

Setup
*****

::

  pip install -r requirements/dev.txt
  pre-commit install

Running checks
**************

::

  make test-quality   # quality (ruff, isort, pylint) + tests with coverage gate
  make test            # tests only
  make quality         # lint only

Adding a new event
*******************

1. Add the event name to ``SUPPORTED_EVENTS`` in ``apps.py``.
2. Register a receiver function in ``receivers.py`` and wire it into
   ``apps.py``'s ``plugin_app["signals_config"]``.
3. Add the new choice to ``WebhookEndpoint.EVENT_CHOICES`` (derived
   automatically from ``SUPPORTED_EVENTS``) and generate a migration:
   ``./manage.py makemigrations openedx_webhook_relay``.
4. Add tests exercising the new receiver + task path.

Architecture decisions
***********************

Non-obvious design choices are recorded under ``docs/decisions/`` as ADRs.
Add a new one for any decision a future maintainer would otherwise have to
reverse-engineer from the diff.
