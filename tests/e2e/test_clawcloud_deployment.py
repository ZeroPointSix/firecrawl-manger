"""
ClawCloud 远程部署 E2E 测试

使用环境变量配置远程部署的测试，不启动本地服务器。

环境变量：
- FCAM_CLAWCLOUD_BASE_URL: ClawCloud 部署的基础 URL
- FCAM_CLAWCLOUD_CLIENT_KEY: 已存在的 Client Key
- FCAM_CLAWCLOUD_ADMIN_TOKEN: Admin Token（可选，用于测试控制面）
- FCAM_E2E_SCRAPE_URL: 用于测试的抓取 URL（可选，默认 https://example.com）
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_E2E_ENV_FILE = _REPO_ROOT / ".env.e2e"

pytestmark = pytest.mark.e2e


def _load_env_file(path: Path, *, allowed_keys: set[str]) -> None:
    """从 .env.e2e 文件加载环境变量"""
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
    """检查环境变量是否为真值"""
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class _ClawCloudConfig:
    """ClawCloud 部署配置"""

    base_url: str
    client_key: str
    admin_token: str | None
    scrape_url: str


@pytest.fixture(scope="session")
def clawcloud_config() -> _ClawCloudConfig:
    """加载 ClawCloud 部署配置"""
    _load_env_file(
        _E2E_ENV_FILE,
        allowed_keys={
            "FCAM_CLAWCLOUD_BASE_URL",
            "FCAM_CLAWCLOUD_CLIENT_KEY",
            "FCAM_CLAWCLOUD_ADMIN_TOKEN",
            "FCAM_E2E_SCRAPE_URL",
        },
    )

    base_url = os.environ.get("FCAM_CLAWCLOUD_BASE_URL")
    if not base_url:
        pytest.skip("需要设置环境变量 FCAM_CLAWCLOUD_BASE_URL 才会运行 ClawCloud 部署测试")

    client_key = os.environ.get("FCAM_CLAWCLOUD_CLIENT_KEY")
    if not client_key:
        pytest.skip("需要设置环境变量 FCAM_CLAWCLOUD_CLIENT_KEY 才会运行 ClawCloud 部署测试")

    admin_token = os.environ.get("FCAM_CLAWCLOUD_ADMIN_TOKEN")
    scrape_url = os.environ.get("FCAM_E2E_SCRAPE_URL") or "https://example.com"

    return _ClawCloudConfig(
        base_url=base_url.rstrip("/"),
        client_key=client_key,
        admin_token=admin_token,
        scrape_url=scrape_url,
    )


@pytest.fixture()
def http(clawcloud_config: _ClawCloudConfig):
    """创建 HTTP 客户端"""
    with httpx.Client(base_url=clawcloud_config.base_url, timeout=30.0) as client:
        yield client


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_clawcloud_healthz(http: httpx.Client):
    """测试健康检查端点"""
    r = http.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True


def test_clawcloud_readyz(http: httpx.Client):
    """测试就绪检查端点"""
    r = http.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True


def test_clawcloud_client_auth_invalid(http: httpx.Client):
    """测试无效的 Client Key 应该被拒绝"""
    request_id = f"clawcloud_invalid_{uuid.uuid4().hex[:16]}"
    r = http.post(
        "/api/scrape",
        headers={**_client_headers("invalid_token"), "X-Request-Id": request_id},
        json={"url": "https://example.com"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body.get("error", {}).get("code") == "CLIENT_UNAUTHORIZED"


def test_clawcloud_api_scrape_with_valid_client_key(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试使用有效 Client Key 访问 /api/scrape"""
    request_id = f"clawcloud_scrape_{uuid.uuid4().hex[:16]}"
    r = http.post(
        "/api/scrape",
        headers={**_client_headers(clawcloud_config.client_key), "X-Request-Id": request_id},
        json={"url": clawcloud_config.scrape_url},
    )

    # 应该不是 401（鉴权失败）
    assert r.status_code != 401, f"Client Key 鉴权失败: {r.status_code} {r.text}"

    if r.status_code == 503:
        # 如果返回 503，说明鉴权成功但没有配置上游 Key
        body = r.json()
        assert body.get("error", {}).get("code") == "NO_KEY_CONFIGURED"
    elif r.status_code == 429:
        # 可能触发限流
        pytest.skip("触发限流，跳过该用例")
    elif r.status_code == 402:
        # 上游配额不足
        pytest.skip("上游配额不足，跳过该用例")
    elif r.status_code == 200:
        # 请求成功
        body = r.json()
        assert isinstance(body, dict)
        assert body  # 应该有返回内容
    else:
        pytest.fail(f"意外的状态码: {r.status_code} {r.text}")


