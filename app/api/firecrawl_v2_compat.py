from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import enforce_client_governance, get_db, require_client
from app.core.forwarder import Forwarder
from app.core.idempotency import complete as idempotency_complete
from app.core.idempotency import start_or_replay as idempotency_start_or_replay
from app.core.resource_binding import bind_resource, lookup_bound_key_id
from app.db.models import Client

router = APIRouter(prefix="/v2", tags=["firecrawl-compat-v2"])

logger = logging.getLogger(__name__)


def _forwarder(request: Request) -> Forwarder:
    return request.app.state.forwarder


def _with_query(request: Request, path: str) -> str:
    query = request.url.query
    return f"{path}?{query}" if query else path


def _path_and_query(request: Request) -> str:
    return _with_query(request, request.url.path)


def _extract_id_from_response(response: Any) -> str | None:
    status_code = getattr(response, "status_code", None)
    if status_code is None or int(status_code) < 200 or int(status_code) >= 300:
        return None

    headers = getattr(response, "headers", None) or {}
    content_type = str(headers.get("content-type") or "")
    if "application/json" not in content_type.lower():
        return None

    body: bytes = getattr(response, "body", b"") or b""
    if not body:
        return None

    try:
        data = json.loads(body)
    except Exception:
        return None

    if isinstance(data, dict):
        resource_id = data.get("id")
        if isinstance(resource_id, str) and resource_id.strip():
            return resource_id.strip()
    return None


def _maybe_bind_created_resource(
    request: Request,
    *,
    db: Session,
    client: Client,
    resource_type: str,
    response: Any,
) -> None:
    resource_id = _extract_id_from_response(response)
    api_key_id = getattr(request.state, "api_key_id", None)
    if resource_id is None or api_key_id is None:
        return
    try:
        bind_resource(
            db,
            client_id=client.id,
            api_key_id=int(api_key_id),
            resource_type=resource_type,
            resource_id=resource_id,
        )
    except Exception:
        logger.exception(
            "resource_binding.unhandled_error",
            extra={
                "fields": {
                    "client_id": client.id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "api_key_id": api_key_id,
                }
            },
        )


def _forward_with_fallback(
    request: Request,
    *,
    db: Session,
    client: Client,
    method: str,
    primary_path: str,
    fallback_path: str | None,
    payload: Any | None,
    pinned_api_key_id: int | None = None,
) -> Any:
    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method=method,
        upstream_path=primary_path,
        json_body=payload,
        inbound_headers=dict(request.headers),
        pinned_api_key_id=pinned_api_key_id,
    )
    if fallback_path is not None and result.response.status_code in {404, 405}:
        result = _forwarder(request).forward(
            db=db,
            request_id=request.state.request_id,
            client=client,
            method=method,
            upstream_path=fallback_path,
            json_body=payload,
            inbound_headers=dict(request.headers),
            pinned_api_key_id=pinned_api_key_id,
        )

    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    return result.response


@router.post("/crawl", dependencies=[Depends(enforce_client_governance)])
def crawl(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl"
    ctx, replay = idempotency_start_or_replay(
        db=db,
        config=request.app.state.config,
        client_id=client.id,
        idempotency_key=request.headers.get("x-idempotency-key"),
        endpoint="crawl",
        method="POST",
        payload=payload,
    )
    if replay is not None:
        request.state.retry_count = 0
        return replay

    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/crawl"),
        fallback_path=_with_query(request, "/v2/crawl/start"),
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="crawl",
        response=response,
    )
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=response)
    return response


@router.post("/crawl/start", dependencies=[Depends(enforce_client_governance)])
def crawl_start(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl"
    ctx, replay = idempotency_start_or_replay(
        db=db,
        config=request.app.state.config,
        client_id=client.id,
        idempotency_key=request.headers.get("x-idempotency-key"),
        endpoint="crawl",
        method="POST",
        payload=payload,
    )
    if replay is not None:
        request.state.retry_count = 0
        return replay

    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/crawl"),
        fallback_path=_with_query(request, "/v2/crawl/start"),
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="crawl",
        response=response,
    )
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=response)
    return response


