"""Bearer 认证中间件与启动期绑定校验的测试。"""

from __future__ import annotations

import httpx
import pytest

from wechat_mcp.errors import ConfigError
from wechat_mcp.security.auth import BearerAuthMiddleware, assert_safe_binding

TOKEN = "t" * 40


async def _ok_app(scope, receive, send):
    """一个永远返回 200 的极简 ASGI 应用。"""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})


def _make_client(*, authorization: str | None = None, **kwargs):
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, **kwargs)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": authorization} if authorization is not None else {}
    return httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    )


async def test_missing_authorization_is_rejected():
    async with _make_client() as client:
        response = await client.post("/mcp")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_correct_token_is_accepted():
    async with _make_client(authorization=f"Bearer {TOKEN}") as client:
        response = await client.post("/mcp")
    assert response.status_code == 200


async def test_wrong_token_is_rejected():
    async with _make_client(authorization="Bearer " + "x" * 40) as client:
        response = await client.post("/mcp")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        f"bearer {TOKEN}",       # 小写 scheme
        f"BEARER {TOKEN}",       # 大写 scheme
        f"Bearer  {TOKEN}  ",    # 多余空白
    ],
)
async def test_scheme_and_whitespace_are_tolerated(header):
    async with _make_client(authorization=header) as client:
        response = await client.post("/mcp")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "header",
    [
        TOKEN,                   # 缺 scheme
        f"Basic {TOKEN}",        # 错的 scheme
        "Bearer",                # 只有 scheme
        "Bearer ",               # 空令牌
        "Bearer " + "x" * 600,   # 超长
    ],
)
async def test_malformed_headers_are_rejected(header):
    async with _make_client(authorization=header) as client:
        response = await client.post("/mcp")
    assert response.status_code == 401


async def test_healthz_is_exempt():
    async with _make_client() as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


async def test_lifespan_scope_passes_through():
    """lifespan 必须透传，否则 Starlette 的 session manager 起不来。"""
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    middleware = BearerAuthMiddleware(app, token=TOKEN)
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type"]

    assert seen == ["lifespan"]


class TestAssertSafeBinding:
    """启动期 fail-safe 校验。"""

    def test_non_loopback_without_token_is_refused(self):
        with pytest.raises(ConfigError):
            assert_safe_binding("0.0.0.0", None, "streamable-http")

    def test_loopback_without_token_is_allowed(self):
        assert_safe_binding("127.0.0.1", None, "streamable-http")
        assert_safe_binding("::1", None, "streamable-http")
        assert_safe_binding("localhost", None, "streamable-http")

    def test_stdio_never_requires_token(self):
        assert_safe_binding("0.0.0.0", None, "stdio")

    def test_short_token_is_refused(self):
        with pytest.raises(ConfigError):
            assert_safe_binding("0.0.0.0", "too-short", "streamable-http")

    def test_long_enough_token_is_accepted(self):
        assert_safe_binding("0.0.0.0", "t" * 32, "streamable-http", ["mcp.internal"])

    def test_non_loopback_without_allowed_hosts_is_refused(self):
        """绑非回环地址时必须声明允许的 Host——否则 DNS rebinding 防护是关闭的。"""
        with pytest.raises(ConfigError):
            assert_safe_binding("0.0.0.0", "t" * 32, "streamable-http")
