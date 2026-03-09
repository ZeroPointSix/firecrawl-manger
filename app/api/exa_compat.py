"""Exa native API compatibility layer.

Provides 4 P0 endpoints that proxy to the Exa API (https://api.exa.ai):
  POST /exa/search
  POST /exa/findSimilar
  POST /exa/contents
  POST /exa/answer

Each endpoint strips the /exa prefix and forwards the request to the configured
Exa upstream base URL using x-api-key authentication.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import enforce_client_governance, get_db, require_client
from app.core.forwarder import Forwarder
from app.db.models import Client

router = APIRouter(prefix="/exa", tags=["exa-compat"])

_PROVIDER = "exa"


def _forwarder(request: Request) -> Forwarder:
    return request.app.state.forwarder


def _upstream_path(request: Request) -> str:
    """Strip /exa prefix and keep query string: /exa/search?foo=bar -> /search?foo=bar"""
    path = request.url.path
    if path.startswith("/exa"):
        path = path[4:]  # strip "/exa"
    if not path.startswith("/"):
        path = "/" + path
    query = request.url.query
    return f"{path}?{query}" if query else path


@router.post("/search", dependencies=[Depends(enforce_client_governance)])
def exa_search(
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
        upstream_path=_upstream_path(request),
        json_body=payload,
        inbound_headers=dict(request.headers),
        provider=_PROVIDER,
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "exa_search"
    return result.response


@router.post("/findSimilar", dependencies=[Depends(enforce_client_governance)])
def exa_find_similar(
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
        upstream_path=_upstream_path(request),
        json_body=payload,
        inbound_headers=dict(request.headers),
        provider=_PROVIDER,
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "exa_findSimilar"
    return result.response


@router.post("/contents", dependencies=[Depends(enforce_client_governance)])
def exa_contents(
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
        upstream_path=_upstream_path(request),
        json_body=payload,
        inbound_headers=dict(request.headers),
        provider=_PROVIDER,
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "exa_contents"
    return result.response


@router.post("/answer", dependencies=[Depends(enforce_client_governance)])
def exa_answer(
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
        upstream_path=_upstream_path(request),
        json_body=payload,
        inbound_headers=dict(request.headers),
        provider=_PROVIDER,
    )
    request.state.api_key_id = result.api_key_id
    request.state.retry_count = result.retry_count
    request.state.endpoint = "exa_answer"
    return result.response
