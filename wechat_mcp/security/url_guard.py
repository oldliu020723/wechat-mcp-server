"""SSRF 防护：校验目标 URL，并确保它只指向公网可路由地址。

设计要点：

1. **先解析、再校验、最后用校验过的 IP 连接**。校验完再按域名重新解析一次是
   经典漏洞——攻击者可以在这两次解析之间用 DNS rebinding 把域名指向
   ``169.254.169.254``（云元数据）或任意内网地址。因此本模块返回的是**具体 IP**，
   由调用方直接拿它建连。
2. **全部记录都必须通过校验**。只要有一条 A/AAAA 记录落在非公网网段就整体拒绝，
   不允许"挑一条公网的来用"，否则攻击者可以构造混合应答绕过。
3. **内嵌 IPv4 的 IPv6 形式要归一化**。``::ffff:127.0.0.1``、6to4、Teredo 都能把
   内网 IPv4 藏进一个看起来人畜无害的 IPv6 地址里。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Final, Optional, Sequence
from urllib.parse import urlsplit

import anyio

from wechat_mcp.errors import SSRFBlocked, UpstreamError

# ---------------------------------------------------------------------------
# 网段黑名单
# ---------------------------------------------------------------------------

#: 禁止访问的 IPv4 网段。显式列举而不依赖 ``ipaddress`` 的 ``is_private``，
#: 是因为那个属性在不同 Python 版本对部分网段的判定并不一致。
_BLOCKED_V4: Final[tuple[ipaddress.IPv4Network, ...]] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",        # 本网络
        "10.0.0.0/8",       # 私有
        "100.64.0.0/10",    # 运营商级 NAT
        "127.0.0.0/8",      # 回环
        "169.254.0.0/16",   # 链路本地，含云元数据 169.254.169.254
        "172.16.0.0/12",    # 私有
        "192.0.0.0/24",     # IETF 协议分配
        "192.0.2.0/24",     # TEST-NET-1
        "192.88.99.0/24",   # 6to4 中继（已弃用）
        "192.168.0.0/16",   # 私有
        "198.18.0.0/15",    # 网络基准测试
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",   # TEST-NET-3
        "224.0.0.0/4",      # 组播
        "240.0.0.0/4",      # 保留，含 255.255.255.255
    )
)

#: 禁止访问的 IPv6 网段（不含 IPv4-mapped，后者由 :func:`_embedded_ipv4` 处理）。
_BLOCKED_V6: Final[tuple[ipaddress.IPv6Network, ...]] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "::/128",           # 未指定地址
        "::1/128",          # 回环
        "64:ff9b::/96",     # IPv4/IPv6 转换
        "100::/64",         # 丢弃前缀
        "2001:db8::/32",    # 文档用
        "fc00::/7",         # 唯一本地地址
        "fe80::/10",        # 链路本地
        "ff00::/8",         # 组播
    )
)

#: 允许的目标端口。限制在 Web 常用端口，避免把服务端当作任意端口的内网探针。
DEFAULT_ALLOWED_PORTS: Final[frozenset[int]] = frozenset({80, 443})

_SCHEME_DEFAULT_PORTS: Final[dict[str, int]] = {"http": 80, "https": 443}

#: URL 中不允许出现的控制字符（可能被用于绕过解析器）。
_FORBIDDEN_HOST_CHARS: Final[str] = " \t\r\n\x00"


# ---------------------------------------------------------------------------
# IP 判定
# ---------------------------------------------------------------------------


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    """提取 IPv6 地址中内嵌的 IPv4（若有）。

    覆盖 IPv4-mapped（``::ffff:a.b.c.d``）、6to4（``2002::/16``）和
    Teredo（``2001::/32``）三种形式。
    """
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr.sixtofour is not None:
        return addr.sixtofour
    teredo = addr.teredo
    if teredo is not None:
        # teredo 返回 (服务器 IPv4, 客户端 IPv4)
        return teredo[1]
    return None


def _is_public_v4(addr: ipaddress.IPv4Address) -> bool:
    return not any(addr in network for network in _BLOCKED_V4)


def is_public_ip(raw: str) -> bool:
    """判断给定字符串是否为公网可路由的 IP 地址。

    无法解析为 IP、或是任何内网/保留/特殊用途地址时返回 ``False``
    （fail-closed：判断不了就当它不安全）。
    """
    text = raw.strip()
    if not text:
        return False

    # getaddrinfo 对链路本地 IPv6 会带 %scope 后缀，需先剥离。
    if "%" in text:
        text = text.split("%", 1)[0]

    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(addr)
        if embedded is not None:
            return _is_public_v4(embedded)
        return not any(addr in network for network in _BLOCKED_V6)

    return _is_public_v4(addr)


# ---------------------------------------------------------------------------
# URL 解析与校验
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetURL:
    """已通过基础校验的目标 URL。

    ``host`` 保留调用方给的原始主机名（不是 IP），因为它要用于回填 ``Host``
    请求头和 TLS 的 SNI。
    """

    scheme: str
    host: str
    port: int
    path: str

    @property
    def default_port(self) -> int:
        return _SCHEME_DEFAULT_PORTS[self.scheme]


def parse_and_validate_url(
    raw_url: str,
    *,
    allowed_schemes: Sequence[str] = ("http", "https"),
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
) -> TargetURL:
    """解析并校验目标 URL 的 scheme、主机与端口。

    Raises:
        SSRFBlocked: URL 本身不合法，或指向了不被允许的目标形态。
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise SSRFBlocked("图片地址不能为空")

    candidate = raw_url.strip()

    # 控制字符可以骗过某些解析器，先于一切处理直接拒绝。
    if any(ch in candidate for ch in _FORBIDDEN_HOST_CHARS):
        raise SSRFBlocked("图片地址包含非法的空白或控制字符")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise SSRFBlocked(f"图片地址无法解析：{exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise SSRFBlocked(
            f"仅支持 {'/'.join(allowed_schemes)} 协议，收到的是 '{parts.scheme or '空'}'"
        )

    # urlsplit 会把 user:pass@host 里的凭证拆到 username/password，
    # 这类 URL 常用于混淆真实目标，一律拒绝。
    if parts.username is not None or parts.password is not None:
        raise SSRFBlocked("图片地址不允许携带用户名或密码")

    host = parts.hostname
    if not host:
        raise SSRFBlocked("图片地址缺少主机名")

    # 去掉 IPv6 字面量可能带的 %scope 后缀，避免后续解析歧义。
    if "%" in host:
        host = host.split("%", 1)[0]

    try:
        port = parts.port
    except ValueError as exc:
        raise SSRFBlocked(f"图片地址端口非法：{exc}") from exc

    if port is None:
        port = _SCHEME_DEFAULT_PORTS[scheme]

    if port not in allowed_ports:
        allowed = "、".join(str(p) for p in sorted(allowed_ports))
        raise SSRFBlocked(f"目标端口 {port} 不在允许范围内（允许：{allowed}）")

    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    return TargetURL(scheme=scheme, host=host, port=port, path=path)


