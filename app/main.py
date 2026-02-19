from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.health import router as health_router
from app.api.control_plane import router as control_plane_router
from app.api.data_plane import router as data_plane_router
from app.api.firecrawl_compat import router as firecrawl_compat_router
from app.api.firecrawl_v2_compat import router as firecrawl_v2_compat_router
from app.config import AppConfig, Secrets, load_config
from app.core.concurrency import ConcurrencyManager, RedisConcurrencyManager
from app.core.cooldown import NoopCooldownStore, RedisCooldownStore
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.rate_limit import RedisTokenBucketRateLimiter, TokenBucketRateLimiter
from app.db.session import create_engine_from_config, create_session_factory
from app.errors import register_exception_handlers
from app.middleware import FcamErrorMiddleware, RequestIdMiddleware, RequestLimitsMiddleware
from app.observability.logging import configure_logging
from app.observability.metrics import Metrics

logger = logging.getLogger(__name__)


def create_app(*, config: AppConfig | None = None, secrets: Secrets | None = None) -> FastAPI:
    if config is None or secrets is None:
        config, secrets = load_config()

    configure_logging(config.logging)

    app = FastAPI(
        docs_url="/docs" if config.server.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.server.enable_docs else None,
    )
    app.state.config = config
    app.state.secrets = secrets
    app.state.db_engine = create_engine_from_config(config)
    app.state.db_session_factory = create_session_factory(app.state.db_engine)

    lease_ttl_ms = int((max(config.firecrawl.timeout, 1) * (max(config.firecrawl.max_retries, 0) + 1) + 10) * 1000)

    if config.state.mode == "redis":
        import redis

        app.state.redis = redis.from_url(
            config.state.redis.url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        app.state.client_concurrency = RedisConcurrencyManager(
            client=app.state.redis,
            key_prefix=config.state.redis.key_prefix,
            scope="client",
            lease_ttl_ms=lease_ttl_ms,
        )
        app.state.client_rate_limiter = RedisTokenBucketRateLimiter(
            client=app.state.redis,
            key_prefix=config.state.redis.key_prefix,
            scope="client",
        )
        app.state.key_concurrency = RedisConcurrencyManager(
            client=app.state.redis,
            key_prefix=config.state.redis.key_prefix,
            scope="key",
            lease_ttl_ms=lease_ttl_ms,
        )
        app.state.key_rate_limiter = RedisTokenBucketRateLimiter(
            client=app.state.redis,
            key_prefix=config.state.redis.key_prefix,
            scope="key",
        )
        app.state.cooldown_store = RedisCooldownStore(
            client=app.state.redis,
            key_prefix=config.state.redis.key_prefix,
            scope="key",
        )
    else:
        app.state.redis = None
        app.state.client_concurrency = ConcurrencyManager()
        app.state.client_rate_limiter = TokenBucketRateLimiter()
        app.state.key_concurrency = ConcurrencyManager()
        app.state.key_rate_limiter = TokenBucketRateLimiter()
        app.state.cooldown_store = NoopCooldownStore()

    app.state.key_pool = KeyPool(cooldown_store=app.state.cooldown_store)

    if config.observability.metrics_enabled:
        app.state.metrics = Metrics()
        app.add_api_route(
            config.observability.metrics_path,
            app.state.metrics.render,
            methods=["GET"],
            include_in_schema=False,
        )
    else:
        app.state.metrics = None
    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=app.state.key_pool,
        key_concurrency=app.state.key_concurrency,
        key_rate_limiter=app.state.key_rate_limiter,
        metrics=app.state.metrics,
        cooldown_store=app.state.cooldown_store,
        transport=None,
    )

    app.add_middleware(
        RequestLimitsMiddleware,
        max_body_bytes=config.security.request_limits.max_body_bytes,
        allowed_api_paths=set(config.security.request_limits.allowed_paths),
    )
    app.add_middleware(FcamErrorMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router)
    if config.server.enable_data_plane:
        app.include_router(data_plane_router)
        app.include_router(firecrawl_compat_router)
        app.include_router(firecrawl_v2_compat_router)
    if config.server.enable_control_plane:
        app.include_router(control_plane_router)
        ui2_dir = Path(__file__).resolve().parent / "ui2"
        if ui2_dir.exists():
            app.mount("/ui2", StaticFiles(directory=str(ui2_dir), html=True), name="ui2")
        else:
            logger.warning("ui2.static_dir_missing", extra={"fields": {"path": str(ui2_dir)}})

            @app.get("/ui2/", include_in_schema=False)
            def ui2_placeholder() -> HTMLResponse:
                html = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="robots" content="noindex,nofollow" />
    <title>FCAM WebUI</title>
  </head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 24px;">
    <h1 style="margin: 0 0 8px 0;">FCAM WebUI（UI2）</h1>
    <p style="margin: 0 0 16px 0; color: #444;">
      UI2 静态文件尚未构建（目录不存在）。请在仓库根目录执行：
    </p>
    <pre style="background:#f6f8fa; padding:12px; border-radius:8px; overflow:auto;">cd webui
npm ci
npm run build</pre>
    <p style="margin: 16px 0 0 0; color: #444;">构建完成后刷新本页即可。</p>
    <div id="app" style="display:none"></div>
  </body>
</html>
"""
                return HTMLResponse(content=html, status_code=200)

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        def ui_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui2/", status_code=307)

    logger.info("app.started", extra={"fields": {"port": config.server.port}})
    return app


app = create_app()
