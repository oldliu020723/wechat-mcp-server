"""HTTP 传输的 Bearer 令牌认证。

为什么不用 SDK 内置的认证：``FastMCP`` 的 ``auth`` 参数走的是 OAuth
Resource Server 流程（必须提供 ``issuer_url`` / ``resource_server_url``，
客户端要先做元数据发现）。对一个自己人就近调用的私有服务来说太重了。
这里用最朴素的 Bearer 令牌，客户端只要带一个 header。
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import (
    Any,
    Awaitable,
    Callable,
    Final,
    FrozenSet,
    Iterable,
    Optional,
    Sequence,
)

from wechat_mcp.errors import ConfigError
from wechat_mcp.settings import LOOPBACK_HOSTS

logger = logging.getLogger(__name__)

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: 超长的 Authorization 头直接拒绝。它没有任何合法用途，却能被用来
#: 污染日志或消耗内存。
MAX_TOKEN_LENGTH: Final[int] = 512

#: 客户端令牌的最短长度。
MIN_TOKEN_LENGTH: Final[int] = 32


class BearerAuthMiddleware:
    """纯 ASGI 中间件，校验 ``Authorization: Bearer <token>``。

    刻意不继承 Starlette 的 ``BaseHTTPMiddleware``：后者会额外包一层 task group
    和 receive 包装，对 streamable-http 这类长连接流式响应是已知的麻烦来源。

    只接管 ``scope["type"] == "http"``，其余类型原样透传——**lifespan 必须透传**，
    否则 Starlette 的 session manager 根本不会启动。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        realm: str = "wechat-mcp",
        exempt_paths: FrozenSet[str] = frozenset({"/healthz"}),
    ) -> None:
        self._app = app
        self._expected = token.encode("utf-8")
        self._realm = realm
        self._exempt = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        if scope.get("path", "") in self._exempt:
            await self._app(scope, receive, send)
            return

        presented = self._extract(scope.get("headers") or [])
        # 常量时间比较，避免按字节逐位试探出令牌。
        if presented is None or not hmac.compare_digest(presented, self._expected):
            logger.warning(
                "认证失败，拒绝来自 %s 的请求：%s",
                scope.get("client", ("unknown", 0))[0],
                scope.get("path", ""),
            )
            await self._reject(send)
            return

        await self._app(scope, receive, send)

    def _extract(self, headers: Iterable[tuple[bytes, bytes]]) -> Optional[bytes]:
        """从请求头里取出 Bearer 令牌，任何异常形态都返回 ``None``。"""
        found: list[bytes] = []
        for name, value in headers:
            if name.lower() == b"authorization":
                found.append(value)

        # 多个 Authorization 头属于畸形请求，直接拒绝。
        if len(found) != 1:
            return None

        raw = found[0].strip()
        if len(raw) > MAX_TOKEN_LENGTH:
            return None

        scheme, _, credentials = raw.partition(b" ")
        if scheme.lower() != b"bearer":
            return None

        token = credentials.strip()
        return token or None

    async def _reject(self, send: Send) -> None:
        """统一返回 401：不区分"没带令牌"与"令牌错误"。"""
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", f'Bearer realm="{self._realm}"'.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def assert_safe_binding(
    host: str,
    auth_token: Optional[str],
    transport: str,
    allowed_hosts: Sequence[str] = (),
) -> None:
    """启动前的 fail-safe 校验。

    绑定到非回环地址却没有任何认证，等于把"用你的公众号发文章"这个能力开放给
    整个网络。这里选择直接拒绝启动，而不是打一条警告就放行——警告没人会看。

    Raises:
        ConfigError: 配置组合不安全。
    """
    if transport != "streamable-http":
        return

    if host in LOOPBACK_HOSTS:
        return

    if not auth_token:
        raise ConfigError(
            f"拒绝启动：绑定到非回环地址 {host!r} 却没有配置认证令牌。"
            f"请设置环境变量 WECHAT_MCP_AUTH_TOKEN，或改用 --host 127.0.0.1。"
        )

    if len(auth_token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"认证令牌过短（{len(auth_token)} 个字符），至少需要 {MIN_TOKEN_LENGTH} 个。"
        )

    if not allowed_hosts:
        # 实测（mcp 1.30.0）：FastMCP 只在 host 属于 {127.0.0.1, localhost, ::1}
        # 时才自动开启 DNS rebinding 防护，绑到别的地址时该配置为 None——
        # 也就是完全没有防护，而且不会有任何警告。这里强制要求显式声明。
        raise ConfigError(
            f"拒绝启动：绑定到非回环地址 {host!r} 时必须显式声明允许的 Host。"
            f"请用 --allowed-host 指定客户端实际访问时使用的主机名"
            f"（例如 --allowed-host mcp.internal），可重复指定多个。"
        )


__all__ = ["BearerAuthMiddleware", "assert_safe_binding", "MIN_TOKEN_LENGTH"]
