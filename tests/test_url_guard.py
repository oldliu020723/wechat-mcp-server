"""url_guard 的单元测试——SSRF 判定的安全核心，覆盖最密。"""

from __future__ import annotations

import socket

import pytest

from wechat_mcp.errors import SSRFBlocked, UpstreamError
from wechat_mcp.security import url_guard


@pytest.mark.parametrize(
    "ip",
    [
        # IPv4：私有网段
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.255.254",
        # IPv4：回环与链路本地（169.254.169.254 是云元数据端点）
        "127.0.0.1",
        "127.1.2.3",
        "169.254.169.254",
        # IPv4：保留与特殊用途
        "0.0.0.0",
        "100.64.0.1",
        "192.0.0.1",
        "192.0.2.1",
        "192.88.99.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        # IPv4：组播与保留段
        "224.0.0.1",
        "239.255.255.255",
        "240.0.0.1",
        "255.255.255.255",
        # IPv6：回环、ULA、链路本地、组播、文档段
        "::",
        "::1",
        "fc00::1",
        "fdff::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "100::1",
        "64:ff9b::1",
        # 把内网 IPv4 藏进 IPv6 的绕过形式
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        "2002:7f00:0001::1",  # 6to4 里包着 127.0.0.1
        # 根本不是 IP 的输入（fail-closed）
        "",
        "   ",
        "not-an-ip",
        "999.999.999.999",
        "1.2.3",
        "0x7f.1",
    ],
)
def test_non_public_addresses_are_rejected(ip):
    assert url_guard.is_public_ip(ip) is False


@pytest.mark.parametrize(
    "ip",
    [
        "1.1.1.1",
        "8.8.8.8",
        "93.184.216.34",
        "223.5.5.5",
        "2606:4700:4700::1111",
        "2400:3200::1",
        # 带链路本地 scope 后缀的写法要先剥离再判断
        "8.8.8.8%eth0",
    ],
)
def test_public_addresses_are_accepted(ip):
    assert url_guard.is_public_ip(ip) is True


class TestParseAndValidateUrl:
    """URL 形态校验。"""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/a.png",
            "gopher://example.com/",
            "javascript:alert(1)",
            "data:image/png;base64,AAAA",
        ],
    )
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(SSRFBlocked):
            url_guard.parse_and_validate_url(url)

    def test_rejects_userinfo(self):
        with pytest.raises(SSRFBlocked):
            url_guard.parse_and_validate_url("https://user:pass@example.com/a.png")

    def test_rejects_disallowed_port(self):
        with pytest.raises(SSRFBlocked):
            url_guard.parse_and_validate_url("https://example.com:8080/a.png")

    @pytest.mark.parametrize("url", ["https://example.com/a\n.png", "https://exa mple.com/a.png"])
    def test_rejects_control_characters(self, url):
        with pytest.raises(SSRFBlocked):
            url_guard.parse_and_validate_url(url)

    def test_rejects_empty(self):
        with pytest.raises(SSRFBlocked):
            url_guard.parse_and_validate_url("   ")

    def test_accepts_https_default_port(self):
        target = url_guard.parse_and_validate_url("https://example.com/a.png")
        assert (target.scheme, target.host, target.port) == ("https", "example.com", 443)

    def test_keeps_query_string(self):
        target = url_guard.parse_and_validate_url("https://example.com/a.png?v=2")
        assert target.path == "/a.png?v=2"

    def test_defaults_path_when_absent(self):
        target = url_guard.parse_and_validate_url("https://example.com")
        assert target.path == "/"


class TestResolvePublicAddresses:
    """DNS 解析与整体校验。"""

    async def test_mixed_records_are_rejected_entirely(self):
        """混合记录必须整体拒绝：不能挑那条公网的来用。"""

        def resolver(host, port):
            return ["93.184.216.34", "127.0.0.1"]

        with pytest.raises(SSRFBlocked):
            await url_guard.resolve_public_addresses("evil.test", 443, resolver=resolver)

    async def test_all_public_records_pass(self):
        def resolver(host, port):
            return ["93.184.216.34", "1.1.1.1"]

        addresses = await url_guard.resolve_public_addresses(
            "cdn.test", 443, resolver=resolver
        )
        assert addresses == ["93.184.216.34", "1.1.1.1"]

    async def test_private_ip_literal_is_rejected_without_dns(self):
        def resolver(host, port):  # pragma: no cover - 不应被调用
            raise AssertionError("IP 字面量不应该触发 DNS 查询")

        with pytest.raises(SSRFBlocked):
            await url_guard.resolve_public_addresses(
                "169.254.169.254", 80, resolver=resolver
            )

    async def test_public_ip_literal_skips_dns(self):
        def resolver(host, port):  # pragma: no cover - 不应被调用
            raise AssertionError("IP 字面量不应该触发 DNS 查询")

        addresses = await url_guard.resolve_public_addresses(
            "93.184.216.34", 443, resolver=resolver
        )
        assert addresses == ["93.184.216.34"]

    async def test_dns_failure_is_fail_closed(self):
        def resolver(host, port):
            raise socket.gaierror("name not known")

        with pytest.raises(UpstreamError):
            await url_guard.resolve_public_addresses("nope.test", 443, resolver=resolver)

    async def test_empty_resolution_is_rejected(self):
        def resolver(host, port):
            return []

        with pytest.raises(UpstreamError):
            await url_guard.resolve_public_addresses("empty.test", 443, resolver=resolver)


class TestBuildPinnedUrl:
    """把 URL 改写成"直连已验证 IP"的形式。"""

    def test_uses_ip_and_keeps_path_and_query(self):
        target = url_guard.parse_and_validate_url("https://example.com/a/b.png?x=1")
        assert (
            url_guard.build_pinned_url(target, "93.184.216.34")
            == "https://93.184.216.34/a/b.png?x=1"
        )

    def test_brackets_ipv6_literals(self):
        target = url_guard.parse_and_validate_url("https://example.com/a.png")
        assert (
            url_guard.build_pinned_url(target, "2606:4700::1111")
            == "https://[2606:4700::1111]/a.png"
        )

    def test_omits_default_port(self):
        target = url_guard.parse_and_validate_url("https://example.com/a.png")
        assert ":443" not in url_guard.build_pinned_url(target, "1.1.1.1")
