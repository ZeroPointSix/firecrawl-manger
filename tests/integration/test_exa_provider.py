"""Integration tests for Exa provider support.

Tests cover:
- Control plane: create/list/import keys with provider field
- Control plane: batch mixed-provider rejection
- Control plane: credits unsupported for exa
- KeyPool: provider-isolated key selection
- Forwarder: provider-aware auth header and base_url
- Exa compat routes: /exa/search, /exa/findSimilar, /exa/contents, /exa/answer
- Middleware: _infer_api_endpoint for /exa/ prefix
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, hmac_sha256_hex
from app.middleware import _infer_api_endpoint


def _get_error_code(resp) -> str:
    """Extract error code from response, handling both flat and nested error formats."""
    data = resp.json()
    return data.get("code", "") or data.get("error", {}).get("code", "")


# ---------------------------------------------------------------------------
# Unit: _infer_api_endpoint
# ---------------------------------------------------------------------------

class TestInferApiEndpoint:
    def test_exa_search(self):
        assert _infer_api_endpoint("/exa/search") == "exa_search"

    def test_exa_findSimilar(self):
        assert _infer_api_endpoint("/exa/findSimilar") == "exa_findSimilar"

    def test_exa_contents(self):
        assert _infer_api_endpoint("/exa/contents") == "exa_contents"

    def test_exa_answer(self):
        assert _infer_api_endpoint("/exa/answer") == "exa_answer"

    def test_firecrawl_unchanged(self):
        assert _infer_api_endpoint("/v1/scrape") == "scrape"
        assert _infer_api_endpoint("/api/scrape") == "scrape"

    def test_unknown_prefix(self):
        assert _infer_api_endpoint("/admin/keys") is None


# ---------------------------------------------------------------------------
# Control Plane: provider CRUD
# ---------------------------------------------------------------------------

class TestControlPlaneProvider:
    def test_create_key_with_provider_firecrawl(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "fc-test-key-001", "provider": "firecrawl"},
            headers=admin_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["provider"] == "firecrawl"

    def test_create_key_with_provider_exa(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "exa-test-key-001", "provider": "exa"},
            headers=admin_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["provider"] == "exa"

    def test_create_key_invalid_provider(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "test-key-001", "provider": "invalid_provider"},
            headers=admin_headers(),
        )
        assert resp.status_code == 400
        code = _get_error_code(resp)
        assert "VALIDATION_ERROR" in code

    def test_create_key_default_provider_is_firecrawl(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "fc-default-key"},
            headers=admin_headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["provider"] == "firecrawl"

    def test_list_keys_filter_by_provider(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        # Create one firecrawl and one exa key
        tc.post("/admin/keys", json={"api_key": "fc-key-1"}, headers=admin_headers())
        tc.post("/admin/keys", json={"api_key": "exa-key-1", "provider": "exa"}, headers=admin_headers())

        # List all
        resp = tc.get("/admin/keys", headers=admin_headers())
        assert resp.status_code == 200
        all_keys = resp.json()["items"]
        assert len(all_keys) == 2

        # Filter by provider=exa
        resp = tc.get("/admin/keys", params={"provider": "exa"}, headers=admin_headers())
        assert resp.status_code == 200
        exa_keys = resp.json()["items"]
        assert len(exa_keys) == 1
        assert exa_keys[0]["provider"] == "exa"

        # Filter by provider=firecrawl
        resp = tc.get("/admin/keys", params={"provider": "firecrawl"}, headers=admin_headers())
        assert resp.status_code == 200
        fc_keys = resp.json()["items"]
        assert len(fc_keys) == 1
        assert fc_keys[0]["provider"] == "firecrawl"

    def test_import_keys_with_provider_exa(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        # Create a client first
        resp = tc.post("/admin/clients", json={"name": "test_client"}, headers=admin_headers())
        assert resp.status_code == 201
        client_id = resp.json()["client"]["id"]

        resp = tc.post(
            "/admin/keys/import-text",
            json={"client_id": client_id, "text": "exa-imported-key-1", "provider": "exa"},
            headers=admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["created"] >= 1

        # Verify the key has provider=exa
        resp = tc.get("/admin/keys", params={"provider": "exa"}, headers=admin_headers())
        assert resp.status_code == 200
        assert any(k["provider"] == "exa" for k in resp.json()["items"])


class TestControlPlaneCreditsProvider:
    def test_exa_key_credits_unsupported(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        # Create an exa key
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "exa-credits-test", "provider": "exa"},
            headers=admin_headers(),
        )
        key_id = resp.json()["id"]

        # GET credits should return UNSUPPORTED_PROVIDER_OPERATION
        resp = tc.get(f"/admin/keys/{key_id}/credits", headers=admin_headers())
        assert resp.status_code == 400
        assert _get_error_code(resp) == "UNSUPPORTED_PROVIDER_OPERATION"

    def test_exa_key_refresh_credits_unsupported(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/admin/keys",
            json={"api_key": "exa-refresh-test", "provider": "exa"},
            headers=admin_headers(),
        )
        key_id = resp.json()["id"]

        resp = tc.post(f"/admin/keys/{key_id}/credits/refresh", headers=admin_headers())
        assert resp.status_code == 400
        assert _get_error_code(resp) == "UNSUPPORTED_PROVIDER_OPERATION"


class TestControlPlaneBatchProvider:
    def test_batch_mixed_provider_test_rejected(self, tmp_path, make_app, admin_headers):
        app, config, secrets = make_app(tmp_path)
        tc = TestClient(app, raise_server_exceptions=False)

        # Create one firecrawl + one exa key
        r1 = tc.post("/admin/keys", json={"api_key": "fc-batch-1"}, headers=admin_headers())
        r2 = tc.post("/admin/keys", json={"api_key": "exa-batch-1", "provider": "exa"}, headers=admin_headers())
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]

        resp = tc.post(
            "/admin/keys/batch",
            json={"ids": [id1, id2], "test": {"mode": "scrape"}},
            headers=admin_headers(),
        )
        assert resp.status_code == 400
        assert _get_error_code(resp) == "MIXED_PROVIDER_BATCH"


# ---------------------------------------------------------------------------
# KeyPool: provider isolation
# ---------------------------------------------------------------------------

class TestKeyPoolProvider:
    def test_select_filters_by_provider(self, tmp_path, make_db, seed_api_key):
        config, engine, SessionLocal = make_db(tmp_path)
        db = SessionLocal()
        try:
            master_key = "master"
            mk = derive_master_key_bytes(master_key)

            # Seed one firecrawl key and one exa key
            seed_api_key(
                db,
                master_key=master_key,
                api_key_plain="fc-pool-key-1",
                api_key_hash=hmac_sha256_hex(mk, "fc-pool-key-1"),
                last4="ey-1",
                provider="firecrawl",
            )
            seed_api_key(
                db,
                master_key=master_key,
                api_key_plain="exa-pool-key-1",
                api_key_hash=hmac_sha256_hex(mk, "exa-pool-key-1"),
                last4="ey-1",
                provider="exa",
            )

            pool = KeyPool()

            # Select firecrawl should only get the firecrawl key
            selected = pool.select(db, config, provider="firecrawl")
            assert selected.api_key.provider == "firecrawl"

            # Select exa should only get the exa key
            selected = pool.select(db, config, provider="exa")
            assert selected.api_key.provider == "exa"
        finally:
            db.close()

    def test_select_no_exa_key_raises(self, tmp_path, make_db, seed_api_key):
        config, engine, SessionLocal = make_db(tmp_path)
        db = SessionLocal()
        try:
            master_key = "master"
            mk = derive_master_key_bytes(master_key)

            # Only seed firecrawl key
            seed_api_key(
                db,
                master_key=master_key,
                api_key_plain="fc-only-key",
                api_key_hash=hmac_sha256_hex(mk, "fc-only-key"),
                last4="nly1",
                provider="firecrawl",
            )

            pool = KeyPool()
            from app.errors import FcamError

            with pytest.raises(FcamError) as exc_info:
                pool.select(db, config, provider="exa")
            assert exc_info.value.code == "NO_KEY_CONFIGURED"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Exa compat routes
# ---------------------------------------------------------------------------

class TestExaCompatRoutes:
    @staticmethod
    def _exa_handler(request: httpx.Request) -> httpx.Response:
        """Mock Exa upstream that checks auth and returns OK."""
        api_key = request.headers.get("x-api-key")
        if not api_key:
            return httpx.Response(401, json={"error": "missing x-api-key"})
        path = request.url.path
        return httpx.Response(200, json={"success": True, "path": path})

    def _setup(self, tmp_path, make_app, seed_client, seed_api_key):
        def _enable_exa(config):
            config.providers.exa.enabled = True

        app, config, secrets = make_app(
            tmp_path,
            handler=self._exa_handler,
            config_mutate=_enable_exa,
        )
        db = app.state.db_session_factory()
        client, token = seed_client(db, master_key="master")
        mk = derive_master_key_bytes("master")
        seed_api_key(
            db,
            master_key="master",
            api_key_plain="exa-test-api-key",
            api_key_hash=hmac_sha256_hex(mk, "exa-test-api-key"),
            last4="akey",
            client_id=client.id,
            provider="exa",
        )
        db.close()
        return app, token

    def test_exa_search(self, tmp_path, make_app, seed_client, seed_api_key):
        app, token = self._setup(tmp_path, make_app, seed_client, seed_api_key)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/search",
            json={"query": "test query"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_exa_findSimilar(self, tmp_path, make_app, seed_client, seed_api_key):
        app, token = self._setup(tmp_path, make_app, seed_client, seed_api_key)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/findSimilar",
            json={"url": "https://example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_exa_contents(self, tmp_path, make_app, seed_client, seed_api_key):
        app, token = self._setup(tmp_path, make_app, seed_client, seed_api_key)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/contents",
            json={"ids": ["doc1"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_exa_answer(self, tmp_path, make_app, seed_client, seed_api_key):
        app, token = self._setup(tmp_path, make_app, seed_client, seed_api_key)
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/answer",
            json={"query": "what is python?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_exa_route_uses_x_api_key_header(self, tmp_path, make_app, seed_client, seed_api_key):
        """Verify the upstream request uses x-api-key, NOT Authorization Bearer."""
        captured_headers: dict[str, str] = {}

        def _capture_handler(request: httpx.Request) -> httpx.Response:
            for k, v in request.headers.items():
                captured_headers[k.lower()] = v
            return httpx.Response(200, json={"success": True})

        def _enable_exa(config):
            config.providers.exa.enabled = True

        app, config, secrets = make_app(
            tmp_path,
            handler=_capture_handler,
            config_mutate=_enable_exa,
        )
        db = app.state.db_session_factory()
        client, token = seed_client(db, master_key="master")
        mk = derive_master_key_bytes("master")
        seed_api_key(
            db,
            master_key="master",
            api_key_plain="exa-header-test",
            api_key_hash=hmac_sha256_hex(mk, "exa-header-test"),
            last4="test",
            client_id=client.id,
            provider="exa",
        )
        db.close()

        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/search",
            json={"query": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # The upstream should have received x-api-key, NOT authorization
        assert "x-api-key" in captured_headers
        assert captured_headers["x-api-key"] == "exa-header-test"
        assert "authorization" not in captured_headers


class TestExaDisabled:
    def test_exa_routes_not_registered_when_disabled(self, tmp_path, make_app):
        """When providers.exa.enabled is false, /exa/* routes should 404."""
        app, config, secrets = make_app(tmp_path)
        # exa is disabled by default
        assert not config.providers.exa.enabled
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.post(
            "/exa/search",
            json={"query": "test"},
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code in {404, 405}
