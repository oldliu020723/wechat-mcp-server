"""受保护下载的测试。全程使用 MockTransport，不碰真实网络。"""

from __future__ import annotations

import httpx
import pytest

from wechat_mcp.errors import ImageRejected, SSRFBlocked, UpstreamError
from wechat_mcp.security import safe_fetch

ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
ZIP = b"PK\x03\x04" + b"0" * 64


class FakeNetworkStream:
    """伪造 httpcore 的 network_stream，用于验证对端 IP 复核。"""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    def get_extra_info(self, info: str):
        if info == "server_addr":
            return (self._ip, 443)
        return None


class ChunkStream(httpx.AsyncByteStream):
    """按块产出的流式响应体，不带 Content-Length。

    用来覆盖"边下边计数"那条路径：用 ``content=`` 构造的响应会自动带上
    Content-Length，走的是提前拒绝的分支，测不到流式累计。
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _resolver(*ips: str):
    """构造一个返回固定 IP 列表的假解析器。"""

    def resolve(host: str, port: int) -> list[str]:
        return list(ips)

    return resolve


def _image_response(
    content: bytes = PNG,
    content_type: str = "image/png",
    status: int = 200,
    peer_ip: str | None = None,
) -> httpx.Response:
    extensions = {}
    if peer_ip is not None:
        extensions["network_stream"] = FakeNetworkStream(peer_ip)
    return httpx.Response(
        status,
        headers={"content-type": content_type},
        content=content,
        extensions=extensions,
    )


async def _fetch(url: str, handler, *, resolver=None, max_bytes: int = 1024):
    return await safe_fetch.fetch_image(
        url,
        allowed_types=ALLOWED,
        max_bytes=max_bytes,
        timeout=5.0,
        resolver=resolver or _resolver("93.184.216.34"),
        transport_factory=lambda: httpx.MockTransport(handler),
    )


async def test_successful_download():
    blob = await _fetch("https://example.com/a.png", lambda request: _image_response())

    assert blob.content_type == "image/png"
    assert blob.extension == ".png"
    assert blob.size == len(PNG)
    assert blob.data == PNG


async def test_request_is_pinned_to_validated_ip():
    """核心断言：请求必须打到已校验的 IP 上，同时保留原域名的 Host 与 SNI。"""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return _image_response()

    await _fetch("https://example.com/path/a.png?x=1", handler)

    assert str(captured["url"]).startswith("https://93.184.216.34/path/a.png?x=1")
    assert captured["host"] == "example.com"
    # SNI 保持原域名，HTTPS 证书校验才仍然针对域名而非 IP。
    assert captured["sni"] == "example.com"


async def test_private_ip_is_rejected_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发出任何请求")

    with pytest.raises(SSRFBlocked):
        await _fetch(
            "https://evil.test/a.png", handler, resolver=_resolver("127.0.0.1")
        )


async def test_mixed_records_rejected_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发出任何请求")

    with pytest.raises(SSRFBlocked):
        await _fetch(
            "https://evil.test/a.png",
            handler,
            resolver=_resolver("93.184.216.34", "169.254.169.254"),
        )


async def test_peer_ip_is_rechecked():
    """副防线：实际对端落在内网时，必须在读 body 之前中止。"""
    handler = lambda request: _image_response(peer_ip="10.0.0.5")

    with pytest.raises(SSRFBlocked):
        await _fetch("https://example.com/a.png", handler)


async def test_declared_octet_stream_but_magic_is_png():
    """兼容返回 application/octet-stream 的图床：以魔数为准。"""
    handler = lambda request: _image_response(content_type="application/octet-stream")

    blob = await _fetch("https://example.com/a.png", handler)
    assert blob.content_type == "image/png"


async def test_declared_png_but_magic_is_zip_is_rejected():
    """响应头声称是图片，实际是别的格式——必须拒绝。"""
    handler = lambda request: _image_response(content=ZIP, content_type="image/png")

    with pytest.raises(ImageRejected):
        await _fetch("https://example.com/a.png", handler)


async def test_disallowed_content_type_is_rejected():
    handler = lambda request: _image_response(
        content=b"<html></html>", content_type="text/html"
    )

    with pytest.raises(ImageRejected):
        await _fetch("https://example.com/a.png", handler)


async def test_oversized_content_length_is_rejected_early():
    """声明了超限的 Content-Length 时，连 body 都不必读完。"""
    big = b"\x89PNG\r\n\x1a\n" + b"0" * 5000
    handler = lambda request: _image_response(content=big)

    with pytest.raises(ImageRejected):
        await _fetch("https://example.com/a.png", handler, max_bytes=1024)


async def test_oversized_body_is_rejected_while_streaming():
    """没有 Content-Length 时，靠流式累计计数兜住。"""

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [b"\x89PNG\r\n\x1a\n"] + [b"0" * 512] * 20
        return httpx.Response(
            200, headers={"content-type": "image/png"}, stream=ChunkStream(chunks)
        )

    with pytest.raises(ImageRejected):
        await _fetch("https://example.com/a.png", handler, max_bytes=1024)


async def test_empty_body_is_rejected():
    handler = lambda request: _image_response(content=b"")

    with pytest.raises(ImageRejected):
        await _fetch("https://example.com/a.png", handler)


async def test_non_200_is_reported_with_generic_message():
    handler = lambda request: _image_response(status=404)

    with pytest.raises(UpstreamError) as excinfo:
        await _fetch("https://example.com/a.png", handler)

    assert str(excinfo.value) == safe_fetch.FETCH_FAILED_MESSAGE


async def test_connection_error_is_reported_with_generic_message():
    """连接层错误对外只给统一消息，不给端口探测留 oracle。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(UpstreamError) as excinfo:
        await _fetch("https://example.com/a.png", handler)

    assert str(excinfo.value) == safe_fetch.FETCH_FAILED_MESSAGE
    assert "refused" not in str(excinfo.value)


async def test_falls_back_to_next_ip():
    """第一个 IP 不通时应尝试下一个。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls.append(host)
        if host == "93.184.216.34":
            raise httpx.ConnectError("unreachable")
        return _image_response()

    blob = await _fetch(
        "https://example.com/a.png",
        handler,
        resolver=_resolver("93.184.216.34", "1.1.1.1"),
    )

    assert calls == ["93.184.216.34", "1.1.1.1"]
    assert blob.size == len(PNG)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("image/png", "image/png"),
        ("image/png; charset=binary", "image/png"),
        ("  IMAGE/JPEG  ", "image/jpeg"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_content_type(raw, expected):
    assert safe_fetch.normalize_content_type(raw) == expected


@pytest.mark.parametrize(
    "head,expected",
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (b"GIF89a....", "image/gif"),
        (b"BM......", "image/bmp"),
        (ZIP, None),
        (b"", None),
    ],
)
def test_sniff_image_type(head, expected):
    assert safe_fetch.sniff_image_type(head) == expected
