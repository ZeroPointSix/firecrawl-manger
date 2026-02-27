from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


def _as_posix_path(p: object) -> str:
    try:
        return Path(str(p)).as_posix()
    except Exception:
        return str(p).replace("\\", "/")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = _as_posix_path(getattr(item, "fspath", ""))
        # `regression` 的目标是“快且稳定”（见 pyproject.toml markers）。
        # 因此默认将所有 unit 纳入 regression；integration 仅将显式标记为 smoke 的用例纳入。
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.regression)
            continue

        if "/tests/integration/" in path and item.get_closest_marker("smoke") is not None:
            item.add_marker(pytest.mark.regression)


def pytest_configure(config: pytest.Config) -> None:
    """
    Windows 下 `--basetemp .pytest_tmp` 可能在 session 启动时清理旧目录时触发 WinError 32（SQLite 文件被占用）
    或触发系统临时目录权限问题；这里将其改写为每次运行一个唯一子目录，避免清理冲突导致的用例抖动。
    """

    basetemp = getattr(config.option, "basetemp", None)
    if not basetemp:
        return

    repo_root = Path(__file__).resolve().parents[1]
    default_basetemp = (repo_root / ".pytest_tmp").resolve()
    try:
        current_basetemp = Path(str(basetemp)).resolve()
    except Exception:
        return

    if current_basetemp != default_basetemp:
        return

    run_id = f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    run_dir = default_basetemp / run_id
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(run_dir)
