from __future__ import annotations

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
        if "/tests/unit/" in path or "/tests/integration/" in path:
            item.add_marker(pytest.mark.regression)

