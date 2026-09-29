"""工具层的依赖容器。

刻意用显式传参而不是模块级全局单例：测试可以直接构造一份假的上下文，
不必去 monkeypatch 模块状态，也就不会出现"测试之间互相污染"的问题。
"""

from __future__ import annotations

from dataclasses import dataclass

from wechat_mcp.security.rate_limit import SlidingWindowLimiter
from wechat_mcp.settings import Settings
from wechat_mcp.wechat.http import WeChatAPIClient
from wechat_mcp.wechat.token import TokenCache


@dataclass
class AppContext:
    """一次进程生命周期内共享的运行时依赖。"""

    settings: Settings
    wechat: WeChatAPIClient
    tokens: TokenCache
    limiter: SlidingWindowLimiter

    @classmethod
    def create(cls, settings: Settings) -> "AppContext":
        """按配置构造一份标准上下文。"""
        return cls(
            settings=settings,
            wechat=WeChatAPIClient(timeout=settings.http_timeout),
            tokens=TokenCache(),
            limiter=SlidingWindowLimiter(
                limit=settings.requests_per_minute,
                window_seconds=60.0,
            ),
        )

    async def aclose(self) -> None:
        """释放底层 HTTP 连接池。"""
        await self.wechat.aclose()


__all__ = ["AppContext"]
