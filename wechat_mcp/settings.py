"""运行时可配置项。

配置来源优先级：命令行参数 > 环境变量 > 代码默认值。
所有默认值都取"安全的那一侧"：只监听回环、开启限流、不放宽图片限制。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

Transport = Literal["stdio", "streamable-http"]

#: 回环地址集合。绑定在这些地址上时，进程不对外暴露网络面。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_REQUESTS_PER_MINUTE = 30

#: 微信永久素材图片上限为 10MB，与官方限制保持一致。
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_HTTP_TIMEOUT = 15.0

ENV_AUTH_TOKEN = "WECHAT_MCP_AUTH_TOKEN"
ENV_APPID = "WECHAT_APPID"
ENV_APPSECRET = "WECHAT_APPSECRET"


@dataclass(frozen=True)
class Settings:
    """一次进程生命周期内不变的运行参数。"""

    transport: Transport = "stdio"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    auth_token: Optional[str] = None
    allowed_hosts: tuple[str, ...] = ()
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    log_level: str = "INFO"

    @property
    def is_loopback(self) -> bool:
        """当前监听地址是否只对本机可见。"""
        return self.host in LOOPBACK_HOSTS

    @property
    def needs_auth(self) -> bool:
        """是否需要启用认证。

        仅当绑定到非回环地址时才需要——stdio 传输没有网络面，
        本地回环也只有本机进程可访问。
        """
        return not self.is_loopback

    @property
    def rate_limit_enabled(self) -> bool:
        """负值表示显式关闭限流。"""
        return self.requests_per_minute >= 0


def settings_from_env(
    *,
    transport: Transport = "stdio",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    auth_token: Optional[str] = None,
    allowed_hosts: Sequence[str] = (),
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    log_level: str = "INFO",
) -> Settings:
    """构造 Settings，认证令牌在显式参数为空时回退到环境变量。"""
    token = auth_token if auth_token is not None else os.getenv(ENV_AUTH_TOKEN)
    # 空白字符串等同于未配置，避免 `export TOKEN=` 这类误操作静默禁用认证。
    if token is not None and not token.strip():
        token = None

    return Settings(
        transport=transport,
        host=host,
        port=port,
        auth_token=token,
        allowed_hosts=tuple(h.strip() for h in allowed_hosts if h and h.strip()),
        requests_per_minute=requests_per_minute,
        max_image_bytes=max_image_bytes,
        http_timeout=http_timeout,
        log_level=log_level,
    )


def load_credentials() -> tuple[Optional[str], Optional[str]]:
    """从环境变量读取默认的公众号凭证（供 create_wechat_draft 等工具回退使用）。

    Returns:
        (appid, appsecret)，任一项未配置则为 None。
    """
    appid = os.getenv(ENV_APPID) or None
    appsecret = os.getenv(ENV_APPSECRET) or None
    return appid, appsecret


__all__ = [
    "Settings",
    "Transport",
    "LOOPBACK_HOSTS",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "DEFAULT_MAX_IMAGE_BYTES",
    "DEFAULT_HTTP_TIMEOUT",
    "ENV_AUTH_TOKEN",
    "ENV_APPID",
    "ENV_APPSECRET",
    "settings_from_env",
    "load_credentials",
]
