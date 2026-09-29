"""受保护的图片抓取：只允许公网目标，且校验与连接使用同一个 IP。

这里的防护分三层，缺一不可：

1. **解析即校验**（:mod:`wechat_mcp.security.url_guard`）——目标必须是公网地址；
2. **连接固定到校验过的 IP**——避免校验完再按域名重解析，那中间的窗口就是
   DNS rebinding 的入口；TLS 的 SNI 与证书校验仍按原域名进行；
3. **读 body 之前复核实际对端 IP**——万一前两层有疏漏，这一层兜底。

另外全程 ``trust_env=False``。否则 ``HTTP_PROXY`` 之类的环境变量会把请求交给
代理，代理自己去解析域名，上面三层全部形同虚设。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Final, Mapping, Optional

import httpx

from wechat_mcp.errors import ImageRejected, SSRFBlocked, UpstreamError
from wechat_mcp.security import url_guard

logger = logging.getLogger(__name__)

#: 传输层工厂。默认产出真实的 HTTP 传输；测试注入 ``httpx.MockTransport``
#: 以在不碰网络的前提下验证请求改写是否正确。
TransportFactory = Callable[[], httpx.AsyncBaseTransport]

#: 连接类失败对外统一使用的消息。
#:
#: 具体原因（DNS 失败、连接被拒、超时、TLS 握手失败……）只写进日志。把这些差异
#: 回传给调用方会构成一个探针：攻击者据此就能判断目标端口是否开放。
FETCH_FAILED_MESSAGE: Final[str] = "封面图下载失败：无法获取该地址的图片"

#: 单次读取的块大小。
_CHUNK_SIZE: Final[int] = 64 * 1024

#: 用于魔数嗅探的头部字节数。
_SNIFF_LENGTH: Final[int] = 16


@dataclass(frozen=True)
class FetchedBlob:
    """下载完成并已校验的图片数据。"""

    data: bytes
    content_type: str
    extension: str
    size: int
    peer_ip: str


def normalize_content_type(raw: Optional[str]) -> str:
    """把 ``image/png; charset=binary`` 这类取值归一为 ``image/png``。"""
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


def sniff_image_type(head: bytes) -> Optional[str]:
    """按文件头魔数判断图片类型。

    比信任 ``Content-Type`` 响应头可靠——响应头完全由目标服务器控制，而魔数必须
    与真实字节一致，否则图片在微信侧也用不了。

    Returns:
        归一后的 MIME 类型，无法识别时返回 ``None``。
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"BM"):
        return "image/bmp"
    return None


