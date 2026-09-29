"""工具业务逻辑测试。用 MockTransport 模拟微信接口，全程不碰网络。"""

from __future__ import annotations

import httpx
import pytest

from wechat_mcp.security.rate_limit import SlidingWindowLimiter
from wechat_mcp.security.safe_fetch import FetchedBlob
from wechat_mcp.settings import settings_from_env
from wechat_mcp.tools import drafts, materials, publishing, tokens
from wechat_mcp.tools.context import AppContext
from wechat_mcp.tools.guards import INTERNAL_ERROR_MESSAGE, MISSING_TOKEN_MESSAGE, RATE_LIMIT_MESSAGE
from wechat_mcp.wechat.http import WeChatAPIClient
from wechat_mcp.wechat.token import TokenCache

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
TOKEN = "TEST_TOKEN_abcdefghijklmnopqrstuvwxyz0123"


def make_context(handler, **overrides) -> AppContext:
    """构造一个把 HTTP 层换成 MockTransport 的上下文。"""
    settings = settings_from_env(**overrides)
    return AppContext(
        settings=settings,
        wechat=WeChatAPIClient(transport=httpx.MockTransport(handler)),
        tokens=TokenCache(),
        limiter=SlidingWindowLimiter(
            limit=settings.requests_per_minute, window_seconds=60.0
        ),
    )


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"errcode": 0, **payload})


@pytest.fixture
def stub_image(monkeypatch):
    """把封面图下载替换成固定成功的桩。"""

    async def _fetch(url, **kwargs):
        return FetchedBlob(
            data=PNG,
            content_type="image/png",
            extension=".png",
            size=len(PNG),
            peer_ip="93.184.216.34",
        )

    monkeypatch.setattr(drafts, "fetch_image", _fetch)


# ---------------------------------------------------------------------------
# create_wechat_draft
# ---------------------------------------------------------------------------


async def test_create_draft_happy_path(stub_image):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/material/add_material"):
            return ok({"media_id": "IMG_MEDIA"})
        return ok({"media_id": "DRAFT_MEDIA"})

    ctx = make_context(handler)
    result = await drafts.create_draft(
        ctx,
        image_url="https://cdn.test/a.png",
        title="标题",
        content="<p>正文</p>",
        access_token=TOKEN,
    )

    assert result["success"] is True
    assert result["draft_media_id"] == "DRAFT_MEDIA"
    assert result["image_media_id"] == "IMG_MEDIA"
    # 顺序必须是先上传素材、再建草稿。
    assert calls == ["/cgi-bin/material/add_material", "/cgi-bin/draft/add"]
    await ctx.aclose()


async def test_create_draft_uses_uploaded_media_id_as_thumb(stub_image):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/material/add_material"):
            return ok({"media_id": "IMG_MEDIA"})
        import json

        captured.update(json.loads(request.content))
        return ok({"media_id": "DRAFT_MEDIA"})

    ctx = make_context(handler)
    await drafts.create_draft(
        ctx,
        image_url="https://cdn.test/a.png",
        title="t",
        content="c",
        access_token=TOKEN,
    )

    assert captured["articles"][0]["thumb_media_id"] == "IMG_MEDIA"
    await ctx.aclose()


async def test_create_draft_reports_image_rejection(monkeypatch):
    """图片被安全层拒绝时，返回失败信封而不是抛异常。"""
    from wechat_mcp.errors import SSRFBlocked

    async def _reject(url, **kwargs):
        raise SSRFBlocked("目标地址属于内网或保留网段，已拒绝访问")

    monkeypatch.setattr(drafts, "fetch_image", _reject)

    ctx = make_context(lambda request: ok({}))
    result = await drafts.create_draft(
        ctx,
        image_url="http://169.254.169.254/x.png",
        title="t",
        content="c",
        access_token=TOKEN,
    )

    assert result["success"] is False
    assert "内网" in result["error_msg"]
    await ctx.aclose()


async def test_create_draft_requires_token():
    ctx = make_context(lambda request: ok({}))
    result = await drafts.create_draft(
        ctx, image_url="https://cdn.test/a.png", title="t", content="c"
    )

    assert result["success"] is False
    assert result["error_msg"] == MISSING_TOKEN_MESSAGE
    await ctx.aclose()


async def test_create_draft_propagates_wechat_error(stub_image):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/material/add_material"):
            return ok({"media_id": "IMG"})
        return httpx.Response(200, json={"errcode": 45003, "errmsg": "title too long"})

    ctx = make_context(handler)
    result = await drafts.create_draft(
        ctx,
        image_url="https://cdn.test/a.png",
        title="t",
        content="c",
        access_token=TOKEN,
    )

    assert result["success"] is False
    assert result["errcode"] == 45003
    assert "45003" in result["error_msg"]
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------


async def test_rate_limit_blocks_after_quota(stub_image):
    ctx = make_context(lambda request: ok({"media_id": "M"}), requests_per_minute=1)

    first = await drafts.create_draft(
        ctx, image_url="https://cdn.test/a.png", title="t", content="c", access_token=TOKEN
    )
    second = await drafts.create_draft(
        ctx, image_url="https://cdn.test/a.png", title="t", content="c", access_token=TOKEN
    )

    assert first["success"] is True
    assert second["success"] is False
    assert second["error_msg"] == RATE_LIMIT_MESSAGE
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 删除与素材
# ---------------------------------------------------------------------------


async def test_delete_draft_success():
    ctx = make_context(lambda request: ok({}))
    result = await drafts.delete_draft(ctx, media_id="M1", access_token=TOKEN)

    assert result == {"success": True, "errcode": 0, "error_msg": None}
    await ctx.aclose()


