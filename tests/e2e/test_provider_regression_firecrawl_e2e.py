"""E2E regression tests ensuring Firecrawl paths remain functional after Exa provider addition.

These tests verify that the existing /api/*, /v1/*, /v2/* Firecrawl endpoints are not broken
by the multi-provider changes.

Set FCAM_E2E=1 to enable.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.e2e]


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@dataclass(frozen=True)
class _E2EServer:
    base_url: str
    admin_token: str


@pytest.fixture(scope="module")
def regression_server(tmp_path_factory: pytest.TempPathFactory) -> _E2EServer:
    """Start a FCAM server with Exa enabled alongside Firecrawl."""
    if not _env_flag("FCAM_E2E"):
        pytest.skip("需要设置环境变量 FCAM_E2E=1 才会运行 E2E 测试")

    tmp_dir = tmp_path_factory.mktemp("fcam-regression-e2e")
    db_path = tmp_dir / "regression_e2e.db"
    log_path = tmp_dir / "uvicorn.log"

    admin_token = os.environ.get("FCAM_E2E_ADMIN_TOKEN") or "e2e_admin"
    master_key = os.environ.get("FCAM_E2E_MASTER_KEY") or "e2e_master_key_32_bytes_minimum____"

    env = os.environ.copy()
    env.update(
        {
            "FCAM_DATABASE__URL": _sqlite_url(db_path),
            "FCAM_ADMIN_TOKEN": admin_token,
            "FCAM_MASTER_KEY": master_key,
            "FCAM_SERVER__ENABLE_DOCS": "false",
            "FCAM_OBSERVABILITY__METRICS_ENABLED": "false",
            "FCAM_LOGGING__FORMAT": "plain",
            # Firecrawl upstream points to a non-routable address
            "FCAM_FIRECRAWL__BASE_URL": "http://127.0.0.1:9",
            "FCAM_FIRECRAWL__TIMEOUT": "1",
            "FCAM_FIRECRAWL__MAX_RETRIES": "0",
            # Enable Exa to test coexistence
            "FCAM_PROVIDERS__EXA__ENABLED": "true",
            "FCAM_PROVIDERS__EXA__BASE_URL": "http://127.0.0.1:9",
            "FCAM_PROVIDERS__EXA__TIMEOUT": "1",
        }
    )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_REPO_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    host = "127.0.0.1"
    port = _free_port()
    base_url = f"http://{host}:{port}"

    log_fp = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", host, "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base_url}/healthz", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            proc.wait(timeout=3)
            log_fp.close()
            server_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            raise RuntimeError(f"FCAM regression E2E server 未就绪\n{server_log}")

        yield _E2EServer(base_url=base_url, admin_token=admin_token)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        log_fp.close()


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def fc_client_token(regression_server: _E2EServer) -> str:
    """Create a client with a Firecrawl key and return the client token."""
    base = regression_server.base_url
    headers = _admin_headers(regression_server.admin_token)

    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"fc-regr-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_token = r.json()["token"]
    client_id = r.json()["client"]["id"]

    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"fc-regr-{uuid.uuid4().hex}",
            "client_id": client_id,
            "provider": "firecrawl",
            "name": "fc-regression-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201

    return client_token


# ---------------------------------------------------------------------------
# Firecrawl route regression tests
# ---------------------------------------------------------------------------


def test_api_scrape_route_exists(regression_server: _E2EServer, fc_client_token: str):
    """POST /api/scrape should be routable (upstream may fail but route exists)."""
    r = httpx.post(
        f"{regression_server.base_url}/api/scrape",
        headers={"Authorization": f"Bearer {fc_client_token}"},
        json={"url": "https://example.com"},
        timeout=10.0,
    )
    assert r.status_code != 404, f"/api/scrape returned 404: {r.text}"


def test_v1_scrape_route_exists(regression_server: _E2EServer, fc_client_token: str):
    """POST /v1/scrape should be routable."""
    r = httpx.post(
        f"{regression_server.base_url}/v1/scrape",
        headers={"Authorization": f"Bearer {fc_client_token}"},
        json={"url": "https://example.com"},
        timeout=10.0,
    )
    assert r.status_code != 404, f"/v1/scrape returned 404: {r.text}"


def test_v2_scrape_route_exists(regression_server: _E2EServer, fc_client_token: str):
    """POST /v2/scrape should be routable."""
    r = httpx.post(
        f"{regression_server.base_url}/v2/scrape",
        headers={"Authorization": f"Bearer {fc_client_token}"},
        json={"url": "https://example.com"},
        timeout=10.0,
    )
    assert r.status_code != 404, f"/v2/scrape returned 404: {r.text}"


def test_firecrawl_key_default_provider(regression_server: _E2EServer):
    """Creating a key without provider should default to 'firecrawl'."""
    base = regression_server.base_url
    headers = _admin_headers(regression_server.admin_token)

    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"fc-default-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_id = r.json()["client"]["id"]

    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"fc-default-{uuid.uuid4().hex}",
            "client_id": client_id,
            "name": "no-provider-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201
    assert r.json()["provider"] == "firecrawl"


def test_firecrawl_key_credits_work(regression_server: _E2EServer):
    """Firecrawl key credits endpoint should NOT return UNSUPPORTED_PROVIDER_OPERATION."""
    base = regression_server.base_url
    headers = _admin_headers(regression_server.admin_token)

    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"fc-credits-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_id = r.json()["client"]["id"]

    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"fc-credits-{uuid.uuid4().hex}",
            "client_id": client_id,
            "provider": "firecrawl",
            "name": "fc-credits-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201
    key_id = r.json()["id"]

    # Firecrawl credits should work (may return cached/empty but not UNSUPPORTED)
    r = httpx.get(f"{base}/admin/keys/{key_id}/credits", headers=headers, timeout=10.0)
    assert "UNSUPPORTED_PROVIDER_OPERATION" not in r.text


def test_healthz_still_works(regression_server: _E2EServer):
    """Health check should still work with Exa provider enabled."""
    r = httpx.get(f"{regression_server.base_url}/healthz", timeout=5.0)
    assert r.status_code == 200