def _verify_peer(response: httpx.Response, expected_ip: str) -> str:
    """复核连接实际落在哪个 IP 上，返回该 IP。

    依托 httpcore 放进响应 extensions 里的 ``network_stream``。若该扩展不可用
    （例如换了传输实现），退回到"期望 IP"，不让防护静默失效。
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        logger.debug("响应未提供 network_stream 扩展，跳过对端 IP 复核")
        return expected_ip

    try:
        server_addr = stream.get_extra_info("server_addr")
    except Exception:  # pragma: no cover - 后端实现差异，不应影响主流程
        logger.debug("读取 server_addr 失败，跳过对端 IP 复核", exc_info=True)
        return expected_ip

    if not server_addr:
        return expected_ip

    peer_ip = str(server_addr[0])
    if not url_guard.is_public_ip(peer_ip):
        raise SSRFBlocked(f"连接落在了非公网地址 {peer_ip} 上，已中止")
    return peer_ip


def _host_header(target: url_guard.TargetURL) -> str:
    """还原 Host 头：URL 被改写成 IP 之后，必须手工把主机名写回去。"""
    if target.port == target.default_port:
        return target.host
    return f"{target.host}:{target.port}"


async def _fetch_from_ip(
    target: url_guard.TargetURL,
    ip: str,
    *,
    allowed_types: Mapping[str, str],
    max_bytes: int,
    timeout: float,
    transport_factory: TransportFactory,
) -> FetchedBlob:
    """从指定的、已校验的 IP 抓取图片。"""
    pinned_url = url_guard.build_pinned_url(target, ip)

    # 每个 IP 单独建一个 client：连接池不跨 IP 复用，对端复核才有意义。
    transport = transport_factory()
    async with httpx.AsyncClient(
        transport=transport,
        trust_env=False,          # 关键：否则代理会绕过全部 IP 校验
        follow_redirects=False,   # 关键：重定向目标不会经过校验
        timeout=httpx.Timeout(timeout),
        headers={"Host": _host_header(target)},
    ) as client:
        request = client.build_request(
            "GET",
            pinned_url,
            # 让 TLS 的 SNI 与证书校验仍按原域名进行，而不是 IP。
            extensions={"sni_hostname": target.host},
        )
        response = await client.send(request, stream=True)
        # 注意：httpx 的 Response 并没有实现异步上下文管理器协议（只有 aclose），
        # 所以这里显式 try/finally 保证连接一定被释放。
        try:
            if response.status_code != 200:
                raise UpstreamError(f"目标返回 HTTP {response.status_code}")

            peer_ip = _verify_peer(response, ip)

            declared_length = response.headers.get("content-length")
            if (
                declared_length
                and declared_length.isdigit()
                and int(declared_length) > max_bytes
            ):
                raise ImageRejected(
                    f"图片体积 {declared_length} 字节，超过上限 {max_bytes} 字节"
                )

            declared_type = normalize_content_type(response.headers.get("content-type"))

            buffer = bytearray()
            head = b""
            async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    raise ImageRejected(f"图片体积超过上限 {max_bytes} 字节")
                if len(head) < _SNIFF_LENGTH:
                    head = bytes(buffer[:_SNIFF_LENGTH])
        finally:
            await response.aclose()

    if not buffer:
        raise ImageRejected("目标返回了空响应体")

    # 以文件头魔数为唯一权威：它必须与真实字节一致，而响应头完全由目标服务器
    # 控制。识别不出来就直接拒绝——"声明是图片、内容却不是"正是要挡的情形。
    #
    # 注意不要在这里回退到响应头声明的类型：那等于让撒谎的服务器通过校验。
    # 只按魔数判断同样能兼容返回 application/octet-stream 的图床，因为那些
    # 响应的字节本身仍然是合法图片。
    image_type = sniff_image_type(head)
    if image_type is None or image_type not in allowed_types:
        raise ImageRejected(
            f"响应内容不是受支持的图片格式（响应头声明为 {declared_type or '空'}）"
        )

    return FetchedBlob(
        data=bytes(buffer),
        content_type=image_type,
        extension=allowed_types[image_type],
        size=len(buffer),
        peer_ip=peer_ip,
    )


async def fetch_image(
    url: str,
    *,
    allowed_types: Mapping[str, str],
    max_bytes: int,
    timeout: float,
    resolver: Optional[url_guard.Resolver] = None,
    transport_factory: Optional[TransportFactory] = None,
) -> FetchedBlob:
    """抓取并校验一张图片。

    依次尝试该主机名解析出的每个公网 IP，全部失败才放弃。

    Args:
        url: 调用方给出的图片地址。
        allowed_types: 允许的 MIME 类型到文件扩展名的映射。
        max_bytes: 体积上限。
        timeout: 单次请求超时（秒）。
        resolver: DNS 解析函数，仅供测试注入。
        transport_factory: 传输层工厂，仅供测试注入。

    Raises:
        ImageRejected: 内容不是受支持的图片，或超出体积上限。
        UpstreamError: 所有候选 IP 都无法取回内容（对外消息统一）。
    """
    target = url_guard.parse_and_validate_url(url)
    addresses = await url_guard.resolve_public_addresses(
        target.host, target.port, resolver=resolver
    )
    factory = transport_factory or (lambda: httpx.AsyncHTTPTransport(http2=False, retries=0))

    last_reason = ""
    for ip in addresses:
        try:
            blob = await _fetch_from_ip(
                target,
                ip,
                allowed_types=allowed_types,
                max_bytes=max_bytes,
                timeout=timeout,
                transport_factory=factory,
            )
        except ImageRejected:
            # 内容本身不合规，换 IP 也不会变好，直接上抛。
            raise
        except SSRFBlocked:
            raise
        except Exception as exc:
            # 细节只进日志：对外只暴露统一消息，不给连通性探针留口子。
            last_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("从 %s 抓取失败：%s", ip, exc)
            continue

        logger.info(
            "已抓取图片 %d 字节，类型 %s，来源主机 %s",
            blob.size,
            blob.content_type,
            target.host,
        )
        return blob

    logger.error("所有候选地址均抓取失败，最后原因：%s", last_reason)
    raise UpstreamError(FETCH_FAILED_MESSAGE)


__all__ = [
    "FetchedBlob",
    "TransportFactory",
    "FETCH_FAILED_MESSAGE",
    "fetch_image",
    "normalize_content_type",
    "sniff_image_type",
]
