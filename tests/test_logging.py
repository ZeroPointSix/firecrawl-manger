from __future__ import annotations

import json
import logging

from app.observability.logging import JsonFormatter, PlainFormatter, request_id_ctx


def test_json_formatter_includes_request_id_and_redacts_fields():
    token = request_id_ctx.set("req_12345678")
    try:
        formatter = JsonFormatter({"authorization", "token"})

        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Authorization: Bearer abc.def_123",
            args=(),
            exc_info=None,
        )
        record.fields = {"authorization": "Bearer abc.def_123", "ok": "hello"}
        out = formatter.format(record)
        payload = json.loads(out)

        assert payload["request_id"] == "req_12345678"
        assert "abc.def_123" not in out
        assert payload["fields"]["authorization"] == "[REDACTED]"
        assert payload["fields"]["ok"] == "hello"
    finally:
        request_id_ctx.reset(token)


def test_plain_formatter_includes_request_id_and_redacts_fields():
    token = request_id_ctx.set("req_12345678")
    try:
        formatter = PlainFormatter({"authorization", "token"})
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Authorization: Bearer abc.def_123",
            args=(),
            exc_info=None,
        )
        record.fields = {"authorization": "Bearer abc.def_123", "ok": "hello"}
        out = formatter.format(record)

        assert "request_id=req_12345678" in out
        assert "abc.def_123" not in out
        assert '"authorization": "[REDACTED]"' in out
        assert '"ok": "hello"' in out
    finally:
        request_id_ctx.reset(token)
