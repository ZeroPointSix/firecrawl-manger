from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.skip(reason="需要外部 OpenAPI 规范文件")]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V2_OPENAPI_PATH = _REPO_ROOT / "api-reference/firecrawl-docs/api-reference/v2-openapi.json"
_PATH_PARAM_RE = re.compile(r"{[^}]+}")


def _sample_path(path_template: str) -> str:
    return _PATH_PARAM_RE.sub("abc123", path_template)


def _load_v2_operations() -> list[tuple[str, str]]:
    data = json.loads(_V2_OPENAPI_PATH.read_text(encoding="utf-8"))
    operations: list[tuple[str, str]] = []
    for path, methods in (data.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method in methods.keys():
            m = str(method).lower()
            if m in {"get", "post", "delete"}:
                operations.append((m.upper(), str(path)))
    return sorted(operations, key=lambda x: (x[1], x[0]))


def _payload_for(path: str) -> dict:
    if path.startswith("/search"):
        return {"query": "firecrawl"}
    if path.startswith("/extract"):
        return {"urls": ["https://example.com"], "prompt": "Extract title"}
    if path.startswith("/batch/scrape"):
        return {"urls": ["https://example.com"]}
    if path.startswith("/browser") and path.endswith("/execute"):
        return {"code": "console.log('ok')", "language": "node"}
    if path.startswith("/browser"):
        return {"ttl": 60}
    if path.startswith("/agent"):
        return {"prompt": "Hello"}
    if path.startswith("/crawl/params-preview"):
        return {"url": "https://example.com", "limit": 1}
    if path.startswith("/crawl"):
        return {"url": "https://example.com", "limit": 1}
    if path.startswith("/map") or path.startswith("/scrape"):
        return {"url": "https://example.com"}
    return {"ok": True}


def _make_app(tmp_path, *, handler):
    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test"
    config.firecrawl.max_retries = 0

    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    transport = httpx.MockTransport(handler)
    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=transport,
    )

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        token = "fcam_client_token"
        token_hash = hmac_sha256_hex(derive_master_key_bytes(secrets.master_key), token)
        c = Client(
            name="svc",
            token_hash=token_hash,
            is_active=True,
            daily_quota=10_000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            rate_limit_per_min=10_000,
            max_concurrent=10,
        )
        db.add(c)
        db.flush()

        key_bytes = derive_master_key_bytes(secrets.master_key)
        k = ApiKey(
            client_id=c.id,
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
            api_key_hash="h1",
            api_key_last4="0001",
            is_active=True,
            status="active",
            daily_quota=100_000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
            rate_limit_per_min=10_000,
        )
        db.add(k)
        db.commit()

    return app, token


def test_firecrawl_v2_openapi_operations_are_supported_and_forwarded(tmp_path):
    operations = _load_v2_operations()
    assert operations, "v2-openapi.json 未包含任何可识别的 operations"

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method.upper(), request.url.path))
        return httpx.Response(
            200,
            json={"ok": True, "method": request.method.upper(), "path": request.url.path},
        )

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    expected: list[tuple[str, str]] = []
    with TestClient(app) as client:
        for method, path in operations:
            gateway_path = f"/v2{_sample_path(path)}"
            expected.append((method, gateway_path))

            if method == "GET":
                resp = client.get(gateway_path, headers=headers)
            elif method == "POST":
                resp = client.post(gateway_path, headers=headers, json=_payload_for(path))
            elif method == "DELETE":
                resp = client.delete(gateway_path, headers=headers)
            else:
                raise AssertionError(f"Unexpected method: {method}")

            assert resp.status_code == 200, (method, gateway_path, resp.text)
            body = resp.json()
            assert body["method"] == method
            assert body["path"] == gateway_path

    assert seen == expected

