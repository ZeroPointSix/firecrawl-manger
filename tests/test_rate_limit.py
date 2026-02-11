from __future__ import annotations

from app.core.rate_limit import TokenBucketRateLimiter


def test_rate_limiter_rejects_when_tokens_exhausted():
    rl = TokenBucketRateLimiter()

    ok, _ = rl.allow("c1", 1)
    assert ok is True

    ok2, retry_after = rl.allow("c1", 1)
    assert ok2 is False
    assert retry_after >= 1

