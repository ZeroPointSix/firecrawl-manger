from __future__ import annotations

from fastapi import APIRouter, Request

from app.db.session import check_db_ready
from app.errors import FcamError

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, bool]:
    issues: list[str] = []

    config = request.app.state.config
    secrets = request.app.state.secrets
    if config.server.enable_control_plane and not secrets.admin_token:
        issues.append("Admin token not configured")
    if (config.server.enable_data_plane or config.server.enable_control_plane) and not secrets.master_key:
        issues.append("Master key not configured")

    ok, message = check_db_ready(request.app.state.db_engine)
    if not ok:
        issues.append(message)

    if config.state.mode == "redis":
        redis_client = getattr(request.app.state, "redis", None)
        try:
            if redis_client is None:
                issues.append("Redis not configured")
            else:
                redis_client.ping()
        except Exception:
            issues.append("Redis unavailable")

    if issues:
        raise FcamError(
            status_code=503,
            code="NOT_READY",
            message=issues[0],
            details={"issues": issues},
        )

    return {"ok": True}