async def test_delete_material_success():
    ctx = make_context(lambda request: ok({}))
    result = await materials.delete_material(ctx, media_id="M1", access_token=TOKEN)

    assert result["success"] is True
    await ctx.aclose()


async def test_list_materials_parses_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "total_count": 7,
                "item_count": 1,
                "item": [
                    {
                        "media_id": "M1",
                        "name": "cover.png",
                        "update_time": 1700000000,
                        "url": "https://mmbiz.qpic.cn/x",
                    }
                ],
            }
        )

    ctx = make_context(handler)
    result = await materials.list_materials(ctx, access_token=TOKEN)

    assert result["success"] is True
    assert result["total_count"] == 7
    assert result["materials"][0]["media_id"] == "M1"
    assert result["materials"][0]["name"] == "cover.png"
    await ctx.aclose()


async def test_count_materials_reads_count_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {"image_count": 3, "voice_count": 1, "video_count": 0, "news_count": 2}
        )

    ctx = make_context(handler)
    result = await materials.count_materials(ctx, access_token=TOKEN)

    assert (result["image"], result["voice"], result["video"], result["news"]) == (3, 1, 0, 2)
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 草稿列表
# ---------------------------------------------------------------------------


async def test_list_drafts_parses_titles():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "total_count": 2,
                "item_count": 2,
                "item": [
                    {
                        "media_id": "D1",
                        "update_time": 1700000000,
                        "content": {"news_item": [{"title": "第一篇"}]},
                    },
                    {"media_id": "D2", "update_time": 1700000001},
                ],
            }
        )

    ctx = make_context(handler)
    result = await drafts.list_drafts(ctx, access_token=TOKEN)

    assert result["total_count"] == 2
    assert result["drafts"][0]["title"] == "第一篇"
    # 没有 content 的条目不该报错，标题就是 None。
    assert result["drafts"][1]["title"] is None
    await ctx.aclose()


async def test_count_drafts():
    ctx = make_context(lambda request: ok({"total_count": 5}))
    result = await drafts.count_drafts(ctx, access_token=TOKEN)

    assert result["success"] is True
    assert result["total_count"] == 5
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


async def test_publish_draft_submits():
    ctx = make_context(lambda request: ok({"publish_id": "P1", "msg_data_id": "MD1"}))
    result = await publishing.publish_draft(ctx, draft_media_id="D1", access_token=TOKEN)

    assert result["success"] is True
    assert result["publish_id"] == "P1"
    assert result["msg_data_id"] == "MD1"
    await ctx.aclose()


async def test_get_publish_status_translates_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"publish_id": "P1", "publish_status": 0, "article_id": "A1"})

    ctx = make_context(handler)
    result = await publishing.get_publish_status(ctx, publish_id="P1", access_token=TOKEN)

    assert result["publish_status"] == 0
    assert result["publish_status_text"] == "发布成功"
    assert result["article_id"] == "A1"
    await ctx.aclose()


async def test_get_publish_status_reports_failure_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"publish_id": "P1", "publish_status": 3, "fail_idx": [0]})

    ctx = make_context(handler)
    result = await publishing.get_publish_status(ctx, publish_id="P1", access_token=TOKEN)

    assert result["publish_status_text"] == "发布失败"
    assert result["fail_idx"] == [0]
    await ctx.aclose()


async def test_list_published_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "total_count": 1,
                "item_count": 1,
                "item": [{"article_id": "A1", "update_time": 1700000000}],
            }
        )

    ctx = make_context(handler)
    result = await publishing.list_published(ctx, access_token=TOKEN)

    assert result["articles"][0]["article_id"] == "A1"
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 凭证
# ---------------------------------------------------------------------------


async def test_get_access_token_caches_result():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.path))
        return httpx.Response(200, json={"access_token": "T1", "expires_in": 7200})

    ctx = make_context(handler)

    first = await tokens.fetch_access_token(ctx, appid="wxappid", appsecret="s" * 40)
    second = await tokens.fetch_access_token(ctx, appid="wxappid", appsecret="s" * 40)

    assert first["success"] is True
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    # 第二次不应再打微信接口——重复签发会让旧 token 失效。
    assert len(calls) == 1
    await ctx.aclose()


async def test_get_access_token_without_credentials_reports_clearly():
    ctx = make_context(lambda request: ok({}))
    result = await tokens.fetch_access_token(ctx)

    assert result["success"] is False
    assert "appid" in result["error_msg"]
    await ctx.aclose()


async def test_token_param_is_reused_by_other_tools(monkeypatch):
    """先取 token，后续工具即可省略 access_token 参数。"""
    monkeypatch.setenv("WECHAT_APPID", "wxappid")
    monkeypatch.setenv("WECHAT_APPSECRET", "s" * 40)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "CACHED", "expires_in": 7200})
        return ok({"total_count": 4})

    ctx = make_context(handler)
    await tokens.fetch_access_token(ctx)
    result = await drafts.count_drafts(ctx)

    assert result["success"] is True
    assert result["total_count"] == 4
    await ctx.aclose()


# ---------------------------------------------------------------------------
# 未预期异常
# ---------------------------------------------------------------------------


async def test_unexpected_exception_is_not_leaked(monkeypatch):
    """未预期异常的原文不得回传给调用方，只给通用文案。"""

    async def _boom(url, **kwargs):
        raise RuntimeError("internal detail at https://secret.test/?token=abc")

    monkeypatch.setattr(drafts, "fetch_image", _boom)

    ctx = make_context(lambda request: ok({}))
    result = await drafts.create_draft(
        ctx, image_url="https://cdn.test/a.png", title="t", content="c", access_token=TOKEN
    )

    assert result["success"] is False
    assert result["error_msg"] == INTERNAL_ERROR_MESSAGE
    assert "secret.test" not in result["error_msg"]
    await ctx.aclose()
