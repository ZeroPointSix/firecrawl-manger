from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import enforce_client_governance, get_db, require_client
from app.core.forwarder import Forwarder
from app.core.idempotency import complete as idempotency_complete
from app.core.idempotency import start_or_replay as idempotency_start_or_replay
from app.db.models import Client

router = APIRouter(prefix="/api", tags=["data-plane"])


def _forwarder(request: Request) -> Forwarder:
    return request.app.state.forwarder


@router.post("/scrape", dependencies=[Depends(enforce_client_governance)])
def scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method="POST",
        upstream_path="/scrape",
        json_body=payload,
        inbound_headers=dict(request.headers),
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "scrape"
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

    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method="POST",
        upstream_path="/crawl",
        json_body=payload,
        inbound_headers=dict(request.headers),
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=result.response)
    return result.response


@router.get("/crawl/{crawl_id}", dependencies=[Depends(enforce_client_governance)])
def crawl_status(
    request: Request,
    crawl_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method="GET",
        upstream_path=f"/crawl/{crawl_id}",
        json_body=None,
        inbound_headers=dict(request.headers),
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "crawl_status"
    return result.response


@router.post("/search", dependencies=[Depends(enforce_client_governance)])
def search(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method="POST",
        upstream_path="/search",
        json_body=payload,
        inbound_headers=dict(request.headers),
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "search"
    return result.response


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

    result = _forwarder(request).forward(
        db=db,
        request_id=request.state.request_id,
        client=client,
        method="POST",
        upstream_path="/agent",
        json_body=payload,
        inbound_headers=dict(request.headers),
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    idempotency_complete(db=db, config=request.app.state.config, ctx=ctx, response=result.response)
    return result.response
