"""工具 schema 与注解的回归测试。

参照项目把每个工具都写成 ``def tool(args)``，客户端拿到的 JSON Schema 退化成
一个无类型的 ``args`` 对象——模型既不知道有哪些参数，也不知道类型与约束。
这里逐条把"schema 必须像样"钉死，防止改回去。
"""

from __future__ import annotations

import pytest

from wechat_mcp.server.app import build_server
from wechat_mcp.settings import settings_from_env

EXPECTED_TOOLS = {
    "get_access_token",
    "create_wechat_draft",
    "publish_wechat_draft",
    "del_wechat_draft",
    "del_wechat_material",
    "get_publish_status",
    "list_drafts",
    "count_drafts",
    "list_materials",
    "count_materials",
    "list_published",
}

#: 会产生不可撤销后果的工具。
DESTRUCTIVE_TOOLS = {"publish_wechat_draft", "del_wechat_draft", "del_wechat_material"}

#: 纯查询、无副作用的工具。
READ_ONLY_TOOLS = {
    "get_publish_status",
    "list_drafts",
    "count_drafts",
    "list_materials",
    "count_materials",
    "list_published",
}


@pytest.fixture
async def tools():
    mcp, context = build_server(settings_from_env())
    try:
        return {tool.name: tool for tool in await mcp.list_tools()}
    finally:
        await context.aclose()


async def test_all_expected_tools_are_registered(tools):
    assert set(tools) == EXPECTED_TOOLS


async def test_no_tool_exposes_an_untyped_args_object(tools):
    """核心回归：不能再出现退化的 args 参数。"""
    for name, tool in tools.items():
        properties = tool.inputSchema.get("properties", {})
        assert "args" not in properties, f"{name} 暴露了无类型的 args 参数"
        assert properties, f"{name} 没有任何参数，schema 异常"


async def test_every_property_is_documented(tools):
    """每个参数都要有 description，模型才知道该怎么填。"""
    for name, tool in tools.items():
        for prop, spec in tool.inputSchema.get("properties", {}).items():
            assert spec.get("description"), f"{name}.{prop} 缺少 description"


async def test_create_draft_schema_constraints(tools):
    schema = tools["create_wechat_draft"].inputSchema
    properties = schema["properties"]

    assert properties["image_url"]["type"] == "string"
    assert properties["title"]["maxLength"] == 64
    assert properties["content"]["type"] == "string"
    assert properties["need_open_comment"]["type"] == "integer"
    assert properties["need_open_comment"]["maximum"] == 1
    assert properties["need_open_comment"]["minimum"] == 0

    # 只有这三个是必填。
    assert set(schema["required"]) == {"image_url", "title", "content"}


async def test_access_token_is_always_optional(tools):
    """access_token 应当处处可省略——服务端会复用缓存的 token。"""
    for name, tool in tools.items():
        properties = tool.inputSchema.get("properties", {})
        if "access_token" in properties:
            assert "access_token" not in tool.inputSchema.get("required", []), (
                f"{name} 把 access_token 标成了必填"
            )


async def test_list_tools_expose_count_bounds(tools):
    """count 必须带 1-20 的范围约束（微信侧硬限制）。"""
    for name in ("list_drafts", "list_materials", "list_published"):
        spec = tools[name].inputSchema["properties"]["count"]
        assert spec["type"] == "integer"
        assert spec["minimum"] == 1
        assert spec["maximum"] == 20


async def test_material_type_is_an_enum(tools):
    spec = tools["list_materials"].inputSchema["properties"]["material_type"]

    assert spec.get("enum") == ["image", "video", "voice", "news"]


async def test_read_only_annotations(tools):
    for name in READ_ONLY_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} 没有注解"
        assert annotations.readOnlyHint is True, f"{name} 应标记为只读"


async def test_destructive_annotations(tools):
    for name in DESTRUCTIVE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} 没有注解"
        assert annotations.destructiveHint is True, f"{name} 应标记为破坏性"


async def test_get_publish_status_is_not_marked_idempotent(tools):
    """发布状态会随时间变化，同参数不同时刻结果不同。"""
    annotations = tools["get_publish_status"].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is True
    assert annotations.idempotentHint is False


async def test_tools_have_descriptions(tools):
    for name, tool in tools.items():
        assert tool.description, f"{name} 缺少描述"
        assert len(tool.description) > 10, f"{name} 的描述过于简略"


async def test_draft_tools_describe_the_no_publish_semantics(tools):
    """create 工具必须在描述里说清它不会发布，避免模型误判。"""
    description = tools["create_wechat_draft"].description or ""
    assert "不会发布" in description or "只创建草稿" in description
