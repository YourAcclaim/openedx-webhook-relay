"""
Structured (JSON) logging support for this plugin's log lines.

``tasks.py`` and ``receivers.py`` already pass structured context via the
stdlib logging ``extra=`` kwarg (correlation_id, endpoint_id, event,
status, ...). By default those just get formatted into the plain-text
message per Django's normal logging config. If you want them as real JSON
fields (for a log pipeline that indexes on them), wire this formatter onto
this plugin's logger namespace.

Example Django/Tutor settings patch::

    LOGGING["formatters"]["owr_json"] = {
        "()": "openedx_webhook_relay.logging_utils.JSONFormatter",
    }
    LOGGING["handlers"]["owr_json_console"] = {
        "class": "logging.StreamHandler",
        "formatter": "owr_json",
    }
    LOGGING["loggers"]["openedx_webhook_relay"] = {
        "handlers": ["owr_json_console"],
        "level": "INFO",
        "propagate": False,
    }

See docs/decisions/0008-structured-logging-and-metrics.rst.
"""

import json
import logging

# Attributes every LogRecord has regardless of `extra`; anything else on the
# record is something a caller passed via `extra=` and should be surfaced.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    """Render each LogRecord as one JSON object per line."""

    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = str(value)
            payload[key] = value

        return json.dumps(payload, default=str, sort_keys=True)
