from __future__ import annotations

from collections.abc import Iterable

from app.db.models import Client


def deduplicate_client_ids(client_ids: Iterable[int]) -> list[int]:
    """
    去重并保持输入顺序。

    说明：
    - 业务上 batch 操作更符合“按用户输入顺序”返回 failed_items；
    - `set()` 去重会打乱顺序并造成潜在的用例抖动。
    """

    return list(dict.fromkeys(client_ids))


def apply_batch_action_to_client(client: Client, *, action: str) -> None:
    """
    将 batch action 映射到 Client 状态变更。

    action 为字符串（enable/disable/delete），避免 core 依赖 API 层的 Enum。
    """

    match action:
        case "enable":
            client.is_active = True
            client.status = "active"
        case "disable":
            client.is_active = False
            client.status = "disabled"
        case "delete":
            client.is_active = False
            client.status = "deleted"
        case _:
            raise ValueError(f"Unknown batch action: {action}")

