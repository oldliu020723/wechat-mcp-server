# wechat-mcp-server

微信公众号开放接口的 MCP 服务。支持 `stdio` 与 `streamable-http` 两种传输，
**可以直接用源码运行**，不需要 `pip install` 这个项目本身。

## 特性

- **11 个工具**：凭证获取、草稿创建/删除/列表、素材删除/列表/计数、发布/状态查询。
- **双传输**：`stdio` 给本地 MCP 客户端直接拉起；`streamable-http` 给远程调用，
  带 Bearer 认证。
- **安全加固**：封面图下载有完整 SSRF 防护、依赖锁版本、HTTP 认证、有界限流器。
- **零安装运行**：`python run.py` 即可启动，只有第三方依赖需要事先安装。

## 环境要求

- Python **>= 3.10**（本仓库在 3.12.3 上开发验证）
- 建议使用独立的虚拟环境

## 安装

```bash
# 1) 创建并激活虚拟环境（Ubuntu 24.04 等发行版禁止向系统 Python 装包）
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2) 安装依赖——务必显式指定 HTTPS 官方源
pip install -r requirements.txt -i https://pypi.org/simple
```

> **为什么一定要写 `-i https://pypi.org/simple`**
> 明文 HTTP 的镜像源可以被中间人替换任意 wheel 包。本项目做了不少安全加固，
> 但如果依赖本身被投毒，那些加固都没有意义。加 `-i` 参数可以覆盖本机的
> `pip.conf` 设置，且不需要改动你的系统配置。

依赖是**锁死版本**的，原因很具体：

| 包 | 锁定值 | 为什么不能放开 |
|---|---|---|
| `mcp` | `==1.30.0` | mcp 2.x 把 `FastMCP` 改名为 `MCPServer`，并把 `mcp.server.fastmcp` 变成了一个导入即抛异常的存根。装到 2.x 服务直接起不来。 |
| `httpx` | `==0.28.1` | 代码直接用到了 `sni_hostname` 与 `network_stream` 这两个传输层扩展，行为必须精确可控。 |
| `anyio` | `>=4.5,<5` | 用于把阻塞的 DNS 解析挪到线程里。 |

## 快速开始

### stdio 传输

由 MCP 客户端（Claude Desktop、Cursor 等）直接拉起子进程，**不暴露任何网络端口**：

```bash
python run.py --transport stdio
```

### streamable-http 传输

```bash
# 只监听本机（默认），无需认证
python run.py --transport streamable-http --host 127.0.0.1 --port 8000

# 对外提供服务：必须同时给出认证令牌和允许的 Host
export WECHAT_MCP_AUTH_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
python run.py --transport streamable-http --host 0.0.0.0 --port 8000 \
  --allowed-host mcp.example.internal
```

端点路径为 **`/mcp`**。

> 本服务**不提供旧的 HTTP+SSE 传输**——MCP 协议已将其废弃，官方推荐 Streamable HTTP。

## 启动期的 fail-safe 校验

绑定到非回环地址时，以下两条缺一不可，否则**直接拒绝启动**（退出码 2）：

1. **必须配置 `WECHAT_MCP_AUTH_TOKEN`**（至少 32 字符）。
   没有认证就对外监听，等于把"用你的公众号发文"这个能力开放给整个网段。
2. **必须用 `--allowed-host` 声明客户端实际使用的主机名**（可重复）。
   实测确认：上游 mcp SDK 只在 host 为 `127.0.0.1` / `localhost` / `::1` 时
   才自动开启 DNS rebinding 防护；绑到 `0.0.0.0` 等地址时该防护为**关闭**状态，
   且不会有任何警告。所以这里强制要求显式声明。

## 客户端配置

### Claude Desktop / Cursor（stdio）

```json
{
  "mcpServers": {
    "wechat": {
      "command": "python",
      "args": ["/绝对路径/wechat-mcp-server/run.py", "--transport", "stdio"],
      "env": {
        "WECHAT_APPID": "你的AppID",
        "WECHAT_APPSECRET": "你的AppSecret"
      }
    }
  }
}
```

### 远程 HTTP 客户端

```json
{
  "mcpServers": {
    "wechat": {
      "type": "http",
      "url": "http://your-host:8000/mcp",
      "headers": {
        "Authorization": "Bearer 你的令牌"
      }
    }
  }
}
```

> `stdio` 模式**不经过认证**——它由客户端直接拉起进程，能启动进程就说明已经具备
> 本地执行权限，不存在网络暴露面。请不要把 stdio 的配置误当成"有令牌保护"。
>
> HTTP 模式下，请求头需为 `Authorization: Bearer <token>`。

## 工具一览

| 工具 | 作用 | 副作用 |
|---|---|---|
| `get_access_token` | 获取并缓存 access_token（约 2 小时有效） | — |
| `create_wechat_draft` | 下载封面图 → 上传素材 → **创建草稿（不发布）** | 新增 |
| `publish_wechat_draft` | 提交草稿发布 | **破坏性，不可撤销** |
| `del_wechat_draft` | 删除草稿 | **破坏性** |
| `del_wechat_material` | 删除永久素材 | **破坏性** |
| `get_publish_status` | 查询发布任务结果 | 只读 |
| `list_drafts` | 草稿列表 | 只读 |
| `count_drafts` | 草稿总数 | 只读 |
| `list_materials` | 永久素材列表 | 只读 |
| `count_materials` | 各类素材计数 | 只读 |
| `list_published` | 已成功发布的内容列表 | 只读 |

