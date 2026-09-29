"""把全部工具注册到 FastMCP。

业务逻辑在各自的模块里（可以脱离 MCP 单独测试），这里只负责把参数签名写清楚。

**为什么每个工具都手写完整签名**：参照项目把签名写成 ``def tool(args)``，
客户端拿到的 JSON Schema 退化成一个无类型的 ``args`` 对象——模型既不知道有哪些
参数，也不知道类型和约束，只能靠猜。显式注解之后，参数名、类型、取值范围、
必填与否都会出现在 schema 里，模型调用前就能自我纠错。

另外每个工具都标了 :class:`~mcp.types.ToolAnnotations`，让客户端知道它会不会
产生副作用——这是模型判断"能不能自动调用"的关键信号。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from wechat_mcp.tools import drafts, materials, publishing, tokens
from wechat_mcp.tools.context import AppContext
from wechat_mcp.wechat import endpoints

# ---------------------------------------------------------------------------
# 工具注解
# ---------------------------------------------------------------------------

#: 只读工具：不改变任何状态，模型可以放心自动调用。
ANNOT_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

#: 新增类工具：会创建资源，但不会破坏已有内容。
ANNOT_ADDITIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

#: 破坏性工具：删除，或对外发文。调用前应当获得用户确认。
ANNOT_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

# ---------------------------------------------------------------------------
# 复用的参数注解
# ---------------------------------------------------------------------------

AccessTokenParam = Annotated[
    Optional[str],
    Field(
        max_length=512,
        description=(
            "接口调用凭证。可省略——省略时自动使用进程内缓存的 token，"
            "（前提是此前调用过 get_access_token，或设置了 WECHAT_APPID 环境变量）"
        ),
    ),
]

OffsetParam = Annotated[
    int,
    Field(ge=0, description="分页偏移，0 表示从第一条开始"),
]

CountParam = Annotated[
    int,
    Field(
        ge=1,
        le=endpoints.MAX_LIST_COUNT,
        description=f"本次返回的条数，微信限制为 1-{endpoints.MAX_LIST_COUNT}",
    ),
]

NoContentParam = Annotated[
    bool,
    Field(
        description="为 true 时不返回正文 HTML。响应体积小很多，除非确实需要正文，否则保持 true",
    ),
]


def register_all(mcp: FastMCP, ctx: AppContext) -> None:
    """把所有工具注册到给定的 FastMCP 实例。

    Args:
        mcp: 待注册的服务实例。
        ctx: 依赖容器，通过闭包注入到各工具里。
    """

    # -- 凭证 ---------------------------------------------------------------

    @mcp.tool(name="get_access_token", title="获取微信 access_token", annotations=ANNOT_ADDITIVE)
    async def get_access_token(
        appid: Annotated[
            Optional[str],
            Field(
                max_length=64,
                repr=False,
                description="公众号 AppID。省略时读取环境变量 WECHAT_APPID",
            ),
        ] = None,
        appsecret: Annotated[
            Optional[str],
            Field(
                max_length=128,
                repr=False,
                description="公众号 AppSecret。省略时读取环境变量 WECHAT_APPSECRET。不会写入日志或错误信息",
            ),
        ] = None,
        force_refresh: Annotated[
            bool,
            Field(description="为 true 时忽略缓存，强制重新签发 token"),
        ] = False,
    ) -> Any:
        """获取 access_token（有效期约 2 小时）。

        token 会在服务进程内缓存，并在过期前 5 分钟提前失效。其余工具都可以
        省略 access_token 参数，自动复用这里缓存的值。
        """
        return await tokens.fetch_access_token(
            ctx, appid=appid, appsecret=appsecret, force_refresh=force_refresh
        )

    # -- 草稿 ---------------------------------------------------------------

    @mcp.tool(
        name="create_wechat_draft",
        title="创建微信草稿",
        annotations=ANNOT_ADDITIVE,
    )
    async def create_wechat_draft(
        image_url: Annotated[
            str,
            Field(
                min_length=8,
                max_length=2048,
                description=(
                    "封面图的公网地址。必须是 jpeg/png/gif/bmp，且不超过 10MB；"
                    "目标须解析到公网 IP——内网、回环、链路本地与云元数据地址一律拒绝"
                ),
            ),
        ],
        title: Annotated[
            str,
            Field(min_length=1, max_length=64, description="文章标题，最多 64 字"),
        ],
        content: Annotated[
            str,
            Field(min_length=1, description="文章正文，支持 HTML 富文本"),
        ],
        access_token: AccessTokenParam = None,
        author: Annotated[
            str, Field(max_length=8, description="作者名，最多 8 字")
        ] = "",
        digest: Annotated[
            str,
            Field(max_length=120, description="摘要，最多 120 字；留空时微信自动截取"),
        ] = "",
        source_url: Annotated[
            str, Field(max_length=2048, description="“阅读原文”链接")
        ] = "",
        need_open_comment: Annotated[
            int, Field(ge=0, le=1, description="1 表示开启评论，0 表示关闭")
        ] = 0,
        only_fans_can_comment: Annotated[
            int, Field(ge=0, le=1, description="1 表示仅关注者可评论")
        ] = 0,
    ) -> Any:
        """下载封面图 → 上传为永久素材 → 创建一篇草稿。

        **本工具只创建草稿，不会发布。** 发布需要另外调用 publish_wechat_draft。
        返回的 draft_media_id 用于后续发布或删除，image_media_id 是封面素材的 ID。
        """
        return await drafts.create_draft(
            ctx,
            image_url=image_url,
            title=title,
            content=content,
            access_token=access_token,
            author=author,
            digest=digest,
            source_url=source_url,
            need_open_comment=need_open_comment,
            only_fans_can_comment=only_fans_can_comment,
        )

    @mcp.tool(name="del_wechat_draft", title="删除微信草稿", annotations=ANNOT_DESTRUCTIVE)
    async def del_wechat_draft(
        media_id: Annotated[
            str, Field(min_length=1, max_length=256, description="要删除的草稿 media_id")
        ],
        access_token: AccessTokenParam = None,
    ) -> Any:
        """删除指定草稿。删除后不可恢复。"""
        return await drafts.delete_draft(ctx, media_id=media_id, access_token=access_token)

    @mcp.tool(name="list_drafts", title="获取草稿列表", annotations=ANNOT_READ_ONLY)
    async def list_drafts(
        offset: OffsetParam = 0,
        count: CountParam = endpoints.MAX_LIST_COUNT,
        no_content: NoContentParam = True,
        access_token: AccessTokenParam = None,
    ) -> Any:
        """分页获取草稿列表，返回每篇的 media_id 与更新时间。

        注意：no_content 为 true（默认）时微信不返回标题，title 字段会是 null。
        需要标题时把 no_content 设为 false，代价是响应体明显变大。
        """
        return await drafts.list_drafts(
            ctx,
            offset=offset,
            count=count,
            no_content=no_content,
            access_token=access_token,
        )

    @mcp.tool(name="count_drafts", title="获取草稿总数", annotations=ANNOT_READ_ONLY)
    async def count_drafts(access_token: AccessTokenParam = None) -> Any:
        """获取草稿箱里的草稿总数。"""
        return await drafts.count_drafts(ctx, access_token=access_token)

    # -- 素材 ---------------------------------------------------------------

    @mcp.tool(
        name="del_wechat_material",
        title="删除微信素材",
        annotations=ANNOT_DESTRUCTIVE,
    )
    async def del_wechat_material(
        media_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=256,
                description="要删除的永久素材 media_id（例如封面图）",
            ),
        ],
        access_token: AccessTokenParam = None,
    ) -> Any:
        """删除指定的永久素材。删除后不可恢复。"""
        return await materials.delete_material(
            ctx, media_id=media_id, access_token=access_token
        )

    @mcp.tool(name="list_materials", title="获取素材列表", annotations=ANNOT_READ_ONLY)
    async def list_materials(
        material_type: Annotated[
            Literal["image", "video", "voice", "news"],
            Field(description="素材类型。找封面图请用 image"),
        ] = "image",
        offset: OffsetParam = 0,
        count: CountParam = endpoints.MAX_LIST_COUNT,
        access_token: AccessTokenParam = None,
    ) -> Any:
        """分页获取永久素材列表。

        注意：图文素材库升级为草稿箱之后，type=news 只能取到草稿箱上线之前的
        历史图文素材；草稿箱里的内容请用 list_drafts。
        """
        return await materials.list_materials(
            ctx,
            material_type=material_type,
            offset=offset,
            count=count,
            access_token=access_token,
        )

    @mcp.tool(
        name="count_materials", title="获取素材数量", annotations=ANNOT_READ_ONLY
    )
    async def count_materials(access_token: AccessTokenParam = None) -> Any:
        """获取各类永久素材的数量统计。"""
        return await materials.count_materials(ctx, access_token=access_token)

    # -- 发布 ---------------------------------------------------------------

    @mcp.tool(
        name="publish_wechat_draft",
        title="发布微信草稿",
        annotations=ANNOT_DESTRUCTIVE,
    )
    async def publish_wechat_draft(
        draft_media_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=256,
                description="要发布的草稿 media_id，由 create_wechat_draft 返回",
            ),
        ],
        access_token: AccessTokenParam = None,
    ) -> Any:
        """提交草稿发布。**这是对外发文的动作，不可撤销。**

        微信侧为异步处理：success 为 true 只代表提交成功，不代表已经发出。
        请用返回的 publish_id 调用 get_publish_status 查询最终结果。
        """
        return await publishing.publish_draft(
            ctx, draft_media_id=draft_media_id, access_token=access_token
        )

    @mcp.tool(
        name="get_publish_status",
        title="查询发布状态",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            # 状态会随时间从“正在发布”变成“发布成功”，同参数不同时刻结果不同。
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def get_publish_status(
        publish_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=128,
                description="publish_id，由 publish_wechat_draft 返回",
            ),
        ],
        access_token: AccessTokenParam = None,
    ) -> Any:
        """查询某个发布任务的结果。

        返回的 publish_status 是微信的原始状态码（0 成功、1 发布中、3 失败等），
        同时附带 publish_status_text 给出中文说明。
        """
        return await publishing.get_publish_status(
            ctx, publish_id=publish_id, access_token=access_token
        )

    @mcp.tool(
        name="list_published", title="获取已发布列表", annotations=ANNOT_READ_ONLY
    )
    async def list_published(
        offset: OffsetParam = 0,
        count: CountParam = endpoints.MAX_LIST_COUNT,
        no_content: NoContentParam = True,
        access_token: AccessTokenParam = None,
    ) -> Any:
        """分页获取已成功发布的内容列表。

        只包含通过“发布”接口发出的内容。
        """
        return await publishing.list_published(
            ctx,
            offset=offset,
            count=count,
            no_content=no_content,
            access_token=access_token,
        )


__all__ = ["register_all", "ANNOT_READ_ONLY", "ANNOT_ADDITIVE", "ANNOT_DESTRUCTIVE"]
