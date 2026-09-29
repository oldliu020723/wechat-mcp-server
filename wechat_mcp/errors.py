"""本服务的异常层次。

所有对外暴露的错误都继承自 :class:`WeChatMcpError`，便于工具层统一捕获后
转换成结构化的失败响应，而不是把原始 traceback 泄漏给调用方。
"""

from __future__ import annotations


class WeChatMcpError(Exception):
    """本服务所有异常的基类。"""


class ConfigError(WeChatMcpError):
    """启动配置不合法，例如绑定到非回环地址却没有配置认证令牌。"""


class SSRFBlocked(WeChatMcpError):
    """目标 URL 指向了非公网地址（内网、回环、链路本地等），已拦截。

    这是安全拦截而**不是**普通网络错误，调用方不应重试同一 URL。
    """


class ImageRejected(WeChatMcpError):
    """图片不符合微信素材要求（类型不支持、超出大小上限等）。"""


class UpstreamError(WeChatMcpError):
    """访问微信接口时的网络层异常（连接失败、超时、非法响应等）。"""


class WeChatAPIError(WeChatMcpError):
    """微信接口返回了业务错误码。

    ``message`` 应当是**已经拼装完成、且经过脱敏**的文本，由调用方负责构造
    （见 :func:`wechat_mcp.wechat.errors.error_from_payload`）。
    """

    def __init__(self, errcode: int | None, message: str) -> None:
        self.errcode = errcode
        self.errmsg = message
        super().__init__(message)


__all__ = [
    "WeChatMcpError",
    "ConfigError",
    "SSRFBlocked",
    "ImageRejected",
    "UpstreamError",
    "WeChatAPIError",
]
