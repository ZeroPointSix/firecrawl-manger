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

pytestmark = [pytest.mark.e2e, pytest.mark.upstream]


def _skip_unless_real_upstream_enabled() -> str:
    if not _env_flag("FCAM_E2E_ALLOW_UPSTREAM"):
        pytest.skip("需要设置 FCAM_E2E_ALLOW_UPSTREAM=1 才会执行真实上游调用")

    firecrawl_api_key = (os.environ.get("FCAM_E2E_FIRECRAWL_API_KEY") or "").strip()
    if not firecrawl_api_key:
        pytest.skip("需要设置 FCAM_E2E_FIRECRAWL_API_KEY 才能执行真实上游调用")
    return firecrawl_api_key


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
        },
    )

    if not _env_flag("FCAM_E2E"):
        pytest.skip("需要设置环境变量 FCAM_E2E=1 才会运行真实 API 的 E2E 测试")

    tmp_dir = tmp_path_factory.mktemp("fcam-fc-e2e")
    db_path = tmp_dir / "fc_e2e.db"
    log_path = tmp_dir / "uvicorn.log"

    admin_token = os.environ.get("FCAM_E2E_ADMIN_TOKEN") or "e2e_admin"
    master_key = os.environ.get("FCAM_E2E_MASTER_KEY") or "e2e_master_key_32_bytes_minimum____"

    env = os.environ.copy()
    allow_upstream = _env_flag("FCAM_E2E_ALLOW_UPSTREAM")
    firecrawl_base_url = os.environ.get("FCAM_E2E_FIRECRAWL_BASE_URL") or "https://api.firecrawl.dev"

    env.update(
        {
            "FCAM_DATABASE__PATH": db_path.as_posix(),
            "FCAM_ADMIN_TOKEN": admin_token,
            "FCAM_MASTER_KEY": master_key,
            "FCAM_SERVER__ENABLE_DOCS": "false",
            "FCAM_OBSERVABILITY__METRICS_ENABLED": "false",
            "FCAM_LOGGING__FORMAT": "plain",
            "FCAM_FIRECRAWL__BASE_URL": firecrawl_base_url if allow_upstream else "http://127.0.0.1:9",
            "FCAM_FIRECRAWL__TIMEOUT": "25" if allow_upstream else "1",
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
            raise RuntimeError(f"FCAM FC E2E server 未就绪: {last_err}\n{server_log}") from last_err

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


def _client_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def fc_http(e2e_server: _E2EServer):
    with httpx.Client(base_url=e2e_server.base_url, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def fc_ctx(fc_http: httpx.Client, e2e_server):
    firecrawl_api_key = _skip_unless_real_upstream_enabled()
    admin_headers = _admin_headers(e2e_server.admin_token)

    client_name = f"fc-sdk-{uuid.uuid4().hex[:10]}"
    r_client = fc_http.post(
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

    r_key = fc_http.post(
        "/admin/keys",
        headers=admin_headers,
        json={
            "api_key": firecrawl_api_key,
            "client_id": client_id,
            "name": "fc-sdk",
            "plan_type": "free",
            "daily_quota": 50,
            "max_concurrent": 2,
            "rate_limit_per_min": 10,
            "is_active": True,
        },
    )
    assert r_key.status_code == 201
    key_id = int(r_key.json()["id"])

    # Preflight: validate this upstream key once to avoid cascading failures (401/403/402/429).
    test_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()
    r_test = fc_http.post(
        f"/admin/keys/{key_id}/test",
        headers=admin_headers,
        json={"mode": "scrape", "test_url": test_url},
    )
    assert r_test.status_code == 200
    payload = r_test.json()
    upstream_status = payload.get("upstream_status_code")
    if upstream_status in {401, 403}:
        pytest.skip(
            "上游 key 无效（401/403）。注意 FCAM_E2E_FIRECRAWL_API_KEY 必须是 Firecrawl 官方 key（通常以 fc- 开头），"
            f"不是 FCAM client token。payload={payload}"
        )
    if upstream_status == 402:
        pytest.skip(f"上游 402 insufficient credits，跳过该用例。payload={payload}")
    if upstream_status == 429:
        pytest.skip(f"上游 429 rate limited，跳过该用例。payload={payload}")
    if payload.get("ok") is not True:
        pytest.skip(f"上游 key test 失败，跳过该用例。payload={payload}")

    target_url = (os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com").strip()

    return {
        "admin_headers": admin_headers,
        "client_headers": _client_headers(client_token),
        "client_id": client_id,
        "key_id": key_id,
        "target_url": target_url,
    }


def _maybe_skip_quota_or_rate_limit(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        pytest.skip("上游 429 rate limited，跳过该用例")
    if resp.status_code == 402:
        pytest.skip("上游 402 insufficient credits，跳过该用例")


def _assert_not_truncated(resp: httpx.Response) -> None:
    cl = resp.headers.get("content-length")
    if cl is None:
        return
    try:
        expected = int(cl)
    except ValueError:
        return
    assert expected == len(resp.content)


def test_FC_v2_scrape_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2Scrape_{uuid.uuid4().hex[:24]}"
    r = fc_http.post(
        "/v2/scrape",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={"url": fc_ctx["target_url"]},
    )
    _maybe_skip_quota_or_rate_limit(r)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == request_id
    _assert_not_truncated(r)

    body = r.json()
    assert isinstance(body, dict)
    assert body.get("success") is True
    assert "data" in body


def test_FC_v2_map_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2Map_{uuid.uuid4().hex[:24]}"
    r = fc_http.post(
        "/v2/map",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={"url": fc_ctx["target_url"]},
    )
    _maybe_skip_quota_or_rate_limit(r)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == request_id
    _assert_not_truncated(r)

    body = r.json()
    assert isinstance(body, dict)
    assert body.get("success") is True


def test_FC_v2_search_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2Search_{uuid.uuid4().hex[:23]}"
    r = fc_http.post(
        "/v2/search",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={"query": "firecrawl"},
    )
    _maybe_skip_quota_or_rate_limit(r)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == request_id
    _assert_not_truncated(r)

    body = r.json()
    assert isinstance(body, dict)
    assert body.get("success") is True


def test_FC_v2_extract_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2Extract_{uuid.uuid4().hex[:24]}"
    r = fc_http.post(
        "/v2/extract",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={
            "urls": [fc_ctx["target_url"]],
            "prompt": "Extract the page title",
        },
    )
    _maybe_skip_quota_or_rate_limit(r)
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == request_id
    _assert_not_truncated(r)

    body = r.json()
    assert isinstance(body, dict)
    assert body.get("success") is True


def test_FC_v2_crawl_start_and_status_alias_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2CrawlStart_{uuid.uuid4().hex[:20]}"
    r_start = fc_http.post(
        "/v2/crawl/start",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={"url": fc_ctx["target_url"], "limit": 1},
    )
    _maybe_skip_quota_or_rate_limit(r_start)
    assert r_start.status_code == 200
    assert r_start.headers.get("x-request-id") == request_id
    _assert_not_truncated(r_start)

    start_body = r_start.json()
    assert isinstance(start_body, dict)
    crawl_id = start_body.get("id")
    assert isinstance(crawl_id, str) and crawl_id.strip()

    request_id2 = f"FCv2CrawlStatus_{uuid.uuid4().hex[:19]}"
    r_status = fc_http.get(
        f"/v2/crawl/status/{crawl_id}",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id2},  # type: ignore[arg-type]
    )
    _maybe_skip_quota_or_rate_limit(r_status)
    assert r_status.status_code == 200
    assert r_status.headers.get("x-request-id") == request_id2
    _assert_not_truncated(r_status)

    status_body = r_status.json()
    assert isinstance(status_body, dict)
    assert status_body.get("id") in {crawl_id, None}


def test_FC_v2_batch_scrape_start_and_status_alias_sdk_compat(fc_http: httpx.Client, fc_ctx: dict[str, object]):
    request_id = f"FCv2BatchStart_{uuid.uuid4().hex[:20]}"
    r_start = fc_http.post(
        "/v2/batch/scrape/start",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id},  # type: ignore[arg-type]
        json={"urls": [fc_ctx["target_url"]]},
    )
    _maybe_skip_quota_or_rate_limit(r_start)
    assert r_start.status_code == 200
    assert r_start.headers.get("x-request-id") == request_id
    _assert_not_truncated(r_start)

    start_body = r_start.json()
    assert isinstance(start_body, dict)
    job_id = start_body.get("id")
    assert isinstance(job_id, str) and job_id.strip()

    request_id2 = f"FCv2BatchStatus_{uuid.uuid4().hex[:20]}"
    r_status = fc_http.get(
        f"/v2/batch/scrape/status/{job_id}",
        headers={**fc_ctx["client_headers"], "X-Request-Id": request_id2},  # type: ignore[arg-type]
    )
    _maybe_skip_quota_or_rate_limit(r_status)
    assert r_status.status_code == 200
    assert r_status.headers.get("x-request-id") == request_id2
    _assert_not_truncated(r_status)

    status_body = r_status.json()
    assert isinstance(status_body, dict)
