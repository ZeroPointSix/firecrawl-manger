from __future__ import annotations

from app.core.concurrency import ConcurrencyManager


def test_concurrency_manager_try_acquire_and_release():
    mgr = ConcurrencyManager()

    a1 = mgr.try_acquire("k", 2)
    assert a1 is not None
    a2 = mgr.try_acquire("k", 2)
    assert a2 is not None
    a3 = mgr.try_acquire("k", 2)
    assert a3 is None

    assert mgr.current("k") == 2
    a1.release()
    assert mgr.current("k") == 1
    a2.release()
    assert mgr.current("k") == 0