def test_clawcloud_v1_scrape_with_valid_client_key(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试 Firecrawl 兼容层 /v1/scrape"""
    request_id = f"clawcloud_v1_{uuid.uuid4().hex[:16]}"
    r = http.post(
        "/v1/scrape",
        headers={**_client_headers(clawcloud_config.client_key), "X-Request-Id": request_id},
        json={"url": clawcloud_config.scrape_url},
    )

    # 应该不是 401（鉴权失败）
    assert r.status_code != 401, f"V1 兼容层鉴权失败: {r.status_code} {r.text}"

    if r.status_code == 503:
        body = r.json()
        assert body.get("error", {}).get("code") == "NO_KEY_CONFIGURED"
    elif r.status_code == 429:
        pytest.skip("触发限流，跳过该用例")
    elif r.status_code == 402:
        pytest.skip("上游配额不足，跳过该用例")
    elif r.status_code == 200:
        body = r.json()
        assert isinstance(body, dict)
        # Firecrawl 兼容层应该返回 success 字段
        assert "success" in body
    else:
        pytest.fail(f"意外的状态码: {r.status_code} {r.text}")


def test_clawcloud_v1_scrape_without_auth(http: httpx.Client):
    """测试 /v1/scrape 未鉴权应该被拒绝"""
    request_id = f"clawcloud_v1_noauth_{uuid.uuid4().hex[:16]}"
    r = http.post(
        "/v1/scrape",
        headers={"X-Request-Id": request_id},
        json={"url": "https://example.com"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body.get("error", {}).get("code") == "CLIENT_UNAUTHORIZED"


def test_clawcloud_response_headers(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试响应头是否正确"""
    request_id = f"clawcloud_headers_{uuid.uuid4().hex[:16]}"
    r = http.post(
        "/api/scrape",
        headers={**_client_headers(clawcloud_config.client_key), "X-Request-Id": request_id},
        json={"url": clawcloud_config.scrape_url},
    )

    # 检查 Request-Id 响应头
    assert "x-request-id" in r.headers or "X-Request-Id" in r.headers


@pytest.mark.admin
def test_clawcloud_admin_logs_query(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试控制面日志查询（需要 Admin Token）"""
    if not clawcloud_config.admin_token:
        pytest.skip("需要设置 FCAM_CLAWCLOUD_ADMIN_TOKEN 才能测试控制面")

    # 先发起一个请求，生成日志
    request_id = f"clawcloud_log_{uuid.uuid4().hex[:16]}"
    http.post(
        "/api/scrape",
        headers={**_client_headers(clawcloud_config.client_key), "X-Request-Id": request_id},
        json={"url": clawcloud_config.scrape_url},
    )

    # 查询日志
    r_log = http.get(
        "/admin/logs",
        headers=_admin_headers(clawcloud_config.admin_token),
        params={"request_id": request_id},
    )
    assert r_log.status_code == 200
    body = r_log.json()
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.admin
def test_clawcloud_admin_clients_list(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试控制面 Client 列表查询（需要 Admin Token）"""
    if not clawcloud_config.admin_token:
        pytest.skip("需要设置 FCAM_CLAWCLOUD_ADMIN_TOKEN 才能测试控制面")

    r = http.get("/admin/clients", headers=_admin_headers(clawcloud_config.admin_token))
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.admin
def test_clawcloud_admin_keys_list(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试控制面 Key 列表查询（需要 Admin Token）"""
    if not clawcloud_config.admin_token:
        pytest.skip("需要设置 FCAM_CLAWCLOUD_ADMIN_TOKEN 才能测试控制面")

    r = http.get("/admin/keys", headers=_admin_headers(clawcloud_config.admin_token))
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.admin
def test_clawcloud_admin_stats(http: httpx.Client, clawcloud_config: _ClawCloudConfig):
    """测试控制面统计查询（需要 Admin Token）"""
    if not clawcloud_config.admin_token:
        pytest.skip("需要设置 FCAM_CLAWCLOUD_ADMIN_TOKEN 才能测试控制面")

    r = http.get("/admin/stats", headers=_admin_headers(clawcloud_config.admin_token))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


@pytest.mark.admin
def test_clawcloud_admin_unauthorized(http: httpx.Client):
    """测试控制面未授权访问应该被拒绝"""
    r = http.get("/admin/clients")
    assert r.status_code == 401

    r2 = http.get("/admin/clients", headers=_admin_headers("invalid_admin_token"))
    assert r2.status_code == 401