# ---------------------------------------------------------------------------
# DNS 解析
# ---------------------------------------------------------------------------


#: 解析函数签名：(host, port) -> IP 字符串列表。
Resolver = Callable[[str, int], list[str]]


def _resolve_blocking(host: str, port: int) -> list[str]:
    """同步解析主机名，返回去重后的 IP 列表。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: dict[str, None] = {}
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0]:
            seen[str(sockaddr[0])] = None
    return list(seen)


async def resolve_public_addresses(
    host: str,
    port: int,
    *,
    resolver: Optional[Resolver] = None,
) -> list[str]:
    """把主机名解析为 IP 列表，并确保**每一个**结果都是公网地址。

    Args:
        host: 主机名或 IP 字面量。
        port: 端口，参与 getaddrinfo 的 service 解析。
        resolver: 解析函数，默认走真实 DNS。测试可注入假实现，
            从而不必碰网络（也避免被全局断网夹具挡住）。

    Returns:
        已校验的公网 IP 列表，保持解析顺序。

    Raises:
        SSRFBlocked: 存在任何一条非公网记录，或主机名本身是内网 IP。
        UpstreamError: DNS 解析失败（fail-closed，同样拒绝继续）。
    """
    # IP 字面量不必走 DNS。
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public_ip(host):
            raise SSRFBlocked(f"目标地址 {host} 属于内网或保留网段，已拒绝访问")
        return [host]

    resolve = resolver or _resolve_blocking
    try:
        addresses = await anyio.to_thread.run_sync(resolve, host, port)
    except socket.gaierror as exc:
        raise UpstreamError(f"无法解析图片地址的主机名 {host}：{exc}") from exc
    except OSError as exc:
        raise UpstreamError(f"解析主机名 {host} 时出错：{exc}") from exc

    if not addresses:
        raise UpstreamError(f"主机名 {host} 没有解析到任何地址")

    # 全部记录都要过检，不能只挑其中一条公网的用。
    blocked = [addr for addr in addresses if not is_public_ip(addr)]
    if blocked:
        raise SSRFBlocked(
            f"主机名 {host} 解析到内网或保留地址 {blocked}，已拒绝访问"
        )

    return addresses


def build_pinned_url(target: TargetURL, ip: str) -> str:
    """构造把主机名替换为已验证 IP 的 URL。

    这样 httpcore 建连时不会再做第二次 DNS 解析，rebinding 无从下手；
    调用方需要另行设置 ``Host`` 头与 ``sni_hostname`` 以维持 TLS 语义。
    """
    host_part = f"[{ip}]" if ":" in ip else ip
    if target.port == target.default_port:
        return f"{target.scheme}://{host_part}{target.path}"
    return f"{target.scheme}://{host_part}:{target.port}{target.path}"


__all__ = [
    "TargetURL",
    "Resolver",
    "DEFAULT_ALLOWED_PORTS",
    "is_public_ip",
    "parse_and_validate_url",
    "resolve_public_addresses",
    "build_pinned_url",
]
