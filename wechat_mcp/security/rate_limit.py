"""带过期清理与容量上限的滑动窗口限流器。

限流本身不是安全边界（凭证泄露了照样能直连微信），它的作用是**限制爆炸半径**：
一个失控的客户端或一段错误的循环不至于把公众号的接口配额打满。

本模块刻意做成"有界"的，两个维度都是：

- **时间有界**：超出窗口的时间戳会被丢弃；
- **空间有界**：跟踪的 key 数量不超过 ``max_keys``。

参照项目用的是只增不删的字典，调用方只要不断更换 identifier，就能同时绕过限流
并把内存撑爆；这里两处都堵上了。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import deque
from typing import Callable, Deque, Final, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_KEYS: Final[int] = 4096
DEFAULT_SWEEP_EVERY: Final[int] = 256


class SlidingWindowLimiter:
    """按 key 计数的滑动窗口限流器。

    非线程安全：设计上只在单个 asyncio 事件循环里使用（``allow()`` 是纯同步、
    不含 ``await`` 的临界区，因而天然原子）。若将来要跨线程共享，需要重新评估。
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 60.0,
        max_keys: int = DEFAULT_MAX_KEYS,
        sweep_every: int = DEFAULT_SWEEP_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            limit: 窗口内允许的请求数。负值表示不限制。
            window_seconds: 时间窗口长度。
            max_keys: 最多跟踪多少个 key，超出后淘汰最早活动的那个。
            sweep_every: 每多少次调用做一次全量清理。
            clock: 时钟函数，便于测试注入；默认 ``time.monotonic``
                （不用 ``time.time``，墙上时钟回拨会让窗口失效）。
        """
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max(1, max_keys)
        self._sweep_every = max(1, sweep_every)
        self._clock = clock

        self._buckets: dict[str, Deque[float]] = {}
        self._calls_since_sweep = 0

        # 对 key 做带盐摘要：access_token / appid 就不会以明文形式躺在字典键上，
        # 堆转储、调试器或一次意外的 repr 都不会把它们带出去。
        self._salt = secrets.token_bytes(16)
        self._digest_cache: dict[str, str] = {}

    # -- 内部工具 ---------------------------------------------------------

    def _digest(self, key: str) -> str:
        cached = self._digest_cache.get(key)
        if cached is not None:
            return cached

        digest = hashlib.blake2b(
            key.encode("utf-8", "replace"), key=self._salt, digest_size=8
        ).hexdigest()

        # 摘要缓存也要有界，否则它自己变成新的内存泄漏点。
        if len(self._digest_cache) >= self._max_keys:
            self._digest_cache.clear()
        self._digest_cache[key] = digest
        return digest

    def _evict_one(self) -> None:
        """淘汰最早活动的 key。"""
        if not self._buckets:
            return
        oldest_key: Optional[str] = None
        oldest_stamp = float("inf")
        for key, bucket in self._buckets.items():
            stamp = bucket[-1] if bucket else 0.0
            if stamp < oldest_stamp:
                oldest_stamp = stamp
                oldest_key = key
        if oldest_key is not None:
            del self._buckets[oldest_key]
            logger.warning(
                "限流器 key 数量达到上限 %d，已淘汰最早活动的条目；"
                "若这不是预期行为，说明有客户端在轮换 identifier",
                self._max_keys,
            )

    # -- 对外接口 ---------------------------------------------------------

    def allow(self, key: str) -> bool:
        """判断该 key 本次调用是否在配额内。"""
        if self._limit < 0:
            return True

        now = self._clock()
        digest = self._digest(key)

        bucket = self._buckets.get(digest)
        if bucket is None:
            if len(self._buckets) >= self._max_keys:
                self._evict_one()
            bucket = deque()
            self._buckets[digest] = bucket

        # 丢弃窗口外的时间戳
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self._limit:
            return False

        bucket.append(now)

        self._calls_since_sweep += 1
        if self._calls_since_sweep >= self._sweep_every:
            self.sweep(now)
        return True

    def sweep(self, now: Optional[float] = None) -> int:
        """清掉窗口内已无事件的 key，返回清理掉的条目数。"""
        moment = self._clock() if now is None else now
        cutoff = moment - self._window

        stale = [
            key
            for key, bucket in self._buckets.items()
            if not bucket or bucket[-1] <= cutoff
        ]
        for key in stale:
            del self._buckets[key]

        self._calls_since_sweep = 0
        return len(stale)

    def __len__(self) -> int:
        """当前跟踪的 key 数量，供测试断言内存有界。"""
        return len(self._buckets)


__all__ = ["SlidingWindowLimiter", "DEFAULT_MAX_KEYS", "DEFAULT_SWEEP_EVERY"]
