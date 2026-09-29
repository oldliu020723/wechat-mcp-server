"""素材相关工具的业务实现：删除、列表、计数。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

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
    MaterialCountResult,
    MaterialListResult,
    MaterialSummary,
)
from wechat_mcp.wechat import endpoints

logger = logging.getLogger(__name__)


async def delete_material(
    ctx: AppContext,
    *,
    media_id: str,
    access_token: Optional[str] = None,
) -> BasicResult:
    """删除指定的永久素材（例如封面图）。"""
    token = resolve_access_token(ctx, access_token)
    if not token:
        return basic_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "material_delete", token)
    if limit_message:
        return basic_failure(limit_message)

    try:
        await ctx.wechat.post_json(
            endpoints.PATH_MATERIAL_DELETE,
            params={"access_token": token},
            payload={"media_id": media_id},
        )
    except Exception as exc:
        fields = error_fields(exc, "del_wechat_material")
        return basic_failure(fields["error_msg"], fields["errcode"])

    logger.info("素材已删除")
    return BasicResult(success=True, errcode=0, error_msg=None)


def _list_failure(message: str, errcode: Optional[int] = None) -> MaterialListResult:
    return MaterialListResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        total_count=0,
        item_count=0,
        materials=[],
    )


async def list_materials(
    ctx: AppContext,
    *,
    material_type: str = "image",
    offset: int = 0,
    count: int = endpoints.MAX_LIST_COUNT,
    access_token: Optional[str] = None,
) -> MaterialListResult:
    """分页获取永久素材列表。

    注意一个容易踩的坑：图文素材库升级为草稿箱之后，``type="news"`` 只能取到
    草稿箱上线**之前**的历史图文素材。要看草稿箱里的内容请用 ``list_drafts``。
    """
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _list_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "material_list", token)
    if limit_message:
        return _list_failure(limit_message)

    try:
        payload = await ctx.wechat.post_json(
            endpoints.PATH_MATERIAL_BATCHGET,
            params={"access_token": token},
            payload={"type": material_type, "offset": offset, "count": count},
        )
    except Exception as exc:
        fields = error_fields(exc, "list_materials")
        return _list_failure(fields["error_msg"], fields["errcode"])

    raw_items = payload.get("item") or []
    materials = [
        MaterialSummary(
            media_id=str(item.get("media_id", "")),
            name=str(item.get("name", "")),
            update_time=int(item.get("update_time", 0) or 0),
            url=str(item.get("url", "")),
        )
        for item in raw_items
        if isinstance(item, Mapping)
    ]

    return MaterialListResult(
        success=True,
        errcode=0,
        error_msg=None,
        total_count=int(payload.get("total_count", 0) or 0),
        item_count=int(payload.get("item_count", len(materials)) or 0),
        materials=materials,
    )


def _count_value(payload: Mapping[str, Any], key: str) -> int:
    try:
        return int(payload.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


async def count_materials(
    ctx: AppContext,
    *,
    access_token: Optional[str] = None,
) -> MaterialCountResult:
    """获取各类永久素材的数量。"""
    token = resolve_access_token(ctx, access_token)
    if not token:
        return MaterialCountResult(
            success=False,
            errcode=None,
            error_msg=MISSING_TOKEN_MESSAGE,
            image=0,
            voice=0,
            video=0,
            news=0,
        )

    limit_message = enforce_rate_limit(ctx, "material_count", token)
    if limit_message:
        return MaterialCountResult(
            success=False,
            errcode=None,
            error_msg=limit_message,
            image=0,
            voice=0,
            video=0,
            news=0,
        )

    try:
        payload = await ctx.wechat.get_json(
            endpoints.PATH_MATERIAL_COUNT, params={"access_token": token}
        )
    except Exception as exc:
        fields = error_fields(exc, "count_materials")
        return MaterialCountResult(
            success=False,
            errcode=fields["errcode"],
            error_msg=fields["error_msg"],
            image=0,
            voice=0,
            video=0,
            news=0,
        )

    return MaterialCountResult(
        success=True,
        errcode=0,
        error_msg=None,
        image=_count_value(payload, "image_count"),
        voice=_count_value(payload, "voice_count"),
        video=_count_value(payload, "video_count"),
        news=_count_value(payload, "news_count"),
    )


__all__ = ["delete_material", "list_materials", "count_materials"]
