from __future__ import annotations

import gzip
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from app.config import Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.time import today_in_timezone
from app.db.models import ApiKey
from app.errors import FcamError

pytestmark = pytest.mark.integration


@contextmanager
def _db(tmp_path, *, make_db) -> Any:  # noqa: ANN401
    config, _, SessionLocal = make_db(tmp_path, db_name="fwd.db")
    with SessionLocal() as db:
        yield config, db


def test_forwarder_retries_on_429_and_switches_key(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )
        k2 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-2",
            api_key_hash="h2",
            last4="0002",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate"})
        if auth == "Bearer fc-key-2":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500, json={"error": "unexpected"})

        transport = httpx.MockTransport(handler)
        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=transport,
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )

        assert result.upstream_status_code == 200
        assert result.api_key_id == k2.id
        assert result.retry_count == 1

        db.refresh(k1)
        assert k1.status == "cooling"
        assert k1.cooldown_until is not None

        db.refresh(k2)
        assert k2.total_requests == 1
        assert k2.daily_usage == 1


def test_forwarder_disables_key_on_401_then_uses_next(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )
        k2 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-2",
            api_key_hash="h2",
            last4="0002",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"ok": True})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )
        assert result.upstream_status_code == 200
        assert result.api_key_id == k2.id

        db.refresh(k1)
        assert k1.status == "disabled"
        assert k1.is_active is False


def test_forwarder_timeout_raises_gateway_error(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(FcamError) as e:
            fwd.forward(
                db=db,
                request_id="req_12345678",
                client=client,
                method="POST",
                upstream_path="/scrape",
                json_body={"url": "https://example.com"},
                inbound_headers={"content-type": "application/json"},
            )
        assert e.value.code == "UPSTREAM_TIMEOUT"
        assert e.value.status_code == 504


def test_forwarder_marks_failed_after_threshold(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        config.firecrawl.failure_threshold = 1
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )
        assert result.upstream_status_code == 500

        db.refresh(k1)
        assert k1.status == "failed"
        assert k1.cooldown_until is not None


def test_forwarder_disables_key_if_decrypt_fails_and_uses_next(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)

        k_bad = ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"not-a-valid-aesgcm-blob",
            api_key_hash="hbad",
            api_key_last4="0001",
            is_active=True,
            status="active",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=1,
        )
        db.add(k_bad)
        db.commit()
        db.refresh(k_bad)

        k_good = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-2",
            api_key_hash="h2",
            last4="0002",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization")
            if auth == "Bearer fc-key-2":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(500, json={"error": "unexpected"})

        transport = httpx.MockTransport(handler)
        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=transport,
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )

        assert result.upstream_status_code == 200
        assert result.api_key_id == k_good.id

        db.refresh(k_bad)
        assert k_bad.status == "decrypt_failed"
        assert k_bad.is_active is False


def test_forwarder_drops_content_encoding_and_length_headers(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        raw = b'{"ok":true,"data":"hello"}' * 50
        gz = gzip.compress(raw)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-encoding": "gzip",
                    "content-length": str(len(gz)),
                },
                content=gz,
            )

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
        )

        resp = result.response
        assert resp.status_code == 200
        assert resp.body == raw
        assert resp.headers.get("content-encoding") is None
        assert int(resp.headers["content-length"]) == len(raw)


