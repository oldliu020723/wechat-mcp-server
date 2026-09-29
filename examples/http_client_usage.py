#!/usr/bin/env python3
"""通过 streamable-http 传输调用本服务的示例。

先启动服务（另开一个终端）::

    export WECHAT_MCP_AUTH_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
    python run.py --transport streamable-http --host 127.0.0.1 --port 8000

再运行本示例::

    export WECHAT_MCP_AUTH_TOKEN=<上面那个令牌>
    python examples/http_client_usage.py

如果想让它顺带演示真实调用，再额外设置 WECHAT_APPID 与 WECHAT_APPSECRET
（建议用测试号）。未设置时只演示"列出工具"这类不需要凭证的部分。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def _auth_headers() -> dict[str, str]:
    token = os.getenv("WECHAT_MCP_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _unwrap(result) -> dict:
    """把工具的返回内容解析成字典。

    服务端返回的是文本 JSON；同时 FastMCP 也会填充 structuredContent，
    这里优先用后者，取不到再退回文本解析。
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


async def main() -> int:
    url = os.getenv("WECHAT_MCP_URL", DEFAULT_URL)

    async with streamablehttp_client(url, headers=_auth_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"已连接 {url}")
            print(f"服务提供 {len(tools.tools)} 个工具：")
            for tool in tools.tools:
                annotations = tool.annotations
                tag = "只读" if annotations and annotations.readOnlyHint else "写入"
                print(f"  [{tag}] {tool.name}")

            appid = os.getenv("WECHAT_APPID")
            appsecret = os.getenv("WECHAT_APPSECRET")
            if not (appid and appsecret):
                print(
                    "\n未设置 WECHAT_APPID / WECHAT_APPSECRET，"
                    "跳过真实调用演示。"
                )
                return 0

            print("\n获取 access_token ...")
            token_result = _unwrap(
                await session.call_tool(
                    "get_access_token", {"appid": appid, "appsecret": appsecret}
                )
            )
            if not token_result.get("success"):
                print("失败：", token_result.get("error_msg"))
                return 1
            print("成功（是否来自缓存：%s）" % token_result.get("from_cache"))

            # 注意这里不再传 access_token——服务端会自动复用缓存里的凭证。
            print("\n查询草稿总数 ...")
            count_result = _unwrap(await session.call_tool("count_drafts", {}))
            if not count_result.get("success"):
                print("失败：", count_result.get("error_msg"))
                return 1
            print("草稿总数：", count_result.get("total_count"))

            print("\n列出最近 5 篇草稿 ...")
            drafts_result = _unwrap(
                await session.call_tool("list_drafts", {"count": 5})
            )
            for draft in drafts_result.get("drafts", []):
                print("  -", draft.get("media_id"), draft.get("title") or "(未取标题)")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
