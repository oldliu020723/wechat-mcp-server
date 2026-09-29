#!/usr/bin/env python3
"""wechat-mcp-server 源码直跑入口——不需要 ``pip install -e .``。

用法::

    python run.py --transport stdio
    python run.py --transport streamable-http --host 127.0.0.1 --port 8000

脚本所在目录会被 CPython 自动放进 ``sys.path``，因此 ``wechat_mcp`` 包可以直接
被导入。这里额外插入一次，是为了让 ``python /绝对路径/run.py`` 从任意工作目录
启动时也能成立。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from wechat_mcp.cli import main  # noqa: E402  (必须在修正 sys.path 之后导入)

if __name__ == "__main__":
    raise SystemExit(main())
