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
_E2E_ENV_FILE = _REPO_ROOT / ".env.e2e"

pytestmark = pytest.mark.e2e


def _load_env_file(path: Path, *, allowed_keys: set[str]) -> None:
    if not path.exists():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return

    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, value = s.split("=", 1)
        k = key.strip()
        if k not in allowed_keys:
            continue
        if os.environ.get(k):
            continue

        v = value.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if v:
            os.environ[k] = v


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class _E2EServer:
    base_url: str
    admin_token: str


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory: pytest.TempPathFactory) -> _E2EServer:
    _load_env_file(
        _E2E_ENV_FILE,
        allowed_keys={
            "FCAM_E2E_FIRECRAWL_API_KEY",
            "FCAM_E2E_FIRECRAWL_BASE_URL",
            "FCAM_E2E_SCRAPE_URL",
            "FCAM_E2E_ADMIN_TOKEN",
            "FCAM_E2E_MASTER_KEY",
            "FCAM_E2E_REMOTE_URL",
        },
    )

    if not _env_flag("FCAM_E2E"):
        pytest.skip("需要设置环境变量 FCAM_E2E=1 才会运行真实 API 的 E2E 测试")

    # 如果设置了 FCAM_E2E_REMOTE_URL，则使用远程服务器而不是启动本地服务器
    remote_url = os.environ.get("FCAM_E2E_REMOTE_URL")
    if remote_url:
        admin_token = os.environ.get("FCAM_E2E_ADMIN_TOKEN") or "e2e_admin"
        base_url = remote_url.rstrip("/")

        # 验证远程服务器是否可访问
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=10.0)
            if r.status_code != 200:
                pytest.skip(f"远程服务器健康检查失败: {base_url}/healthz 返回 {r.status_code}")
        except Exception as exc:
            pytest.skip(f"无法连接到远程服务器 {base_url}: {exc}")

        yield _E2EServer(base_url=base_url, admin_token=admin_token)
        return

    # 本地服务器模式（原有逻辑）
    tmp_dir = tmp_path_factory.mktemp("fcam-e2e")
    db_path = tmp_dir / "e2e.db"
    log_path = tmp_dir / "uvicorn.log"

    admin_token = os.environ.get("FCAM_E2E_ADMIN_TOKEN") or "e2e_admin"
    master_key = os.environ.get("FCAM_E2E_MASTER_KEY") or "e2e_master_key_32_bytes_minimum____"

    env = os.environ.copy()
    allow_upstream = _env_flag("FCAM_E2E_ALLOW_UPSTREAM")
    firecrawl_base_url = os.environ.get("FCAM_E2E_FIRECRAWL_BASE_URL") or "https://api.firecrawl.dev/v1"

    env.update(
        {
            "FCAM_DATABASE__PATH": db_path.as_posix(),
            "FCAM_ADMIN_TOKEN": admin_token,
            "FCAM_MASTER_KEY": master_key,
            "FCAM_SERVER__ENABLE_DOCS": "false",
            "FCAM_OBSERVABILITY__METRICS_ENABLED": "false",
            "FCAM_LOGGING__FORMAT": "plain",
            "FCAM_FIRECRAWL__BASE_URL": firecrawl_base_url if allow_upstream else "http://127.0.0.1:9/v1",
            "FCAM_FIRECRAWL__TIMEOUT": "20" if allow_upstream else "1",
            "FCAM_FIRECRAWL__MAX_RETRIES": "0",
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
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.time() + 15
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base_url}/healthz", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception as exc:
                last_err = exc
            time.sleep(0.2)
        else:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
            log_fp.close()
            server_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            raise RuntimeError(f"FCAM E2E server 未就绪: {last_err}\n{server_log}") from last_err

        yield _E2EServer(base_url=base_url, admin_token=admin_token)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        log_fp.close()


@pytest.fixture()
def http(e2e_server: _E2EServer):
    with httpx.Client(base_url=e2e_server.base_url, timeout=10.0) as client:
        yield client


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_e2e_logs_pagination_level_and_q(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_token = r_client.json()["token"]

    for _ in range(25):
        r = http.get("/api/crawl/e2e-1")
        assert r.status_code == 401

    r_no_key = http.get("/api/crawl/e2e-2", headers=_client_headers(client_token))
    assert r_no_key.status_code == 503

    r_warn_p1 = http.get("/admin/logs", headers=admin_headers, params={"limit": 20, "level": "warn"})
    assert r_warn_p1.status_code == 200
    b1 = r_warn_p1.json()
    assert b1["has_more"] is True
    assert len(b1["items"]) == 20
    assert all(i["level"] == "warn" for i in b1["items"])

    cursor = b1["next_cursor"]
    r_warn_p2 = http.get(
        "/admin/logs",
        headers=admin_headers,
        params={"limit": 20, "cursor": cursor, "level": "warn"},
    )
    assert r_warn_p2.status_code == 200
    b2 = r_warn_p2.json()
    assert b2["has_more"] is False
    assert len(b2["items"]) == 5
    assert all(i["level"] == "warn" for i in b2["items"])

    r_err = http.get(
        "/admin/logs",
        headers=admin_headers,
        params={"level": "error", "q": "no_key_configured"},
    )
    assert r_err.status_code == 200
    berr = r_err.json()
    assert any(i.get("error_message") == "NO_KEY_CONFIGURED" for i in berr["items"])

    r_warn_search = http.get(
        "/admin/logs",
        headers=admin_headers,
        params={"level": "warn", "q": "client_unauthorized"},
    )
    assert r_warn_search.status_code == 200
    bw = r_warn_search.json()
    assert any(i.get("error_message") == "CLIENT_UNAUTHORIZED" for i in bw["items"])


def test_e2e_audit_logs_pagination_and_filters(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    created_ids: list[int] = []
    for _ in range(3):
        client_name = f"e2e-audit-{uuid.uuid4().hex[:10]}"
        r = http.post(
            "/admin/clients",
            headers=admin_headers,
            json={
                "name": client_name,
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": True,
            },
        )
        assert r.status_code == 201
        created_ids.append(int(r.json()["client"]["id"]))

    r1 = http.get("/admin/audit-logs", headers=admin_headers, params={"limit": 2, "action": "client.create"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert len(b1["items"]) == 2
    assert b1["has_more"] is True
    assert all(i["action"] == "client.create" for i in b1["items"])

    cursor = b1["next_cursor"]
    r2 = http.get(
        "/admin/audit-logs",
        headers=admin_headers,
        params={"limit": 20, "cursor": cursor, "action": "client.create"},
    )
    assert r2.status_code == 200
    b2 = r2.json()
    ids = {int(i["resource_id"]) for i in b1["items"] + b2["items"] if i.get("resource_id")}
    assert set(created_ids).issubset(ids)


def test_e2e_client_rotate_token_invalidates_old_token(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-rotate-{uuid.uuid4().hex[:10]}"
    r_create = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_create.status_code == 201
    client_id = int(r_create.json()["client"]["id"])
    token_old = r_create.json()["token"]

    r_old_ok = http.get("/api/crawl/e2e-rotate", headers=_client_headers(token_old))
    assert r_old_ok.status_code == 503

    r_rotate = http.post(f"/admin/clients/{client_id}/rotate", headers=admin_headers)
    assert r_rotate.status_code == 200
    token_new = r_rotate.json()["token"]

    r_old = http.get("/api/crawl/e2e-rotate", headers=_client_headers(token_old))
    assert r_old.status_code == 401

    r_new = http.get("/api/crawl/e2e-rotate", headers=_client_headers(token_new))
    assert r_new.status_code == 503


def test_e2e_admin_keys_crud(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-key-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_id = int(r_client.json()["client"]["id"])

    key_value = f"fc-e2e-{uuid.uuid4().hex}"
    r_key = http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": key_value,
            "client_id": client_id,
            "name": "e2e",
            "plan_type": "free",
            "daily_quota": 5,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201
    key_id = int(r_key.json()["id"])

    r_list = http.get("/admin/keys", headers=admin_headers, params={"client_id": client_id})
    assert r_list.status_code == 200
    assert any(int(i["id"]) == key_id for i in r_list.json()["items"])

    r_update = http.put(
        f"/admin/keys/{key_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert r_update.status_code == 200
    assert r_update.json()["is_active"] is False
    assert r_update.json()["status"] == "disabled"

    r_purge = http.delete(f"/admin/keys/{key_id}/purge", headers=admin_headers)
    assert r_purge.status_code == 204

    r_list2 = http.get("/admin/keys", headers=admin_headers, params={"client_id": client_id})
    assert r_list2.status_code == 200
    assert not any(int(i["id"]) == key_id for i in r_list2.json()["items"])


def test_e2e_firecrawl_compat_v1_scrape_rejected_without_token_is_logged(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    request_id = f"e2eV1NoAuth_{uuid.uuid4().hex[:24]}"
    r = http.post("/v1/scrape", headers={"X-Request-Id": request_id}, json={"url": "https://example.com"})
    assert r.status_code == 401
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Missing or invalid client token"

    r_log = http.get("/admin/logs", headers=admin_headers, params={"request_id": request_id})
    assert r_log.status_code == 200
    items = r_log.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["endpoint"] == "scrape"
    assert item["status_code"] == 401
    assert item["level"] == "warn"
    assert item["error_message"] == "CLIENT_UNAUTHORIZED"
    assert item.get("error_details")
    assert "CLIENT_UNAUTHORIZED" in item["error_details"]


def test_e2e_firecrawl_compat_v1_scrape_no_key_configured_is_logged(http: httpx.Client, e2e_server: _E2EServer):
    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-v1-nokey-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_token = r_client.json()["token"]

    request_id = f"e2eV1NoKey_{uuid.uuid4().hex[:24]}"
    r = http.post(
        "/v1/scrape",
        headers={**_client_headers(client_token), "X-Request-Id": request_id},
        json={"url": "https://example.com"},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "No key configured for client"

    r_log = http.get("/admin/logs", headers=admin_headers, params={"request_id": request_id})
    assert r_log.status_code == 200
    items = r_log.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["endpoint"] == "scrape"
    assert item["status_code"] == 503
    assert item["level"] == "error"
    assert item["error_message"] == "NO_KEY_CONFIGURED"
    assert item.get("error_details")
    assert "NO_KEY_CONFIGURED" in item["error_details"]


@pytest.mark.upstream
def test_e2e_firecrawl_compat_scrape_success_with_real_upstream(http: httpx.Client, e2e_server: _E2EServer):
    if not _env_flag("FCAM_E2E_ALLOW_UPSTREAM"):
        pytest.skip("需要设置 FCAM_E2E_ALLOW_UPSTREAM=1 才会执行真实上游调用")

    firecrawl_api_key = os.environ.get("FCAM_E2E_FIRECRAWL_API_KEY")
    if not firecrawl_api_key:
        pytest.skip("需要设置 FCAM_E2E_FIRECRAWL_API_KEY 才能执行真实上游调用")

    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-upstream-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_id = int(r_client.json()["client"]["id"])
    client_token = r_client.json()["token"]

    r_key = http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": firecrawl_api_key,
            "client_id": client_id,
            "name": "e2e-upstream",
            "plan_type": "free",
            "daily_quota": 5,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201

    request_id = f"e2eV1Scrape_{uuid.uuid4().hex[:24]}"
    scrape_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()
    r_scrape = http.post(
        "/v1/scrape",
        headers={**_client_headers(client_token), "X-Request-Id": request_id},
        json={"url": scrape_url},
    )
    if r_scrape.status_code == 402:
        pytest.skip("上游 402 insufficient credits，跳过该用例")
    assert r_scrape.status_code == 200

    r_log = http.get("/admin/logs", headers=admin_headers, params={"request_id": request_id})
    assert r_log.status_code == 200
    items = r_log.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["endpoint"] == "scrape"
    assert item["status_code"] == 200
    assert item["level"] == "info"
    assert item.get("error_message") in {None, ""}
    assert item.get("error_details") in {None, ""}


@pytest.mark.upstream
def test_e2e_api_scrape_success_with_real_upstream(http: httpx.Client, e2e_server: _E2EServer):
    if not _env_flag("FCAM_E2E_ALLOW_UPSTREAM"):
        pytest.skip("需要设置 FCAM_E2E_ALLOW_UPSTREAM=1 才会执行真实上游调用")

    firecrawl_api_key = os.environ.get("FCAM_E2E_FIRECRAWL_API_KEY")
    if not firecrawl_api_key:
        pytest.skip("需要设置 FCAM_E2E_FIRECRAWL_API_KEY 才能执行真实上游调用")

    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-api-upstream-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_id = int(r_client.json()["client"]["id"])
    client_token = r_client.json()["token"]

    r_key = http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": firecrawl_api_key,
            "client_id": client_id,
            "name": "e2e-api-upstream",
            "plan_type": "free",
            "daily_quota": 5,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201

    request_id = f"e2eApiScrape_{uuid.uuid4().hex[:24]}"
    scrape_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()
    r_scrape = http.post(
        "/api/scrape",
        headers={**_client_headers(client_token), "X-Request-Id": request_id},
        json={"url": scrape_url},
    )
    if r_scrape.status_code == 429:
        pytest.skip("上游 429 rate limited，跳过该用例")
    if r_scrape.status_code == 402:
        pytest.skip("上游 402 insufficient credits，跳过该用例")
    assert r_scrape.status_code == 200

    body = r_scrape.json()
    assert isinstance(body, dict)
    assert body

    r_log = http.get("/admin/logs", headers=admin_headers, params={"request_id": request_id})
    assert r_log.status_code == 200
    items = r_log.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["endpoint"] == "scrape"
    assert item["status_code"] == 200
    assert item["level"] == "info"
    assert item.get("error_message") in {None, ""}
    assert item.get("error_details") in {None, ""}


@pytest.mark.upstream
def test_e2e_admin_key_test_success_with_real_upstream(http: httpx.Client, e2e_server: _E2EServer):
    if not _env_flag("FCAM_E2E_ALLOW_UPSTREAM"):
        pytest.skip("需要设置 FCAM_E2E_ALLOW_UPSTREAM=1 才会执行真实上游调用")

    firecrawl_api_key = os.environ.get("FCAM_E2E_FIRECRAWL_API_KEY")
    if not firecrawl_api_key:
        pytest.skip("需要设置 FCAM_E2E_FIRECRAWL_API_KEY 才能执行真实上游调用")

    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-keytest-upstream-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_id = int(r_client.json()["client"]["id"])

    r_key = http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": firecrawl_api_key,
            "client_id": client_id,
            "name": "e2e-keytest-upstream",
            "plan_type": "free",
            "daily_quota": 5,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201
    key_id = int(r_key.json()["id"])

    test_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()
    request_id = f"e2eKeyTest_{uuid.uuid4().hex[:24]}"
    r_test = http.post(
        f"/admin/keys/{key_id}/test",
        headers={**admin_headers, "X-Request-Id": request_id},
        json={"mode": "scrape", "test_url": test_url},
    )
    assert r_test.status_code == 200
    payload = r_test.json()

    if payload.get("upstream_status_code") == 429:
        pytest.skip("上游 429 rate limited，跳过该用例")
    if payload.get("upstream_status_code") == 402:
        pytest.skip("上游 402 insufficient credits，跳过该用例")
    assert payload.get("upstream_status_code") == 200
    assert payload.get("ok") is True


@pytest.mark.upstream
def test_e2e_admin_keys_batch_test_with_real_upstream(http: httpx.Client, e2e_server: _E2EServer):
    if not _env_flag("FCAM_E2E_ALLOW_UPSTREAM"):
        pytest.skip("需要设置 FCAM_E2E_ALLOW_UPSTREAM=1 才会执行真实上游调用")

    firecrawl_api_key = os.environ.get("FCAM_E2E_FIRECRAWL_API_KEY")
    if not firecrawl_api_key:
        pytest.skip("需要设置 FCAM_E2E_FIRECRAWL_API_KEY 才能执行真实上游调用")

    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"e2e-batchtest-upstream-{uuid.uuid4().hex[:10]}"
    r_client = http.post(
        "/admin/clients",
        headers=admin_headers,
        json={
            "name": client_name,
            "daily_quota": 1000,
            "rate_limit_per_min": 60,
            "max_concurrent": 10,
            "is_active": True,
        },
    )
    assert r_client.status_code == 201
    client_id = int(r_client.json()["client"]["id"])

    r_key = http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": firecrawl_api_key,
            "client_id": client_id,
            "name": "e2e-batchtest-upstream",
            "plan_type": "free",
            "daily_quota": 5,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201
    key_id = int(r_key.json()["id"])

    test_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()
    request_id = f"e2eBatchTest_{uuid.uuid4().hex[:24]}"
    r_batch = http.post(
        "/admin/keys/batch",
        headers={**admin_headers, "X-Request-Id": request_id},
        json={
            "ids": [key_id],
            "patch": {"daily_quota": 10},
            "reset_cooldown": True,
            "soft_delete": False,
            "test": {"mode": "scrape", "test_url": test_url},
        },
    )
    assert r_batch.status_code == 200
    body = r_batch.json()
    assert body["requested"] == 1
    assert body["succeeded"] == 1
    assert body["failed"] == 0

    result = body["results"][0]
    assert result["id"] == key_id
    assert result["ok"] is True
    assert result["key"]["daily_quota"] == 10
    if result.get("test", {}).get("upstream_status_code") == 429:
        pytest.skip("上游 429 rate limited，跳过该用例")
    if result.get("test", {}).get("upstream_status_code") == 402:
        pytest.skip("上游 402 insufficient credits，跳过该用例")
    assert result["test"]["ok"] is True
