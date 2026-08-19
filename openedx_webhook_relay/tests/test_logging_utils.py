"""Tests for the JSON log formatter."""

import io
import json
import logging

from openedx_webhook_relay.logging_utils import JSONFormatter


def _log_and_capture(caplog_handler_stream, extra=None):
    logger = logging.getLogger("openedx_webhook_relay.test_logging_utils")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(caplog_handler_stream)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.info("delivery outcome", extra=extra or {})
    finally:
        logger.removeHandler(handler)


def test_json_formatter_includes_standard_fields():
    stream = io.StringIO()
    _log_and_capture(stream, extra={"correlation_id": "abc", "status": "succeeded"})
    parsed = json.loads(stream.getvalue().strip())

    assert parsed["message"] == "delivery outcome"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "openedx_webhook_relay.test_logging_utils"
    assert parsed["correlation_id"] == "abc"
    assert parsed["status"] == "succeeded"
    assert "timestamp" in parsed


def test_json_formatter_handles_non_serializable_extra_values():
    class Unserializable:
        def __str__(self):
            return "unserializable-repr"

    stream = io.StringIO()
    _log_and_capture(stream, extra={"weird": Unserializable()})
    parsed = json.loads(stream.getvalue().strip())
    assert parsed["weird"] == "unserializable-repr"


def test_json_formatter_output_is_one_line_per_record():
    stream = io.StringIO()
    _log_and_capture(stream, extra={"a": 1})
    _log_and_capture(stream, extra={"a": 2})
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    # each call adds its own handler/stream in this test helper, so just
    # confirm each individual write is valid, single-line JSON
    for line in lines:
        json.loads(line)
