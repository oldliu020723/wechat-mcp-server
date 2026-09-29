"""发布相关工具的业务实现：提交发布、查询发布状态、已发布列表。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from wechat_mcp.tools.context import AppContext
from wechat_mcp.tools.guards import (
    MISSING_TOKEN_MESSAGE,
    enforce_rate_limit,
    error_fields,
    resolve_access_token,
)
from wechat_mcp.tools.results import (
    PublishedListResult,
    PublishedSummary,
    PublishResult,
    PublishStatusResult,
    describe_publish_status,
)
from wechat_mcp.wechat import endpoints

logger = logging.getLogger(__name__)

ASYNCHRONOUS_HINT = (
    "发布是异步的：本接口返回 publish_id 只代表提交成功，"
    "请调用 get_publish_status 查询最终结果"
)


async def publish_draft(
    ctx: AppContext,
    *,
    draft_media_id: str,
    access_token: Optional[str] = None,
) -> PublishResult:
    """提交草稿发布。

    **这是对外发文的动作，不可撤销。** 微信侧是异步处理的，返回 ``success=True``
    仅表示提交成功，真正的发布结果要用 ``get_publish_status`` 查。
    """
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _publish_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "publish", token)
    if limit_message:
        return _publish_failure(limit_message)

    try:
        payload = await ctx.wechat.post_json(
            endpoints.PATH_PUBLISH_SUBMIT,
            params={"access_token": token},
            payload={"media_id": draft_media_id},
        )
    except Exception as exc:
        fields = error_fields(exc, "publish_wechat_draft")
        return _publish_failure(fields["error_msg"], fields["errcode"])

    publish_id = payload.get("publish_id")
    logger.info("发布已提交")

    return PublishResult(
        success=True,
        errcode=0,
        error_msg=None,
        publish_id=str(publish_id) if publish_id else None,
        msg_data_id=str(payload.get("msg_data_id")) if payload.get("msg_data_id") else None,
    )


def _publish_failure(message: str, errcode: Optional[int] = None) -> PublishResult:
    return PublishResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        publish_id=None,
        msg_data_id=None,
    )


async def get_publish_status(
    ctx: AppContext,
    *,
    publish_id: str,
    access_token: Optional[str] = None,
) -> PublishStatusResult:
    """查询某个发布任务的结果。

    状态码的取值含义见 :data:`wechat_mcp.tools.results.PUBLISH_STATUS_TEXT`，
    这里会一并返回中文描述，省得调用方去猜数字。
    """
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _status_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "publish_status", token)
    if limit_message:
        return _status_failure(limit_message)

    try:
        payload = await ctx.wechat.post_json(
            endpoints.PATH_PUBLISH_GET,
            params={"access_token": token},
            payload={"publish_id": publish_id},
        )
    except Exception as exc:
        fields = error_fields(exc, "get_publish_status")
        return _status_failure(fields["error_msg"], fields["errcode"])

    status_code = payload.get("publish_status")
    fail_idx = payload.get("fail_idx")
    article_id = payload.get("article_id")

    return PublishStatusResult(
        success=True,
        errcode=0,
        error_msg=None,
        publish_status=int(status_code) if status_code is not None else None,
        publish_status_text=describe_publish_status(status_code),
        article_id=str(article_id) if article_id else None,
        fail_idx=list(fail_idx) if isinstance(fail_idx, list) else None,
    )


def _status_failure(message: str, errcode: Optional[int] = None) -> PublishStatusResult:
    return PublishStatusResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        publish_status=None,
        publish_status_text=None,
        article_id=None,
        fail_idx=None,
    )


def _list_failure(message: str, errcode: Optional[int] = None) -> PublishedListResult:
    return PublishedListResult(
        success=False,
        errcode=errcode,
        error_msg=message,
        total_count=0,
        item_count=0,
        articles=[],
    )


async def list_published(
    ctx: AppContext,
    *,
    offset: int = 0,
    count: int = endpoints.MAX_LIST_COUNT,
    no_content: bool = True,
    access_token: Optional[str] = None,
) -> PublishedListResult:
    """分页获取已成功发布的内容列表。"""
    token = resolve_access_token(ctx, access_token)
    if not token:
        return _list_failure(MISSING_TOKEN_MESSAGE)

    limit_message = enforce_rate_limit(ctx, "publish_list", token)
    if limit_message:
        return _list_failure(limit_message)

    try:
        payload = await ctx.wechat.post_json(
            endpoints.PATH_PUBLISH_BATCHGET,
            params={"access_token": token},
            payload={
                "offset": offset,
                "count": count,
                "no_content": 1 if no_content else 0,
            },
        )
    except Exception as exc:
        fields = error_fields(exc, "list_published")
        return _list_failure(fields["error_msg"], fields["errcode"])

    raw_items = payload.get("item") or []
    articles = [
        PublishedSummary(
            article_id=str(item.get("article_id", "")),
            update_time=int(item.get("update_time", 0) or 0),
        )
        for item in raw_items
        if isinstance(item, Mapping)
    ]

    return PublishedListResult(
        success=True,
        errcode=0,
        error_msg=None,
        total_count=int(payload.get("total_count", 0) or 0),
        item_count=int(payload.get("item_count", len(articles)) or 0),
        articles=articles,
    )


__all__ = [
    "publish_draft",
    "get_publish_status",
    "list_published",
    "ASYNCHRONOUS_HINT",
]