def test_forwarder_http_error_raises_upstream_unavailable(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed", request=request)

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(FcamError) as exc:
            fwd.forward(
                db=db,
                request_id="req_12345678",
                client=client,
                method="POST",
                upstream_path="/scrape",
                json_body={"url": "https://example.com"},
                inbound_headers={"content-type": "application/json"},
            )

        assert exc.value.status_code == 503
        assert exc.value.code == "UPSTREAM_UNAVAILABLE"


def test_forwarder_raises_all_keys_busy_when_rate_limited(
    tmp_path, make_db, seed_client, seed_api_key
):
    class _DenyRateLimiter:
        def allow(self, key: str, rate_limit_per_min: int):  # noqa: ANN001
            return False, 1

    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            key_rate_limiter=_DenyRateLimiter(),
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(FcamError) as exc:
            fwd.forward(
                db=db,
                request_id="req_12345678",
                client=client,
                method="POST",
                upstream_path="/scrape",
                json_body={"url": "https://example.com"},
                inbound_headers={"content-type": "application/json"},
            )

        assert exc.value.status_code == 503
        assert exc.value.code == "ALL_KEYS_BUSY"


def test_forwarder_pinned_key_raises_all_keys_busy_when_rate_limited(
    tmp_path, make_db, seed_client, seed_api_key
):
    class _DenyRateLimiter:
        def allow(self, key: str, rate_limit_per_min: int):  # noqa: ANN001
            return False, 1

    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not call upstream when rate-limited locally")

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            key_rate_limiter=_DenyRateLimiter(),
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(FcamError) as exc:
            fwd.forward(
                db=db,
                request_id="req_12345678",
                client=client,
                method="POST",
                upstream_path="/scrape",
                json_body={"url": "https://example.com"},
                inbound_headers={"content-type": "application/json"},
                pinned_api_key_id=k1.id,
            )

        assert exc.value.status_code == 503
        assert exc.value.code == "ALL_KEYS_BUSY"


def test_forwarder_pinned_key_raises_all_keys_busy_when_concurrency_exceeded(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        concurrency = ConcurrencyManager()
        lease = concurrency.try_acquire(str(k1.id), 1)
        assert lease is not None

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not call upstream when key concurrency exceeded")

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=concurrency,
            transport=httpx.MockTransport(handler),
        )

        try:
            with pytest.raises(FcamError) as exc:
                fwd.forward(
                    db=db,
                    request_id="req_12345678",
                    client=client,
                    method="POST",
                    upstream_path="/scrape",
                    json_body={"url": "https://example.com"},
                    inbound_headers={"content-type": "application/json"},
                    pinned_api_key_id=k1.id,
                )
            assert exc.value.status_code == 503
            assert exc.value.code == "ALL_KEYS_BUSY"
        finally:
            lease.release()


def test_forwarder_pinned_key_disables_on_401_and_returns_response(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.max_retries = 0
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        k1 = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer fc-key-1"
            return httpx.Response(401, json={"error": "bad key"})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        result = fwd.forward(
            db=db,
            request_id="req_12345678",
            client=client,
            method="POST",
            upstream_path="/scrape",
            json_body={"url": "https://example.com"},
            inbound_headers={"content-type": "application/json"},
            pinned_api_key_id=k1.id,
        )

        assert result.upstream_status_code == 401
        assert result.api_key_id == k1.id

        db.refresh(k1)
        assert k1.status == "disabled"
        assert k1.is_active is False


def test_forwarder_test_key_rejects_unsupported_mode(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(FcamError) as exc:
            fwd.test_key(db=db, request_id="req_12345678", key=key, mode="crawl")
        assert exc.value.status_code == 400
        assert exc.value.code == "VALIDATION_ERROR"


def test_forwarder_test_key_tries_v2_then_v1_when_base_url_has_v2_suffix(
    tmp_path, make_db, seed_client, seed_api_key
):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.base_url = "http://firecrawl.test/v2"
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        seen_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            if request.url.path == "/v2/scrape":
                return httpx.Response(404, json={"error": "not found"})
            if request.url.path == "/v1/scrape":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(500, json={"error": "unexpected"})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is True
        assert out.upstream_status_code == 200
        assert seen_paths[:2] == ["/v2/scrape", "/v1/scrape"]


def test_forwarder_test_key_disables_key_when_decrypt_fails(tmp_path, make_db, seed_client):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)

        bad_key = ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"not-a-valid-aesgcm-blob",
            api_key_hash="hbad",
            api_key_last4="0001",
            is_active=True,
            status="active",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=1,
        )
        db.add(bad_key)
        db.commit()
        db.refresh(bad_key)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not call upstream when decrypt fails")

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=bad_key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is False
        assert out.upstream_status_code is None

        db.refresh(bad_key)
        assert bad_key.status == "decrypt_failed"
        assert bad_key.is_active is False


def test_forwarder_test_key_disables_key_on_401(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is False
        assert out.upstream_status_code == 401

        db.refresh(key)
        assert key.status == "disabled"
        assert key.is_active is False


def test_forwarder_test_key_timeout_returns_not_ok(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is False
        assert out.upstream_status_code is None


def test_forwarder_test_key_http_error_returns_not_ok(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connect failed", request=request)

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is False
        assert out.upstream_status_code is None


def test_forwarder_test_key_records_failure_on_5xx(tmp_path, make_db, seed_client, seed_api_key):
    secrets = Secrets(admin_token="admin", master_key="master")

    with _db(tmp_path, make_db=make_db) as (config, db):
        config.firecrawl.failure_threshold = 1
        client, _ = seed_client(db, master_key=secrets.master_key, daily_quota=10, daily_usage=0)
        key = seed_api_key(
            db,
            master_key=secrets.master_key,
            api_key_plain="fc-key-1",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            daily_quota=5,
            max_concurrent=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        fwd = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

        out = fwd.test_key(
            db=db,
            request_id="req_12345678",
            key=key,
            mode="scrape",
            test_url="https://example.com",
        )
        assert out.ok is False
        assert out.upstream_status_code == 500

        db.refresh(key)
        assert key.status == "failed"
        assert key.cooldown_until is not None
