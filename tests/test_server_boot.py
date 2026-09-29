"""服务装配与启动校验的测试。"""

from __future__ import annotations

import pytest

from wechat_mcp.errors import ConfigError
from wechat_mcp.server.app import (
    SERVER_NAME,
    build_server,
    build_transport_security,
    run_server,
)
from wechat_mcp.settings import settings_from_env


def test_loopback_keeps_sdk_defaults():
    """回环绑定时沿用 SDK 自带的默认防护，我们不必额外配置。"""
    settings = settings_from_env(host="127.0.0.1")

    assert build_transport_security(settings) is None


@pytest.mark.parametrize("host", ["localhost", "::1"])
def test_other_loopback_forms_also_keep_defaults(host):
    assert build_transport_security(settings_from_env(host=host)) is None


def test_non_loopback_gets_explicit_dns_rebinding_protection():
    """实测确认 SDK 在非回环绑定下不会自动开启防护，必须由我们补上。"""
    settings = settings_from_env(host="0.0.0.0", allowed_hosts=("mcp.internal",))

    transport_security = build_transport_security(settings)

    assert transport_security is not None
    assert transport_security.enable_dns_rebinding_protection is True
    assert "mcp.internal" in transport_security.allowed_hosts
    # 带端口与不带端口的 Host 都要放行。
    assert "mcp.internal:*" in transport_security.allowed_hosts


async def test_build_server_exposes_tools():
    mcp, context = build_server(settings_from_env())
    try:
        assert mcp.name == SERVER_NAME
        tools = await mcp.list_tools()
        assert len(tools) == 11
    finally:
        await context.aclose()


def test_run_server_refuses_unsafe_binding_without_token():
    settings = settings_from_env(transport="streamable-http", host="0.0.0.0")

    with pytest.raises(ConfigError):
        run_server(settings)


def test_run_server_refuses_non_loopback_without_allowed_hosts():
    settings = settings_from_env(
        transport="streamable-http", host="0.0.0.0", auth_token="t" * 40
    )

    with pytest.raises(ConfigError):
        run_server(settings)


def test_stdio_does_not_require_token():
    """stdio 没有网络面，绑定地址与认证都无关。"""
    from wechat_mcp.security.auth import assert_safe_binding

    assert_safe_binding("0.0.0.0", None, "stdio")
