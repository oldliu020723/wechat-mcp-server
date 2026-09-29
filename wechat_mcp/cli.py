"""命令行入口。

``run.py``（源码直跑）与打包后的 ``wechat-mcp-server`` 命令都走这里，
保证两种用法的行为和参数完全一致。
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from wechat_mcp.errors import ConfigError
from wechat_mcp.settings import (
    DEFAULT_HOST,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_PORT,
    DEFAULT_REQUESTS_PER_MINUTE,
    ENV_AUTH_TOKEN,
    settings_from_env,
)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="wechat-mcp-server",
        description="微信公众号 MCP 服务（支持 stdio 与 streamable-http 两种传输）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="传输方式。stdio 由 MCP 客户端直接拉起进程，不暴露网络端口",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP 监听端口")
    parser.add_argument(
        "--auth-token",
        default=None,
        help=f"HTTP 传输的 Bearer 令牌，缺省读取环境变量 {ENV_AUTH_TOKEN}",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        dest="allowed_hosts",
        metavar="HOST",
        help=(
            "允许的 Host 头，可重复指定。绑定非回环地址时必填——"
            "mcp SDK 只在回环地址下才会自动开启 DNS rebinding 防护"
        ),
    )
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_REQUESTS_PER_MINUTE,
        help="每个凭证每分钟允许的请求数，-1 表示不限制",
    )
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=DEFAULT_MAX_IMAGE_BYTES,
        help="允许下载的封面图最大字节数",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT,
        help="访问微信接口与下载图片的超时时间（秒）",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """解析参数并启动服务，返回进程退出码。"""
    args = build_parser().parse_args(argv)

    settings = settings_from_env(
        transport=args.transport,
        host=args.host,
        port=args.port,
        auth_token=args.auth_token,
        allowed_hosts=args.allowed_hosts,
        requests_per_minute=args.requests_per_minute,
        max_image_bytes=args.max_image_bytes,
        http_timeout=args.http_timeout,
        log_level=args.log_level,
    )

    # 延迟导入：让 --help 与配置错误提示不必等第三方依赖加载。
    from wechat_mcp.server.app import run_server

    try:
        run_server(settings)
    except ConfigError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        return 130
    return 0


__all__ = ["build_parser", "main"]
