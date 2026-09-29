"""pytest 全局夹具。"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """禁止任何测试真的发起网络连接。

    有了这道闸，某个测试万一漏了 mock 会立刻失败，而不是让结果悄悄依赖外网——
    同时也能保证 DNS 解析在测试里只可能来自显式注入的假解析器。
    """
    def _forbidden(*args, **kwargs):
        raise AssertionError("测试中不允许发起真实网络请求")

    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
