"""工具层的横切逻辑：限流、凭证解析、统一的错误处理。

这些逻辑原先在参照项目里被复制到了每个工具函数中（且每份都不太一样）。
集中到这里之后，每个工具函数只需要表达自己的业务流程。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from wechat_mcp.errors import WeChatMcpError
from wechat_mcp.redaction import redact_text
from wechat_mcp.settings import ENV_APPID, ENV_APPSECRET
from wechat_mcp.tools.context import AppContext

logger = logging.getLogger(__name__)

#: 触发限流时对外的话术。
RATE_LIMIT_MESSAGE = "接口调用过于频繁，已达到请求上限，请稍后再试"

#: 未预期异常对外统一使用的话术，细节只进日志。
INTERNAL_ERROR_MESSAGE = "服务内部错误，请查看服务端日志排查"

#: 拿不到可用 access_token 时的提示。
MISSING_TOKEN_MESSAGE = (
    "缺少可用的 access_token：请先调用 get_access_token 获取，"
    "或在调用时通过 access_token 参数传入"
)


def enforce_rate_limit(ctx: AppContext, scope: str, identifier: str) -> Optional[str]:
    """检查限流。

    Returns:
        超限时返回给调用方的提示文本；未超限返回 ``None``。
    """
    if ctx.limiter.allow(f"{scope}:{identifier}"):
        return None
    logger.warning("限流触发：scope=%s", scope)
    return RATE_LIMIT_MESSAGE


def resolve_credentials(
    appid: Optional[str], appsecret: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """解析公众号凭证：显式参数优先，其次环境变量。"""
    return (
        appid or os.getenv(ENV_APPID),
        appsecret or os.getenv(ENV_APPSECRET),
    )


def resolve_access_token(ctx: AppContext, access_token: Optional[str]) -> Optional[str]:
    """拿到可用的 access_token。

    显式传入的优先；否则用环境变量里配置的 appid 去缓存里找——这样客户端
    只要先调用过一次 ``get_access_token``，后续工具就不必反复传 token。
    """
    if access_token:
        return access_token

    appid, _ = resolve_credentials(None, None)
    if appid:
        return ctx.tokens.get(appid)
    return None


def basic_failure(message: str, errcode: Optional[int] = None) -> Any:
    """构造只含公共字段的失败信封（供返回 :class:`BasicResult` 的工具使用）。"""
    from wechat_mcp.tools.results import BasicResult

    return BasicResult(success=False, errcode=errcode, error_msg=message)


def error_fields(exc: BaseException, tool: str) -> dict[str, Any]:
    """把异常转换成可以安全回传给调用方的 ``errcode`` / ``error_msg``。

    - 本服务自己抛的 :class:`~wechat_mcp.errors.WeChatMcpError`：消息由我们
      构造，可以直接用，但仍过一遍脱敏作为纵深防御。
    - 其余异常：只进日志，对外给一句通用文案——异常原文可能带 URL 或凭证。
    """
    if isinstance(exc, WeChatMcpError):
        return {
            "errcode": getattr(exc, "errcode", None),
            "error_msg": redact_text(str(exc)),
        }

    logger.exception("工具 %s 执行时出现未预期异常", tool)
    return {"errcode": None, "error_msg": INTERNAL_ERROR_MESSAGE}


__all__ = [
    "RATE_LIMIT_MESSAGE",
    "INTERNAL_ERROR_MESSAGE",
    "MISSING_TOKEN_MESSAGE",
    "enforce_rate_limit",
    "resolve_credentials",
    "resolve_access_token",
    "basic_failure",
    "error_fields",
]