只读工具标记了 `readOnlyHint`，破坏性工具标记了 `destructiveHint`，
MCP 客户端可以据此判断能否自动调用。

### 推荐的使用流程

1. `get_access_token` 取凭证（服务端会缓存，后续工具可以省略 `access_token` 参数）。
2. `create_wechat_draft` 建草稿——**它不会发布**。
3. `publish_wechat_draft` 提交发布。
4. `get_publish_status` 确认结果。

第 4 步不能省：微信侧的发布是**异步**的，`publish_wechat_draft` 返回
`success: true` 只代表提交成功，不代表已经发出。

## 安全设计

### 封面图下载的 SSRF 防护

`create_wechat_draft` 的 `image_url` 由调用方提供，是唯一接受外部 URL 的入口。
下载流程有三层防护：

1. **解析即校验**：目标必须解析到公网 IP。内网、回环、链路本地、CGNAT 以及
   云元数据地址（`169.254.169.254`）全部拒绝；IPv4-mapped IPv6（`::ffff:127.0.0.1`）、
   6to4、Teredo 这些藏内网地址的形式也会被拆开判断。**只要有一条 DNS 记录落在
   非公网网段就整体拒绝**，不给"公私混合记录"留空子。
2. **连接固定在校验过的 IP 上**：校验完再按域名重解析一次是经典漏洞——攻击者能在
   两次解析之间用 DNS rebinding 把域名指向内网。这里直接把 URL 的主机名换成
   已校验的 IP 去连，同时用 `sni_hostname` 让 TLS 的 SNI 与证书校验仍针对原域名。
3. **读 body 前复核实际对端 IP**：万一前两层有疏漏，这一层兜底。

配套约束：`trust_env=False`（否则 `HTTP_PROXY` 会让代理绕过全部校验）、
不跟随重定向、端口限定在 80/443、体积上限 10MB、类型以**文件头魔数**为准
（响应头由对方控制，不可信）。

失败信息对外统一成一句话，具体原因只进日志——否则错误信息本身就变成了
一个内网端口探针。

### 凭证不外泄

微信把凭证放在 query string 里，而 HTTP 库抛出的异常文本**通常带完整 URL**。
因此：

- 所有异常都转换成不含 URL 的消息再回传；
- 日志写入前统一过 `redaction` 模块，把 `secret=`、`access_token=` 之类的值替换掉，
  并兜底擦除任何长度 ≥32 的疑似 token 串；
- `appsecret` 参数标了 `repr=False`，不会出现在任何 `repr()` 输出里。

### 有界的内存

限流器与 token 缓存都带 TTL 与容量上限，并会惰性清理过期条目。限流的 key 先经
带盐摘要再入字典，凭证不会以明文形式停在内存里。

## 微信侧准备

1. 到 [微信公众平台](https://mp.weixin.qq.com) 完成开发者认证，取得 AppID / AppSecret。
2. 在「设置与开发 → 开发接口管理 → IP 白名单」中，把**部署本服务的机器出口 IP**
   加进去。没有加白名单的话，微信会直接拒绝所有请求（错误码 40164）。
3. 建议先用测试号验证整条链路，避免在生产号上误发或误删。

> ⚠️ 自 2025 年 7 月起，个人主体账号、未认证企业主体账号的**发布相关接口权限会被回收**。
> 如果你的账号属于这些类型，`publish_wechat_draft` / `get_publish_status` /
> `list_published` 可能会返回权限错误（48001）。其余草稿与素材类工具不受影响。

## 开发

```bash
# 安装开发依赖（含 pytest）
pip install -r requirements-dev.txt -i https://pypi.org/simple

# 运行测试
python -m pytest -v
```

测试全程使用 `httpx.MockTransport` 与假 DNS 解析器，`conftest.py` 里有一个
自动生效的断网夹具——任何试图发起真实网络请求的测试都会立刻失败。

## 目录结构

```
wechat-mcp-server/
├── run.py                     # 源码直跑入口
├── wechat_mcp/
│   ├── cli.py                 # 命令行参数解析
│   ├── settings.py            # 配置数据类
│   ├── redaction.py           # 日志/错误信息脱敏
│   ├── security/
│   │   ├── url_guard.py       # SSRF 判定：公网 IP 校验、DNS 解析
│   │   ├── safe_fetch.py      # 受保护的图片下载
│   │   ├── rate_limit.py      # 有界滑动窗口限流
│   │   └── auth.py            # Bearer 认证中间件与绑定校验
│   ├── wechat/
│   │   ├── endpoints.py       # 接口路径常量
│   │   ├── http.py            # 异步 HTTP 客户端
│   │   ├── token.py           # token 缓存
│   │   └── errors.py          # 错误码解析
│   ├── tools/                 # 工具实现与 MCP 注册
│   └── server/app.py          # 服务装配与启动
├── tests/
└── examples/
```

## 授权

MIT，见 [LICENSE](LICENSE)。