# ========== P2 辅助功能端点 ==========
# 注意：这些静态路径必须定义在 `/crawl/{job_id}` 之前，否则会被 path 参数路由吞掉。


@router.get("/crawl/active", dependencies=[Depends(enforce_client_governance)])
def crawl_active(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get all active crawls for the authenticated team."""
    request.state.endpoint = "crawl_active"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/crawl/active"),
        fallback_path=None,
        payload=None,
    )


@router.post("/crawl/params-preview", dependencies=[Depends(enforce_client_governance)])
def crawl_params_preview(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Preview crawl parameters generated from natural language prompt."""
    request.state.endpoint = "crawl_params_preview"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/crawl/params-preview"),
        fallback_path=None,
        payload=payload,
    )


@router.get("/crawl/{job_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl_status"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="crawl",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}"),
        fallback_path=_with_query(request, f"/v2/crawl/status/{job_id}"),
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.get("/crawl/status/{job_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_status_alias(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl_status"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="crawl",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}"),
        fallback_path=_with_query(request, f"/v2/crawl/status/{job_id}"),
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.post("/agent", dependencies=[Depends(enforce_client_governance)])
def agent(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "agent"
    ctx, replay = idempotency_start_or_replay(
        db=db,
        config=request.app.state.config,
        client_id=client.id,
        idempotency_key=request.headers.get("x-idempotency-key"),
        endpoint="agent",
        method="POST",
        payload=payload,
    )
    if replay is not None:
        request.state.retry_count = 0
        return replay

    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_path_and_query(request),
        fallback_path=None,
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="agent",
        response=response,
    )
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=response)
    return response


@router.post("/batch/scrape", dependencies=[Depends(enforce_client_governance)])
def batch_scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/batch/scrape"),
        fallback_path=_with_query(request, "/v2/batch/scrape/start"),
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="batch_scrape",
        response=response,
    )
    return response


