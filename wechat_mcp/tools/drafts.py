"""草稿相关工具的业务实现：创建、删除、列表、计数。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from wechat_mcp.security.safe_fetch import fetch_image
from wechat_mcp.tools.context import AppContext
from wechat_mcp.tools.guards import (
    MISSING_TOKEN_MESSAGE,
    basic_failure,
    enforce_rate_limit,
    error_fields,
    resolve_access_token,
)
from wechat_mcp.tools.results import (
    BasicResult,
    DraftCountResult,
    DraftListResult,
    DraftResult,
    DraftSummary,
)
from wechat_mcp.wechat import endpoints

logger = logging.getLogger(__name__)


def _draft_failure(message: str, errcode: Optional[int] = None) -> DraftResult:
    return DraftResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        draft_media_id=None,
        image_media_id=None,
    )


def _draft_title(item: Mapping[str, Any]) -> Optional[str]:
    """从草稿条目里取标题。

    ``no_content=1`` 时微信不返回 ``content``，此时标题不可得——这属于正常
    情况，返回 ``None`` 即可，不应当报错。
    """
    content = item.get("content")
    if not isinstance(content, Mapping):
        return None

    news_items = content.get("news_item")
    if not isinstance(news_items, list) or not news_items:
        return None

    first = news_items[0]
    if not isinstance(first, Mapping):
        return None

    title = first.get("title")
    return str(title) if title is not None else None


async def create_draft(
    ctx: AppContext,
    *,
    image_url: str,
    title: str,
    content: str,
    access_token: Optional[str] = None,
    author: str = "",
    digest: str = "",
    source_url: str = "",
    need_open_comment: int = 0,
    only_fans_can_comment: int = 0,
) -> DraftResult:
    """下载封面图 → 上传为永久素材 → 创建草稿。

    本工具**只建草稿，不会发布**。发布需要另外调用 ``publish_wechat_draft``。
    """
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _draft_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "draft_create", token)
    if limit_message:
        return _draft_failure(limit_message)

    # 下载封面图。这一步是唯一接受调用方指定 URL 的地方，全程受
    # url_guard / safe_fetch 保护：只允许公网目标、连接固定在校验过的 IP 上。
    try:
        blob = await fetch_image(
            image_url,
            allowed_types=endpoints.ALLOWED_IMAGE_TYPES,
            max_bytes=ctx.settings.max_image_bytes,
            timeout=ctx.settings.http_timeout,
        )
    except Exception as exc:
        fields = error_fields(exc, "create_wechat_draft")
        return _draft_failure(fields["error_msg"], fields["errcode"])

    try:
        upload = await ctx.wechat.post_multipart(
            endpoints.PATH_MATERIAL_ADD,
            params={"access_token": token, "type": "image"},
            field_name="media",
            filename=f"cover{blob.extension}",
            content=blob.data,
            content_type=blob.content_type,
        )
        image_media_id = upload.get("media_id")
        if not image_media_id:
            return _draft_failure("微信未返回素材 media_id")

        article: dict[str, Any] = {
            "articles": [
                {
                    "title": title,
                    "author": author,
                    "digest": digest,
                    "content": content,
                    "content_source_url": source_url,
                    "thumb_media_id": image_media_id,
                    "need_open_comment": need_open_comment,
                    "only_fans_can_comment": only_fans_can_comment,
                }
            ]
        }
        created = await ctx.wechat.post_json(
            endpoints.PATH_DRAFT_ADD,
            params={"access_token": token},
            payload=article,
        )
    except Exception as exc:
        fields = error_fields(exc, "create_wechat_draft")
        return _draft_failure(fields["error_msg"], fields["errcode"])

    draft_media_id = created.get("media_id")
    if not draft_media_id:
        return _draft_failure("微信未返回草稿 media_id")

    logger.info("草稿已创建")
    return DraftResult(
        success=True,
        errcode=0,
        error_msg=None,
        draft_media_id=str(draft_media_id),
        image_media_id=str(image_media_id),
    )


async def delete_draft(
    ctx: AppContext,
    *,
    media_id: str,
    access_token: Optional[str] = None,
) -> BasicResult:
    """删除指定草稿。"""
    token = resolve_access_token(ctx, access_token)
    if not token:
        return basic_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "draft_delete", token)
    if limit_message:
        return basic_failure(limit_message)

    try:
        await ctx.wechat.post_json(
            endpoints.PATH_DRAFT_DELETE,
            params={"access_token": token},
            payload={"media_id": media_id},
        )
    except Exception as exc:
        fields = error_fields(exc, "del_wechat_draft")
        return basic_failure(fields["error_msg"], fields["errcode"])

    logger.info("草稿已删除")
    return BasicResult(success=True, errcode=0, error_msg=None)


async def list_drafts(
    ctx: AppContext,
    *,
    offset: int = 0,
    count: int = endpoints.MAX_LIST_COUNT,
    no_content: bool = True,
    access_token: Optional[str] = None,
) -> DraftListResult:
    """分页获取草稿列表。

    ``no_content=True`` 时微信不返回正文 HTML，响应体积小很多，默认开启；
    代价是拿不到标题（微信只在 ``content.news_item`` 里给标题）。
    """
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _list_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "draft_list", token)
    if limit_message:
        return _list_failure(limit_message)

    try:
        payload = await ctx.wechat.post_json(
            endpoints.PATH_DRAFT_BATCHGET,
            params={"access_token": token},
            payload={
                "offset": offset,
                "count": count,
                "no_content": 1 if no_content else 0,
            },
        )
    except Exception as exc:
        fields = error_fields(exc, "list_drafts")
        return _list_failure(fields["error_msg"], fields["errcode"])

    raw_items = payload.get("item") or []
    drafts = [
        DraftSummary(
            media_id=str(item.get("media_id", "")),
            update_time=int(item.get("update_time", 0) or 0),
            title=_draft_title(item),
        )
        for item in raw_items
        if isinstance(item, Mapping)
    ]

    return DraftListResult(
        success=True,
        errcode=0,
        error_msg=None,
        total_count=int(payload.get("total_count", 0) or 0),
        item_count=int(payload.get("item_count", len(drafts)) or 0),
        drafts=drafts,
    )


def _list_failure(message: str, errcode: Optional[int] = None) -> DraftListResult:
    return DraftListResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        total_count=0,
        item_count=0,
        drafts=[],
    )


async def count_drafts(
    ctx: AppContext,
    *,
    access_token: Optional[str] = None,
) -> DraftCountResult:
    """获取草稿总数。"""
    token = resolve_access_token(ctx, access_token)
    if not token:
        return DraftCountResult(
            success=False, errcode=None, error_msg=MISSING_TOKEN_MESSAGE, total_count=0
        )

    limit_message = enforce_rate_limit(ctx, "draft_count", token)
    if limit_message:
        return DraftCountResult(
            success=False, errcode=None, error_msg=limit_message, total_count=0
        )

    try:
        payload = await ctx.wechat.get_json(
            endpoints.PATH_DRAFT_COUNT, params={"access_token": token}
        )
    except Exception as exc:
        fields = error_fields(exc, "count_drafts")
        return DraftCountResult(
            success=False,
            errcode=fields["errcode"],
            error_msg=fields["error_msg"],
            total_count=0,
        )

    return DraftCountResult(
        success=True,
        errcode=0,
        error_msg=None,
        total_count=int(payload.get("total_count", 0) or 0),
    )


__all__ = ["create_draft", "delete_draft", "list_drafts", "count_drafts"]
