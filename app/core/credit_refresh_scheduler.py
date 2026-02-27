from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI

from app.core.credit_refresh import credit_refresh_loop
from app.core.security import derive_master_key_bytes

logger = logging.getLogger(__name__)


async def start_credit_refresh_scheduler(app: FastAPI) -> None:
    config = getattr(app.state, "config", None)
    secrets = getattr(app.state, "secrets", None)
    if config is None or secrets is None:
        return

    if not getattr(config, "credit_monitoring", None) or not config.credit_monitoring.enabled:
        return

    if not secrets.master_key:
        logger.warning("credit.scheduler_skipped", extra={"fields": {"reason": "master_key_missing"}})
        return

    if getattr(app.state, "credit_refresh_task", None) is not None:
        return

    stop_event = asyncio.Event()
    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    db_factory = app.state.db_session_factory

    task = asyncio.create_task(
        credit_refresh_loop(
            db_factory=db_factory,
            master_key=master_key_bytes,
            config=config,
            stop_event=stop_event,
        )
    )
    app.state.credit_refresh_stop_event = stop_event
    app.state.credit_refresh_task = task
    logger.info("credit.scheduler_started")


async def stop_credit_refresh_scheduler(app: FastAPI) -> None:
    task: asyncio.Task[Any] | None = getattr(app.state, "credit_refresh_task", None)
    stop_event: asyncio.Event | None = getattr(app.state, "credit_refresh_stop_event", None)
    if task is None:
        return

    try:
        if stop_event is not None:
            stop_event.set()
        await asyncio.wait_for(task, timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass
    except Exception:
        task.cancel()
    finally:
        app.state.credit_refresh_task = None
        app.state.credit_refresh_stop_event = None
        logger.info("credit.scheduler_stopped")

