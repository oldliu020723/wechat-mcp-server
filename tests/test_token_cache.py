"""access_token 缓存测试。"""

from __future__ import annotations

from wechat_mcp.wechat.token import TokenCache


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_put_then_get():
    cache = TokenCache(clock=FakeClock())
    cache.put("appid", "tok", expires_in=7200)

    assert cache.get("appid") == "tok"


def test_missing_appid_returns_none():
    cache = TokenCache(clock=FakeClock())

    assert cache.get("nobody") is None


def test_expires_early_by_the_margin():
    clock = FakeClock()
    cache = TokenCache(early_expiry_seconds=300, clock=clock)
    cache.put("appid", "tok", expires_in=1000)  # 实际有效 700 秒

    clock.advance(699)
    assert cache.get("appid") == "tok"

    clock.advance(2)
    assert cache.get("appid") is None


def test_short_expires_in_never_yields_negative_ttl():
    """回归测试：参照实现用 expires_in - 300，expires_in=100 时会算出负 TTL。"""
    clock = FakeClock()
    cache = TokenCache(early_expiry_seconds=300, clock=clock)
    cache.put("appid", "tok", expires_in=100)

    # 至少保证缓存 1 秒，行为可预期。
    assert cache.get("appid") == "tok"

    clock.advance(2)
    assert cache.get("appid") is None


def test_entry_count_is_bounded():
    """回归测试：参照实现只增不删，换 appid 就能把内存撑大。"""
    cache = TokenCache(max_entries=3, clock=FakeClock())

    for index in range(20):
        cache.put(f"app-{index}", f"tok-{index}", expires_in=7200)

    assert len(cache) <= 3


def test_sweep_removes_expired_entries():
    clock = FakeClock()
    cache = TokenCache(early_expiry_seconds=0, clock=clock)
    cache.put("a", "tok-a", expires_in=10)
    cache.put("b", "tok-b", expires_in=100)

    clock.advance(11)
    assert cache.sweep() == 1
    assert len(cache) == 1
    assert cache.get("b") == "tok-b"


def test_invalidate():
    cache = TokenCache(clock=FakeClock())
    cache.put("appid", "tok", expires_in=7200)

    cache.invalidate("appid")
    assert cache.get("appid") is None


def test_expired_entry_is_dropped_on_read():
    clock = FakeClock()
    cache = TokenCache(early_expiry_seconds=0, clock=clock)
    cache.put("appid", "tok", expires_in=10)

    clock.advance(11)
    assert cache.get("appid") is None
    # 读取时就顺手清理了，不必等到 sweep。
    assert len(cache) == 0
