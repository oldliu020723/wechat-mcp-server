"""``get_access_token`` 的业务实现。"""

from __future__ import annotations

import logging
from typing import Optional

from wechat_mcp.tools.context import AppContext
from wechat_mcp.tools.guards import (
    enforce_rate_limit,
    error_fields,
    resolve_credentials,
)
from wechat_mcp.tools.results import TokenResult

logger = logging.getLogger(__name__)

MISSING_CREDENTIALS_MESSAGE = (
    "缺少公众号凭证：请在调用时传入 appid 与 appsecret，"
    "或设置环境变量 WECHAT_APPID / WECHAT_APPSECRET"
)


def _failure(message: str, errcode: Optional[int] = None) -> TokenResult:
    return TokenResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        access_token=None,
        expires_in=None,
        from_cache=False,
    )


async def fetch_access_token(
    ctx: AppContext,
    *,
    appid: Optional[str] = None,
    appsecret: Optional[str] = None,
    force_refresh: bool = False,
) -> TokenResult:
    """获取 access_token，优先走进程内缓存。

    微信的 token 有效期 7200 秒，且重新签发会让旧 token 立即失效——所以缓存
    不只是省请求，也避免并发调用互相踢掉对方的 token。
    """
    resolved_appid, resolved_secret = resolve_credentials(appid, appsecret)

    if not resolved_appid or not resolved_secret:
        return _failure(MISSING_CREDENTIALS_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "access_token", resolved_appid)
    if limit_message:
        return _failure(limit_message)

    if not force_refresh:
        cached = ctx.tokens.get(resolved_appid)
        if cached:
            logger.debug("命中 token 缓存")
            return TokenResult(
                success=True,
                errcode=0,
                error_msg=None,
                access_token=cached,
                expires_in=None,
                from_cache=True,
            )

    try:
        payload = await ctx.wechat.fetch_access_token(resolved_appid, resolved_secret)
    except Exception as exc:
        fields = error_fields(exc, "get_access_token")
        return _failure(fields["error_msg"], fields["errcode"])

    token = payload.get("access_token")
    if not token:
        return _failure("微信接口未返回 access_token")

    try:
        expires_in = int(payload.get("expires_in", 7200))
    except (TypeError, ValueError):
        expires_in = 7200

    ctx.tokens.put(resolved_appid, str(token), expires_in)
    logger.info("已刷新 access_token（有效期 %d 秒）", expires_in)

    return TokenResult(
        success=True,
        errcode=0,
        error_msg=None,
        access_token=str(token),
        expires_in=expires_in,
        from_cache=False,
    )


__all__ = ["fetch_access_token", "MISSING_CREDENTIALS_MESSAGE"]
