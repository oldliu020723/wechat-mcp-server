"""日志与错误信息的脱敏工具。

背景：``requests`` / ``httpx`` 这类 HTTP 库抛出的异常，其 ``str()`` 往往包含
**完整请求 URL**。而微信开放接口把凭证放在 query string 里
（``?access_token=...``、``?secret=...``），于是"把异常原文当作 error_msg
返回给调用方"就等于把 AppSecret / access_token 直接交给了对方，并会进一步
落进客户端日志与对话记录。

本模块提供统一的擦除函数，作为对外输出前的最后一道关卡。
"""

from __future__ import annotations

import re
from typing import Any, Final, Mapping

#: 已知的敏感 query 参数名（小写）。
SENSITIVE_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "secret",
        "appsecret",
        "app_secret",
        "token",
        "signature",
        "password",
        "key",
    }
)

#: 形如 `key=value` 的 query 片段，用于文本级擦除。
_QUERY_PAIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^&\s'\"]+)"
)

#: 连续 32 位以上的字母数字串：微信 token 的典型形态，作为兜底擦除。
_LONG_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")

MASK: Final[str] = "***"


def redact_text(text: str) -> str:
    """擦除任意文本中的凭证痕迹。

    处理两件事：

    1. 把敏感参数的取值替换为 ``***``（``secret=abc`` → ``secret=***``）；
    2. 把剩下任何长度 >= 32 的疑似 token 串也替换掉，覆盖参数名未知的情况。

    Args:
        text: 原始文本，通常是异常信息或日志行。

    Returns:
        脱敏后的文本。若入参为空则原样返回。
    """
    if not text:
        return text

    def _mask_pair(match: re.Match[str]) -> str:
        key = match.group("key")
        if key.lower() in SENSITIVE_QUERY_KEYS:
            return f"{key}={MASK}"
        return match.group(0)

    masked = _QUERY_PAIR_RE.sub(_mask_pair, text)
    return _LONG_TOKEN_RE.sub(MASK, masked)


def redact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """返回参数映射的副本，敏感项的取值替换为 ``***``。"""
    return {
        key: (MASK if str(key).lower() in SENSITIVE_QUERY_KEYS else value)
        for key, value in params.items()
    }


def summarize_url(url: str) -> str:
    """把 URL 压缩成只含 scheme、主机与路径的形式，丢掉 query 与 fragment。

    用于日志：既保留定位问题所需的信息，又不带出 query 中的凭证。
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return "<无法解析的 URL>"

    host = parts.hostname or ""
    # 主机名本身可能被塞进敏感信息之外的用户数据，只保留常见形态。
    return f"{parts.scheme}://{host}{parts.path}"


__all__ = [
    "MASK",
    "SENSITIVE_QUERY_KEYS",
    "redact_text",
    "redact_params",
    "summarize_url",
]
