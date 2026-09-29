"""工具返回值的结构契约。

所有工具都返回同一个信封：``success`` / ``errcode`` / ``error_msg``，失败时再由
各工具追加自己特有的字段。这个约定与参照项目一致，既有调用方可以平滑迁移。

用 ``TypedDict`` 而不是裸 ``dict`` 有两层好处：FastMCP 会据此生成 ``outputSchema``，
模型能提前知道返回结构；同时 FastMCP 仍然会把结果序列化成文本块，所以那些按
``content[0]["text"]`` 解析的老客户端也不会坏。
"""

from __future__ import annotations

from typing import Any, TypedDict


class BasicResult(TypedDict):
    """所有工具共有的字段。失败时 success 为 False，error_msg 给出原因。"""

    success: bool
    errcode: int | None
    error_msg: str | None


class TokenResult(BasicResult):
    access_token: str | None
    expires_in: int | None
    from_cache: bool


class DraftResult(BasicResult):
    draft_media_id: str | None
    image_media_id: str | None


class PublishResult(BasicResult):
    publish_id: str | None
    msg_data_id: str | None


class PublishStatusResult(BasicResult):
    publish_status: int | None
    publish_status_text: str | None
    article_id: str | None
    fail_idx: list[int] | None


class DraftSummary(TypedDict):
    media_id: str
    update_time: int
    title: str | None


class DraftListResult(BasicResult):
    total_count: int
    item_count: int
    drafts: list[DraftSummary]


class DraftCountResult(BasicResult):
    total_count: int


class MaterialSummary(TypedDict):
    media_id: str
    name: str
    update_time: int
    url: str


class MaterialListResult(BasicResult):
    total_count: int
    item_count: int
    materials: list[MaterialSummary]


class MaterialCountResult(BasicResult):
    image: int
    voice: int
    video: int
    news: int


class PublishedSummary(TypedDict):
    article_id: str
    update_time: int


class PublishedListResult(BasicResult):
    total_count: int
    item_count: int
    articles: list[PublishedSummary]


#: 发布状态的取值含义，来自微信开放文档。
PUBLISH_STATUS_TEXT: dict[int, str] = {
    0: "发布成功",
    1: "正在发布",
    2: "原创校验失败",
    3: "发布失败",
    4: "平台审核不通过",
    5: "发布成功后已被删除",
    6: "发布成功后已被封禁",
}


def describe_publish_status(code: Any) -> str | None:
    """把发布状态码翻译成中文描述。

    直接给模型一个人话描述，省得它去猜数字含义。
    """
    if code is None:
        return None
    try:
        return PUBLISH_STATUS_TEXT.get(int(code), f"未知状态（{code}）")
    except (TypeError, ValueError):
        return f"未知状态（{code}）"


__all__ = [
    "BasicResult",
    "TokenResult",
    "DraftResult",
    "PublishResult",
    "PublishStatusResult",
    "DraftSummary",
    "DraftListResult",
    "DraftCountResult",
    "MaterialSummary",
    "MaterialListResult",
    "MaterialCountResult",
    "PublishedSummary",
    "PublishedListResult",
    "PUBLISH_STATUS_TEXT",
    "describe_publish_status",
]
