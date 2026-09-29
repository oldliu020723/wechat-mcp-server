"""微信客户端测试。重点是**凭证绝不外泄**这一条不变量。"""

from __future__ import annotations

import httpx
import pytest

from wechat_mcp.errors import UpstreamError, WeChatAPIError
from wechat_mcp.wechat import endpoints
from wechat_mcp.wechat.http import WeChatAPIClient

# 刻意做得像真实凭证：够长，且不会被脱敏正则误判为无关文本。
APPSECRET = "SUPERSECRET_abcdefghijklmnopqrstuvwxyz012345"
ACCESS_TOKEN = "ACCESSTOKEN_abcdefghijklmnopqrstuvwxyz0123"


def _client(handler) -> WeChatAPIClient:
    return WeChatAPIClient(transport=httpx.MockTransport(handler))


async def test_successful_token_fetch():
    def handler(request):
        return httpx.Response(
            200, json={"access_token": "TOKENVALUE", "expires_in": 7200}
        )

    async with _client(handler) as client:
        payload = await client.fetch_access_token("wxappid", APPSECRET)

    assert payload["access_token"] == "TOKENVALUE"
    assert payload["expires_in"] == 7200


async def test_requests_always_go_to_the_wechat_origin():
    captured: dict[str, str] = {}

    def handler(request):
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        return httpx.Response(200, json={"errcode": 0})

    async with _client(handler) as client:
        await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})

    assert captured["host"] == "api.weixin.qq.com"
    assert captured["path"] == endpoints.PATH_DRAFT_COUNT


async def test_appsecret_never_leaks_into_error_message():
    """回归测试：参照实现把 requests 异常原文回传，其中含 `secret=<AppSecret>`。"""

    def handler(request):
        raise httpx.ConnectError(f"failed while connecting to {request.url}")

    async with _client(handler) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.fetch_access_token("wxappid", APPSECRET)

    message = str(excinfo.value)
    assert APPSECRET not in message
    assert "secret=" not in message


async def test_access_token_never_leaks_into_error_message():
    """access_token 同样只出现在 query string 里，一样不能回传。"""

    def handler(request):
        raise httpx.ConnectError(f"failed while connecting to {request.url}")

    async with _client(handler) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.post_json(
                endpoints.PATH_DRAFT_DELETE,
                params={"access_token": ACCESS_TOKEN},
                payload={"media_id": "m"},
            )

    message = str(excinfo.value)
    assert ACCESS_TOKEN not in message
    assert "access_token=" not in message


async def test_wechat_business_error_becomes_exception():
    def handler(request):
        return httpx.Response(
            200, json={"errcode": 40001, "errmsg": "invalid credential"}
        )

    async with _client(handler) as client:
        with pytest.raises(WeChatAPIError) as excinfo:
            await client.fetch_access_token("wxappid", APPSECRET)

    assert excinfo.value.errcode == 40001
    assert "40001" in str(excinfo.value)
    # 附带处理建议与错误码对照表链接
    assert "access_token" in str(excinfo.value)
    assert endpoints.ERROR_DOC_URL in str(excinfo.value)


async def test_errcode_zero_is_success():
    def handler(request):
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "total_count": 3})

    async with _client(handler) as client:
        payload = await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})

    assert payload["total_count"] == 3


async def test_non_200_is_rejected():
    def handler(request):
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        with pytest.raises(UpstreamError):
            await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})


async def test_redirects_are_not_followed():
    """302 不能被自动跟随，否则请求会跑到我们没校验过的地方。"""
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.test/steal"})

    async with _client(handler) as client:
        with pytest.raises(UpstreamError):
            await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})

    assert len(calls) == 1
    assert "evil.test" not in calls[0]


async def test_non_json_response_is_rejected():
    def handler(request):
        return httpx.Response(200, text="<html>not json</html>")

    async with _client(handler) as client:
        with pytest.raises(UpstreamError):
            await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})


async def test_oversized_response_is_rejected():
    big = b"x" * (3 * 1024 * 1024)

    def handler(request):
        return httpx.Response(200, content=big)

    async with _client(handler) as client:
        with pytest.raises(UpstreamError):
            await client.get_json(endpoints.PATH_DRAFT_COUNT, params={"access_token": "t"})


async def test_multipart_upload_sends_bytes_without_touching_disk():
    captured: dict[str, object] = {}

    def handler(request):
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.read()
        return httpx.Response(200, json={"errcode": 0, "media_id": "MEDIA123"})

    async with _client(handler) as client:
        payload = await client.post_multipart(
            endpoints.PATH_MATERIAL_ADD,
            params={"access_token": "t", "type": "image"},
            field_name="media",
            filename="cover.png",
            content=b"\x89PNG\r\n\x1a\nbin",
            content_type="image/png",
        )

    assert payload["media_id"] == "MEDIA123"
    assert "multipart/form-data" in str(captured["content_type"])
    assert b"\x89PNG" in captured["body"]
