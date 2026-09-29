"""access_token 的进程内缓存。

微信的 access_token 有效期 7200 秒，且**重新签发会让旧 token 失效**，所以
缓存不只是省流量，也是避免并发调用互相踢掉 token 的必要手段。

这里的缓存是"有界"的：有 TTL、有条目数上限、有惰性清理。参照实现用的是
只增不删的字典，调用方换个 appid 就能让内存一直涨。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Final, Optional

#: 最多缓存多少个 appid 的 token。单机服务不会有这么多公众号。
DEFAULT_MAX_ENTRIES: Final[int] = 64

#: 提前过期的时间，避免在临界点上用到刚失效的 token。
DEFAULT_EARLY_EXPIRY_SECONDS: Final[int] = 300


@dataclass(frozen=True)
class _Entry:
    token: str
    expires_at: float


class TokenCache:
    """按 appid 索引的 token 缓存。"""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        early_expiry_seconds: int = DEFAULT_EARLY_EXPIRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            max_entries: 最多缓存条目数，超出后淘汰最早过期的。
            early_expiry_seconds: 提前过期的秒数。
            clock: 时钟函数，便于测试注入（默认用单调时钟，不受系统时间调整影响）。
        """
        self._max_entries = max(1, max_entries)
        self._early_expiry = max(0, early_expiry_seconds)
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    def get(self, appid: str) -> Optional[str]:
        """取缓存中的 token；不存在或已过期时返回 ``None`` 并顺手清掉该条目。"""
        entry = self._entries.get(appid)
        if entry is None:
            return None

        if self._clock() >= entry.expires_at:
            del self._entries[appid]
            return None

        return entry.token

    def put(self, appid: str, token: str, expires_in: int) -> None:
        """写入 token。

        有效期取 ``max(1, expires_in - early_expiry)``。参照实现直接做减法，
        当微信返回的 ``expires_in`` 小于提前量（异常或降级响应）时会算出已经
        过去的过期时间，行为不可预期；这里保证至少缓存 1 秒，不会有负 TTL。
        """
        ttl = max(1, int(expires_in) - self._early_expiry)

        if appid not in self._entries and len(self._entries) >= self._max_entries:
            self._evict_one()

        self._entries[appid] = _Entry(token=token, expires_at=self._clock() + ttl)

    def invalidate(self, appid: str) -> None:
        """主动失效某个 appid 的缓存（例如微信返回了 token 失效错误码）。"""
        self._entries.pop(appid, None)

    def sweep(self) -> int:
        """清掉所有已过期条目，返回清理数量。"""
        now = self._clock()
        stale = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in stale:
            del self._entries[key]
        return len(stale)

    def _evict_one(self) -> None:
        """淘汰最早过期的条目。"""
        if not self._entries:
            return
        oldest = min(self._entries, key=lambda key: self._entries[key].expires_at)
        del self._entries[oldest]

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "TokenCache",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_EARLY_EXPIRY_SECONDS",
]
