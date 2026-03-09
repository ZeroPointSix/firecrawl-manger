"""E2E tests for Exa SDK compatibility.

These tests require a real running FCAM instance. They:
1. Start a FCAM server process with Exa enabled
2. Create an Exa key via the control plane
3. Verify Exa endpoints forward correctly

Set FCAM_E2E=1 to enable.
Set FCAM_E2E_ALLOW_UPSTREAM=1 and FCAM_E2E_EXA_API_KEY=... for real upstream calls.
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
def exa_e2e_server(tmp_path_factory: pytest.TempPathFactory) -> _E2EServer:
    if not _env_flag("FCAM_E2E"):
        pytest.skip("需要设置环境变量 FCAM_E2E=1 才会运行 E2E 测试")

    tmp_dir = tmp_path_factory.mktemp("fcam-exa-e2e")
    db_path = tmp_dir / "exa_e2e.db"
    log_path = tmp_dir / "uvicorn.log"

    admin_token = os.environ.get("FCAM_E2E_ADMIN_TOKEN") or "e2e_admin"
    master_key = os.environ.get("FCAM_E2E_MASTER_KEY") or "e2e_master_key_32_bytes_minimum____"

    allow_upstream = _env_flag("FCAM_E2E_ALLOW_UPSTREAM")
    exa_base_url = "https://api.exa.ai" if allow_upstream else "http://127.0.0.1:9"

    env = os.environ.copy()
    env.update(
        {
            "FCAM_DATABASE__URL": _sqlite_url(db_path),
            "FCAM_ADMIN_TOKEN": admin_token,
            "FCAM_MASTER_KEY": master_key,
            "FCAM_SERVER__ENABLE_DOCS": "false",
            "FCAM_OBSERVABILITY__METRICS_ENABLED": "false",
            "FCAM_LOGGING__FORMAT": "plain",
            "FCAM_FIRECRAWL__BASE_URL": "http://127.0.0.1:9",
            "FCAM_FIRECRAWL__TIMEOUT": "1",
            "FCAM_FIRECRAWL__MAX_RETRIES": "0",
            # Enable Exa provider
            "FCAM_PROVIDERS__EXA__ENABLED": "true",
            "FCAM_PROVIDERS__EXA__BASE_URL": exa_base_url,
            "FCAM_PROVIDERS__EXA__TIMEOUT": "15" if allow_upstream else "1",
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
            raise RuntimeError(f"FCAM Exa E2E server 未就绪\n{server_log}")

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


# ---------------------------------------------------------------------------
# Tests: Exa Key management via control plane
# ---------------------------------------------------------------------------


def test_exa_key_crud(exa_e2e_server: _E2EServer):
    """Create, list, and delete an Exa key via the control plane."""
    base = exa_e2e_server.base_url
    headers = _admin_headers(exa_e2e_server.admin_token)

    # Create client
    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"exa-e2e-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_id = r.json()["client"]["id"]

    # Create Exa key
    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"exa-e2e-{uuid.uuid4().hex}",
            "client_id": client_id,
            "provider": "exa",
            "name": "exa-e2e-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201
    key_data = r.json()
    assert key_data["provider"] == "exa"
    key_id = key_data["id"]

    # List with provider filter
    r = httpx.get(
        f"{base}/admin/keys",
        headers=headers,
        params={"client_id": client_id, "provider": "exa"},
        timeout=10.0,
    )
    assert r.status_code == 200
    keys = r.json()["items"]
    assert all(k["provider"] == "exa" for k in keys)

    # Delete key
    r = httpx.delete(f"{base}/admin/keys/{key_id}", headers=headers, timeout=10.0)
    assert r.status_code == 204


def test_exa_credits_returns_unsupported(exa_e2e_server: _E2EServer):
    """Exa key credits endpoint should return UNSUPPORTED_PROVIDER_OPERATION."""
    base = exa_e2e_server.base_url
    headers = _admin_headers(exa_e2e_server.admin_token)

    # Create client + Exa key
    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"exa-credits-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_id = r.json()["client"]["id"]

    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"exa-credits-{uuid.uuid4().hex}",
            "client_id": client_id,
            "provider": "exa",
            "name": "exa-credits-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201
    key_id = r.json()["id"]

    # GET credits should fail for Exa
    r = httpx.get(f"{base}/admin/keys/{key_id}/credits", headers=headers, timeout=10.0)
    assert r.status_code == 400
    assert "UNSUPPORTED_PROVIDER_OPERATION" in r.text


def test_exa_search_endpoint_exists(exa_e2e_server: _E2EServer):
    """POST /exa/search should be routable (even if upstream fails)."""
    base = exa_e2e_server.base_url
    headers = _admin_headers(exa_e2e_server.admin_token)

    # Create client + Exa key
    r = httpx.post(
        f"{base}/admin/clients",
        headers=headers,
        json={"name": f"exa-search-{uuid.uuid4().hex[:8]}", "daily_quota": 100, "is_active": True},
        timeout=10.0,
    )
    assert r.status_code == 201
    client_token = r.json()["token"]
    client_id = r.json()["client"]["id"]

    r = httpx.post(
        f"{base}/admin/keys",
        headers=headers,
        json={
            "api_key": f"exa-search-{uuid.uuid4().hex}",
            "client_id": client_id,
            "provider": "exa",
            "name": "exa-search-key",
            "is_active": True,
        },
        timeout=10.0,
    )
    assert r.status_code == 201

    # POST /exa/search — upstream may be unreachable but route should exist (not 404)
    r = httpx.post(
        f"{base}/exa/search",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"query": "test"},
        timeout=10.0,
    )
    # Should not be 404 (route exists) or 405 (method allowed)
    assert r.status_code != 404, f"Route /exa/search not found: {r.status_code}"


def test_exa_non_whitelisted_path_blocked(exa_e2e_server: _E2EServer):
    """/exa/research (non-P0) should be blocked."""
    base = exa_e2e_server.base_url
    r = httpx.post(
        f"{base}/exa/research",
        headers={"Authorization": "Bearer dummy"},
        json={},
        timeout=10.0,
    )
    # Should be 404 or similar — not a valid route
    assert r.status_code in {404, 405}, f"Non-P0 path should be blocked: {r.status_code}"