@router.post("/batch/scrape/start", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_start(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/batch/scrape"),
        fallback_path=_with_query(request, "/v2/batch/scrape/start"),
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="batch_scrape",
        response=response,
    )
    return response


@router.get("/batch/scrape/{job_id}", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="batch_scrape",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}"),
        fallback_path=_with_query(request, f"/v2/batch/scrape/status/{job_id}"),
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.get("/batch/scrape/status/{job_id}", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_status_alias(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="batch_scrape",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}"),
        fallback_path=_with_query(request, f"/v2/batch/scrape/status/{job_id}"),
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.delete("/crawl/{job_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_delete(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="crawl",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="DELETE",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.get("/crawl/{job_id}/errors", dependencies=[Depends(enforce_client_governance)])
def crawl_errors(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl_status"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="crawl",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}/errors"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.get("/batch/scrape/{job_id}/errors", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_errors(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="batch_scrape",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}/errors"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.delete("/batch/scrape/{job_id}", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_delete(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="batch_scrape",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="DELETE",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.get("/agent/{job_id}", dependencies=[Depends(enforce_client_governance)])
def agent_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "agent"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="agent",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/agent/{job_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.delete("/agent/{job_id}", dependencies=[Depends(enforce_client_governance)])
def agent_delete(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "agent"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="agent",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="DELETE",
        primary_path=_with_query(request, f"/v2/agent/{job_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.post("/browser", dependencies=[Depends(enforce_client_governance)])
def browser_create(
    request: Request,
    payload: Any | None = Body(None),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "browser"
    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/browser"),
        fallback_path=None,
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="browser",
        response=response,
    )
    return response


@router.get("/browser", dependencies=[Depends(enforce_client_governance)])
def browser_list(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "browser"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/browser"),
        fallback_path=None,
        payload=None,
    )


@router.post("/browser/{session_id}/execute", dependencies=[Depends(enforce_client_governance)])
def browser_execute(
    request: Request,
    session_id: str,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "browser"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="browser",
        resource_id=session_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, f"/v2/browser/{session_id}/execute"),
        fallback_path=None,
        payload=payload,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.delete("/browser/{session_id}", dependencies=[Depends(enforce_client_governance)])
def browser_delete(
    request: Request,
    session_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "browser"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="browser",
        resource_id=session_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="DELETE",
        primary_path=_with_query(request, f"/v2/browser/{session_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


@router.post("/extract", dependencies=[Depends(enforce_client_governance)])
def extract(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "extract"
    response = _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/extract"),
        fallback_path=None,
        payload=payload,
    )
    _maybe_bind_created_resource(
        request,
        db=db,
        client=client,
        resource_type="extract",
        response=response,
    )
    return response


@router.get("/extract/{job_id}", dependencies=[Depends(enforce_client_governance)])
def extract_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "extract"
    pinned_api_key_id = lookup_bound_key_id(
        db,
        client_id=client.id,
        resource_type="extract",
        resource_id=job_id,
    )
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/extract/{job_id}"),
        fallback_path=None,
        payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )


# ========== P0 核心功能端点 ==========


@router.post("/scrape", dependencies=[Depends(enforce_client_governance)])
def scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Scrape a single URL and optionally extract information using an LLM."""
    request.state.endpoint = "scrape"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/scrape"),
        fallback_path=None,
        payload=payload,
    )


@router.post("/search", dependencies=[Depends(enforce_client_governance)])
def search(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Search and optionally scrape search results."""
    request.state.endpoint = "search"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/search"),
        fallback_path=None,
        payload=payload,
    )


@router.post("/map", dependencies=[Depends(enforce_client_governance)])
def map_urls(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Map multiple URLs based on options."""
    request.state.endpoint = "map"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/map"),
        fallback_path=None,
        payload=payload,
    )


# ========== P1 账户管理端点 ==========


@router.get("/team/credit-usage", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get remaining credits for the authenticated team.

    Returns:
        - remainingCredits: Number of credits remaining
        - planCredits: Number of credits in the plan
        - billingPeriodStart: Start date of billing period
        - billingPeriodEnd: End date of billing period
    """
    request.state.endpoint = "team_credit_usage"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/credit-usage"),
        fallback_path=None,
        payload=None,
    )


@router.get("/team/queue-status", dependencies=[Depends(enforce_client_governance)])
def team_queue_status(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get metrics about your team's scrape queue.

    Returns:
        - jobsInQueue: Total number of jobs in queue
        - activeJobsInQueue: Number of active jobs
        - waitingJobsInQueue: Number of waiting jobs
        - maxConcurrency: Maximum concurrent jobs based on plan
        - mostRecentSuccess: Timestamp of most recent successful job
    """
    request.state.endpoint = "team_queue_status"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/queue-status"),
        fallback_path=None,
        payload=None,
    )


@router.get("/team/credit-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get historical credit usage for the authenticated team."""
    request.state.endpoint = "team_credit_usage_historical"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/credit-usage/historical"),
        fallback_path=None,
        payload=None,
    )


@router.get("/team/token-usage", dependencies=[Depends(enforce_client_governance)])
def team_token_usage(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get remaining tokens for the authenticated team (Extract only).

    Note: This endpoint is specific to the Extract feature.
    """
    request.state.endpoint = "team_token_usage"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/token-usage"),
        fallback_path=None,
        payload=None,
    )


@router.get("/team/token-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_token_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get historical token usage for the authenticated team (Extract only)."""
    request.state.endpoint = "team_token_usage_historical"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/token-usage/historical"),
        fallback_path=None,
        payload=None,
    )


@router.get("/{path:path}", dependencies=[Depends(enforce_client_governance)])
def passthrough_get(
    request: Request,
    path: str,  # noqa: ARG001 - required by FastAPI path matching
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_path_and_query(request),
        fallback_path=None,
        payload=None,
    )


@router.post("/{path:path}", dependencies=[Depends(enforce_client_governance)])
def passthrough_post(
    request: Request,
    path: str,  # noqa: ARG001 - required by FastAPI path matching
    payload: Any | None = Body(None),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_path_and_query(request),
        fallback_path=None,
        payload=payload,
    )


@router.delete("/{path:path}", dependencies=[Depends(enforce_client_governance)])
def passthrough_delete(
    request: Request,
    path: str,  # noqa: ARG001 - required by FastAPI path matching
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="DELETE",
        primary_path=_path_and_query(request),
        fallback_path=None,
        payload=None,
    )
