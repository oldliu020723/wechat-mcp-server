"""FastMCP 服务的装配与启动。

两条传输路径共用同一套工具注册逻辑：

- ``stdio``：由 MCP 客户端直接拉起子进程，不存在网络监听面，因此不做认证——
  能启动这个进程本身就意味着已经具备本地执行权限。
- ``streamable-http``：暴露 HTTP 端点，外面包一层 Bearer 认证；绑定非回环地址
  时还必须显式配置 DNS rebinding 防护（见 :func:`build_transport_security`）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from wechat_mcp.security.auth import BearerAuthMiddleware, assert_safe_binding
from wechat_mcp.settings import Settings
from wechat_mcp.tools.context import AppContext
from wechat_mcp.tools.registry import register_all

logger = logging.getLogger(__name__)

SERVER_NAME = "wechat-mcp-server"

INSTRUCTIONS = """\
本服务把微信公众号开放接口封装成 MCP 工具，可以创建草稿、发布内容、管理素材。

典型流程：
1. 调用 get_access_token 获取凭证（服务端会缓存，其余工具可省略该参数）。
2. 调用 create_wechat_draft 建草稿——它只建草稿，不会发布。
3. 用返回的 draft_media_id 调用 publish_wechat_draft 提交发布。
4. 发布是异步的，用 get_publish_status 查询最终结果。

注意：publish_wechat_draft 以及两个删除类工具都会产生不可撤销的后果，
调用前应当先向用户确认。
""".strip()


def build_transport_security(
    settings: Settings,
) -> Optional[TransportSecuritySettings]:
    """按绑定地址决定 DNS rebinding 防护配置。

    实测确认（mcp 1.30.0）：``FastMCP`` 只在 ``host`` 为 127.0.0.1 / localhost /
    ::1 时自动开启防护；绑到 ``0.0.0.0`` 等地址时 ``transport_security`` 是
    ``None``，即完全不设防，而且不会有任何警告。因此非回环绑定必须由我们显式补上。

    Returns:
        非回环绑定时返回显式配置；回环绑定返回 ``None``，沿用 SDK 自带的默认防护。
    """
    if settings.is_loopback:
        return None

    hosts: list[str] = []
    for host in settings.allowed_hosts:
        # 同时放行带端口与不带端口两种 Host 写法。
        hosts.extend([host, f"{host}:*"])

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{host}" for host in settings.allowed_hosts],
    )


def build_server(settings: Settings) -> tuple[FastMCP, AppContext]:
    """构造 FastMCP 实例并注册全部工具。

    Returns:
        (服务实例, 依赖容器)。调用方负责在结束时释放容器。
    """
    context = AppContext.create(settings)

    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        transport_security=build_transport_security(settings),
    )
    register_all(mcp, context)
    return mcp, context


def _close_context(context: AppContext) -> None:
    """尽力关闭底层连接池；失败也不应影响进程退出码。"""
    try:
        asyncio.run(context.aclose())
    except Exception:  # pragma: no cover - 退出路径上的清理
        logger.debug("关闭 HTTP 连接池失败", exc_info=True)


def run_server(settings: Settings) -> None:
    """按配置启动服务。"""
    # fail-safe：不安全的组合直接拒绝启动，而不是打条警告继续跑。
    assert_safe_binding(
        settings.host,
        settings.auth_token,
        settings.transport,
        settings.allowed_hosts,
    )

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if settings.transport == "stdio":
        run_stdio(settings)
    else:
        run_http(settings)


def run_stdio(settings: Settings) -> None:
    """以 stdio 传输运行。"""
    mcp, context = build_server(settings)
    logger.info("以 stdio 传输启动（无网络监听面）")
    try:
        mcp.run(transport="stdio")
    finally:
        _close_context(context)


def run_http(settings: Settings) -> None:
    """以 streamable-http 传输运行，并在外层挂上 Bearer 认证。"""
    import uvicorn

    mcp, context = build_server(settings)

    # 不用 mcp.run(transport="streamable-http")：那条路径不允许插入中间件。
    # 自己取 ASGI app 再包一层，等价且可控。认证中间件会透传 lifespan，
    # 所以 session manager 仍能正常启动。
    app = mcp.streamable_http_app()
    if settings.auth_token:
        app.add_middleware(BearerAuthMiddleware, token=settings.auth_token)

    logger.info("以 streamable-http 传输启动，监听 %s:%d", settings.host, settings.port)
    try:
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            # 关掉访问日志：请求路径可能带 query，统一不落盘更省心。
            access_log=False,
        )
    finally:
        _close_context(context)


__all__ = [
    "SERVER_NAME",
    "INSTRUCTIONS",
    "build_transport_security",
    "build_server",
    "run_server",
    "run_stdio",
    "run_http",
]
