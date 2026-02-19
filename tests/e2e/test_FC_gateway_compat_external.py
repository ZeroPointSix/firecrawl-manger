from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.external]


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    if not v:
        return None
    s = v.strip()
    return s or None


def _required_env(name: str) -> str:
    v = _env(name)
    if not v:
        pytest.skip(f"需要设置环境变量 {name} 才会运行外部 FC 兼容性测试")
    return v


def _is_verbose() -> bool:
    v = (_env("FCAM_FC_VERBOSE") or "").lower()
    return v in {"1", "true", "yes", "y", "on"}


@pytest.fixture(scope="session")
def fc_target() -> dict[str, str]:
    base_url = _required_env("FCAM_FC_BASE_URL").rstrip("/")
    client_token = _required_env("FCAM_FC_CLIENT_TOKEN")
    admin_token = _env("FCAM_FC_ADMIN_TOKEN")  # optional
    return {"base_url": base_url, "client_token": client_token, "admin_token": admin_token or ""}


@pytest.fixture()
def http(fc_target: dict[str, str]):
    with httpx.Client(base_url=fc_target["base_url"], timeout=60.0) as client:
        yield client


def _client_headers(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_not_truncated(resp: httpx.Response) -> None:
    cl = resp.headers.get("content-length")
    if cl is None:
        return
    try:
        expected = int(cl)
    except ValueError:
        return
    assert expected == len(resp.content)


def _assert_request_id_echo(resp: httpx.Response, request_id: str) -> None:
    # Server sets X-Request-Id on all responses.
    assert resp.headers.get("x-request-id") == request_id


def _maybe_verify_admin_log_contains_upstream_error(
    http: httpx.Client,
    *,
    admin_token: str,
    request_id: str,
    resp: httpx.Response,
) -> None:
    if not admin_token:
        return

    if resp.status_code < 400:
        return

    r_log = http.get(
        "/admin/logs",
        headers=_admin_headers(admin_token),
        params={"limit": 1, "request_id": request_id},
    )
    assert r_log.status_code == 200
    items = r_log.json().get("items") or []
    assert items, "missing request log item"
    item = items[0]
    # Should have error details (either FCAM error or captured upstream error preview/json)
    assert item.get("error_message")
    assert item.get("error_details")

    if _is_verbose():
        print("admin.log.item=", item)


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v2/scrape", {"url": "https://example.com"}),
        ("/v2/map", {"url": "https://example.com"}),
        ("/v2/search", {"query": "firecrawl"}),
        ("/v2/extract", {"urls": ["https://example.com"], "prompt": "Extract the page title"}),
        # Alias paths that should be accepted and forwarded:
        ("/v2/crawl/start", {"url": "https://example.com", "limit": 1}),
        ("/v2/batch/scrape/start", {"urls": ["https://example.com"]}),
    ],
)
def test_FC_external_gateway_sdk_compat_smoke(
    http: httpx.Client,
    fc_target: dict[str, str],
    path: str,
    payload: dict,
) -> None:
    request_id = f"FC_EXT_{uuid.uuid4().hex[:24]}"
    r = http.post(
        path,
        headers=_client_headers(fc_target["client_token"], request_id),
        json=payload,
    )

    if _is_verbose():
        preview = r.text
        if len(preview) > 800:
            preview = preview[:800] + "...(truncated)"
        print(f"request_id={request_id} path={path} status={r.status_code}")
        print("response.headers=", dict(r.headers))
        print("response.preview=", preview)

    # Compatibility expectation:
    # - Must not be 404 from gateway routing/whitelist if path is supported.
    # - Must echo request id, and must not return a truncated body.
    assert r.status_code != 404
    _assert_request_id_echo(r, request_id)
    _assert_not_truncated(r)

    _maybe_verify_admin_log_contains_upstream_error(
        http,
        admin_token=fc_target["admin_token"],
        request_id=request_id,
        resp=r,
    )
