"""微信开放接口的异步 HTTP 客户端。

三条硬性约束，都写在代码里而不是文档里：

1. **origin 硬编码**，永不接受调用方传入——否则这个类就成了一个任意请求发起器。
2. **不跟随重定向**，非 200 直接报错。
3. **错误信息一律脱敏**。微信把凭证放在 query string 里，而 HTTP 库的异常文本
   通常带完整 URL。参照实现直接返回 ``f"Network error: {exc}"``，等于把
   AppSecret（token 接口）或 access_token（其余接口）交给了调用方。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final, Mapping, Optional

import httpx

from wechat_mcp.errors import UpstreamError
from wechat_mcp.redaction import redact_text
from wechat_mcp.wechat import endpoints
from wechat_mcp.wechat.errors import error_from_payload

logger = logging.getLogger(__name__)

#: 微信接口的响应通常只有几百字节，2MB 是很宽松的上限，足以挡住异常上游。
DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024

_CHUNK_SIZE: Final[int] = 64 * 1024

_USER_AGENT: Final[str] = "wechat-mcp-server/1.0"


class WeChatAPIClient:
    """微信公众号开放接口的薄封装。"""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        """
        Args:
            timeout: 单次请求超时（秒）。
            max_response_bytes: 响应体上限。
            transport: 传输层，测试时注入 ``httpx.MockTransport``。
        """
        self._max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            base_url=endpoints.API_ORIGIN,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            # 不接受代理等环境变量注入，避免请求被改道。
            trust_env=False,
            transport=transport,
            headers={"User-Agent": _USER_AGENT},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "WeChatAPIClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- 内部实现 ---------------------------------------------------------

    async def _read_capped(self, response: httpx.Response, path: str) -> bytes:
        """读取响应体，超过上限立即中止。"""
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max_response_bytes:
            raise UpstreamError(f"微信接口响应过大（{path}）")

        body = bytearray()
        async for chunk in response.aiter_bytes(_CHUNK_SIZE):
            body.extend(chunk)
            if len(body) > self._max_response_bytes:
                raise UpstreamError(f"微信接口响应过大（{path}）")
        return bytes(body)

    async def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        json_body: Optional[Mapping[str, Any]] = None,
        files: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """发起请求，返回已确认无业务错误的 JSON 对象。"""
        method = "POST" if (json_body is not None or files is not None) else "GET"

        try:
            request = self._client.build_request(
                method,
                path,
                params=dict(params),
                # httpx 不允许 json 与 files 同时出现。
                json=None if files is not None else (dict(json_body) if json_body else None),
                files=files,
            )
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            # 异常原文可能含完整 URL（里面有凭证），只进日志且必须先脱敏。
            logger.warning("请求 %s 失败：%s", path, redact_text(str(exc)))
            raise UpstreamError(
                f"访问微信接口失败（{path}）：{type(exc).__name__}"
            ) from exc

        try:
            raw = await self._read_capped(response, path)
        finally:
            await response.aclose()

        if response.status_code != 200:
            raise UpstreamError(f"微信接口返回 HTTP {response.status_code}（{path}）")

        try:
            payload = json.loads(raw)
        except ValueError as exc:
            logger.warning("微信接口 %s 返回了非 JSON 内容", path)
            raise UpstreamError(f"微信接口返回了非 JSON 响应（{path}）") from exc

        if not isinstance(payload, dict):
            raise UpstreamError(f"微信接口返回了非预期的响应结构（{path}）")

        error = error_from_payload(payload)
        if error is not None:
            raise error

        return payload

    # -- 对外接口 ---------------------------------------------------------

    async def get_json(self, path: str, *, params: Mapping[str, str]) -> dict[str, Any]:
        """发起 GET 请求并返回 JSON。"""
        return await self._request_json(path, params=params)

    async def post_json(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """发起带 JSON body 的 POST 请求。"""
        return await self._request_json(path, params=params, json_body=payload)

    async def post_multipart(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        field_name: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        """上传一段内存中的二进制内容（用于图片素材）。

        全程不落磁盘：图片已在 :mod:`wechat_mcp.security.safe_fetch` 里读进内存，
        这里直接交给 httpx 组 multipart。
        """
        files = {field_name: (filename, content, content_type)}
        return await self._request_json(path, params=params, files=files)

    async def fetch_access_token(self, appid: str, appsecret: str) -> dict[str, Any]:
        """调用 token 接口换取 access_token。"""
        return await self.get_json(
            endpoints.PATH_TOKEN,
            params={
                "grant_type": "client_credential",
                "appid": appid,
                "secret": appsecret,
            },
        )


__all__ = ["WeChatAPIClient", "DEFAULT_MAX_RESPONSE_BYTES"]
