"""限流器测试。重点在"有界"——时间和空间两个维度都不能无限增长。"""

from __future__ import annotations

import pytest

from wechat_mcp.security.rate_limit import SlidingWindowLimiter


class FakeClock:
    """可控时钟，避免测试依赖真实时间流逝。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_the_limit_then_blocks():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60, clock=clock)

    assert [limiter.allow("a") for _ in range(3)] == [True, True, True]
    assert limiter.allow("a") is False


def test_window_slides_and_recovers():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.allow("a")
    assert limiter.allow("a")
    assert limiter.allow("a") is False

    clock.advance(61)
    assert limiter.allow("a") is True


def test_different_keys_are_independent():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_negative_limit_disables_throttling():
    limiter = SlidingWindowLimiter(limit=-1)

    assert all(limiter.allow("a") for _ in range(1000))


def test_sweep_drops_idle_keys():
    """回归测试：参照实现里的字典只增不删，会造成内存泄漏。"""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, clock=clock)

    for index in range(100):
        limiter.allow(f"key-{index}")
    assert len(limiter) == 100

    clock.advance(61)
    assert limiter.sweep() == 100
    assert len(limiter) == 0


def test_tracked_keys_are_capped():
    """回归测试：轮换 identifier 不能把内存撑爆。"""
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, max_keys=50)

    for index in range(5000):
        limiter.allow(f"key-{index}")

    assert len(limiter) <= 50


def test_sweep_keeps_active_keys():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, clock=clock)

    limiter.allow("fresh")
    clock.advance(30)
    limiter.allow("stale")
    clock.advance(31)  # "fresh" 超窗，"stale" 还有 29 秒

    assert limiter.sweep() == 1
    assert len(limiter) == 1


def test_window_boundary_is_exclusive():
    """恰好在窗口边界上的时间戳应被判定为已过期。"""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("a") is True
    assert limiter.allow("a") is False

    clock.advance(60)
    assert limiter.allow("a") is True
