"""微信开放接口的端点常量与素材约束。

本模块只放常量，不发请求。

安全提示：微信把凭证放在 query string 里（``?access_token=...``），因此任何
回显 URL 或原始异常的地方都必须先过 :mod:`wechat_mcp.redaction`——HTTP 库的
异常文本通常带完整 URL。
"""

from __future__ import annotations

from typing import Final

#: 接口 origin。硬编码，永不接受调用方传入。
API_ORIGIN: Final[str] = "https://api.weixin.qq.com"

# -- 写入类接口 -------------------------------------------------------------

PATH_TOKEN: Final[str] = "/cgi-bin/token"
PATH_DRAFT_ADD: Final[str] = "/cgi-bin/draft/add"
PATH_DRAFT_DELETE: Final[str] = "/cgi-bin/draft/delete"
PATH_MATERIAL_ADD: Final[str] = "/cgi-bin/material/add_material"
PATH_MATERIAL_DELETE: Final[str] = "/cgi-bin/material/del_material"
PATH_PUBLISH_SUBMIT: Final[str] = "/cgi-bin/freepublish/submit"

# -- 只读类接口 -------------------------------------------------------------

PATH_DRAFT_BATCHGET: Final[str] = "/cgi-bin/draft/batchget"
PATH_DRAFT_COUNT: Final[str] = "/cgi-bin/draft/count"
PATH_MATERIAL_BATCHGET: Final[str] = "/cgi-bin/material/batchget_material"
PATH_MATERIAL_COUNT: Final[str] = "/cgi-bin/material/get_materialcount"
PATH_PUBLISH_GET: Final[str] = "/cgi-bin/freepublish/get"
PATH_PUBLISH_BATCHGET: Final[str] = "/cgi-bin/freepublish/batchget"

# -- 约束 -------------------------------------------------------------------

#: 允许的封面图类型到扩展名的映射。微信永久素材只接受这四种。
ALLOWED_IMAGE_TYPES: Final[dict[str, str]] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

#: 微信侧对图片素材的体积上限。
MAX_IMAGE_BYTES: Final[int] = 10 * 1024 * 1024

#: 列表类接口单次可返回的条目数上限（微信侧硬限制）。
MAX_LIST_COUNT: Final[int] = 20

#: 素材类型枚举，对应 batchget_material 的 type 参数。
MATERIAL_TYPES: Final[tuple[str, ...]] = ("image", "video", "voice", "news")

ERROR_DOC_URL: Final[str] = (
    "https://developers.weixin.qq.com/doc/oplatform/developers/errCode/"
)

__all__ = [
    "API_ORIGIN",
    "PATH_TOKEN",
    "PATH_DRAFT_ADD",
    "PATH_DRAFT_DELETE",
    "PATH_DRAFT_BATCHGET",
    "PATH_DRAFT_COUNT",
    "PATH_MATERIAL_ADD",
    "PATH_MATERIAL_DELETE",
    "PATH_MATERIAL_BATCHGET",
    "PATH_MATERIAL_COUNT",
    "PATH_PUBLISH_SUBMIT",
    "PATH_PUBLISH_GET",
    "PATH_PUBLISH_BATCHGET",
    "ALLOWED_IMAGE_TYPES",
    "MAX_IMAGE_BYTES",
    "MAX_LIST_COUNT",
    "MATERIAL_TYPES",
    "ERROR_DOC_URL",
]
