"""微信接口错误码的解析与中文说明。

微信约定响应里的 ``errcode`` 为 0 或缺省表示成功，非 0 即为业务错误。
把常见错误码翻译成人话，能让调用它的模型直接知道下一步该做什么
（重新取 token、去后台加 IP 白名单……），而不是干瞪着一个数字。
"""

from __future__ import annotations

from typing import Any, Final, Mapping, Optional

from wechat_mcp.errors import WeChatAPIError
from wechat_mcp.redaction import redact_text
from wechat_mcp.wechat import endpoints

#: 常见错误码的处理提示。
ERRCODE_HINTS: Final[dict[int, str]] = {
    40001: "access_token 无效，请重新获取",
    40007: "media_id 非法或已失效",
    40013: "appid 无效",
    40014: "access_token 无效",
    40125: "appsecret 无效，请检查配置",
    40164: "调用方 IP 不在白名单内，请到公众号后台添加服务器出口 IP",
    41001: "缺少 access_token 参数",
    42001: "access_token 已过期，请重新获取",
    45009: "接口调用超过当日限额",
    45007: "语音播放时间超限",
    48001: "接口未授权，当前账号没有该接口的调用权限",
    53500: "发布失败，请确认账号具备发布接口权限",
}


def is_success(payload: Mapping[str, Any]) -> bool:
    """判断微信响应是否表示成功。"""
    errcode = payload.get("errcode", 0)
    return errcode in (0, None)


def error_from_payload(payload: Mapping[str, Any]) -> Optional[WeChatAPIError]:
    """把微信响应里的错误码转成异常对象；成功时返回 ``None``。

    返回消息已做脱敏，并附带错误码对照表链接。
    """
    if is_success(payload):
        return None

    raw_code = payload.get("errcode")
    try:
        code: Optional[int] = int(raw_code)
    except (TypeError, ValueError):
        code = None

    # errmsg 由微信返回，正常情况下是固定文案；仍然过一遍脱敏，
    # 避免异常上游把凭证塞进错误描述里。
    message = redact_text(str(payload.get("errmsg", "未知错误")))
    if code is not None:
        message = f"{message}（errcode: {code}）"

    hint = ERRCODE_HINTS.get(code) if code is not None else None
    if hint:
        message = f"{message}。{hint}"

    message = f"{message}。错误码对照表：{endpoints.ERROR_DOC_URL}"
    return WeChatAPIError(code, message)


__all__ = ["ERRCODE_HINTS", "is_success", "error_from_payload"]
