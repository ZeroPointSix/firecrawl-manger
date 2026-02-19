from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import enforce_client_governance, get_db, require_client
from app.core.forwarder import Forwarder
from app.core.idempotency import complete as idempotency_complete
from app.core.idempotency import start_or_replay as idempotency_start_or_replay
from app.db.models import Client

router = APIRouter(prefix="/v2", tags=["firecrawl-compat-v2"])


def _forwarder(request: Request) -> Forwarder:
    return request.app.state.forwarder


def _with_query(request: Request, path: str) -> str:
    query = request.url.query
    return f"{path}?{query}" if query else path


def _path_and_query(request: Request) -> str:
    return _with_query(request, request.url.path)


def _forward_with_fallback(
    request: Request,
    *,
    db: Session,
    client: Client,
    method: str,
    primary_path: str,
    fallback_path: str | None,
    payload: Any | None,
) -> Any:
    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method=method,
        upstream_path=primary_path,
        json_body=payload,
        inbound_headers=dict(request.headers),
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
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=response)
    return response


@router.get("/crawl/{job_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl_status"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}"),
        fallback_path=_with_query(request, f"/v2/crawl/status/{job_id}"),
        payload=None,
    )


@router.get("/crawl/status/{job_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_status_alias(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl_status"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/crawl/{job_id}"),
        fallback_path=_with_query(request, f"/v2/crawl/status/{job_id}"),
        payload=None,
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
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/batch/scrape"),
        fallback_path=_with_query(request, "/v2/batch/scrape/start"),
        payload=payload,
    )


@router.post("/batch/scrape/start", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_start(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/batch/scrape"),
        fallback_path=_with_query(request, "/v2/batch/scrape/start"),
        payload=payload,
    )


@router.get("/batch/scrape/{job_id}", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}"),
        fallback_path=_with_query(request, f"/v2/batch/scrape/status/{job_id}"),
        payload=None,
    )


@router.get("/batch/scrape/status/{job_id}", dependencies=[Depends(enforce_client_governance)])
def batch_scrape_status_alias(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "batch"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, f"/v2/batch/scrape/{job_id}"),
        fallback_path=_with_query(request, f"/v2/batch/scrape/status/{job_id}"),
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

