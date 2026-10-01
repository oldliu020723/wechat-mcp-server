# wechat-mcp-server

微信公众号开放接口的 MCP 服务。支持 `stdio` 与 `streamable-http` 两种传输，

本文只讲部署与配置。两种部署形态：

| 方式 | 适用场景 | 认证 |
|---|---|---|
| [stdio](#部署一stdio本机使用) | 客户端和服务在同一台机器上 | 不需要 |
| [streamable-http](#部署二streamable-http远程常驻) | 服务跑在远程设备上，其他机器通过 HTTP 调用 | 必须带 Bearer 令牌 |

## 环境要求

- Python **>= 3.10**（本仓库在 3.12.3 上开发验证）
- Linux / macOS / Windows 均可；远程常驻方案需要 systemd 的 Linux
- 部署机器能访问 `api.weixin.qq.com`（出网）

## 第一步：微信侧准备

1. 到 [微信公众平台](https://mp.weixin.qq.com) 完成开发者认证，取得 **AppID / AppSecret**
   （「设置与开发 → 开发接口管理 → 基本配置」）。

2. 在同一页面找到「**IP 白名单**」，把**部署本服务的机器的出口 IP** 加进去。
   不加白名单微信会直接拒绝所有请求，报错误码 **40164**。
   注意填的是它访问外网时用的那个 IP，不是内网 IP。

3. 建议先用**测试号**验证整条链路，避免在生产号上误发或误删。

> ⚠️ 自 2025 年 7 月起，个人主体账号、未认证企业主体账号的**发布相关接口权限会被回收**。
> 这类账号调用 `publish_wechat_draft` / `get_publish_status` / `list_published`
> 会返回 **48001**。草稿与素材类工具不受影响。

## 安装

两种方式任选其一。**方式一不需要 `pip install` 本项目本身。**

### 方式一：源码直跑（推荐）

```bash
# 1) 创建并激活虚拟环境（Ubuntu 24.04 等发行版禁止向系统 Python 装包）
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2) 安装依赖——务必显式指定 HTTPS 官方源
pip install -r requirements.txt -i https://pypi.org/simple
```

### 方式二：安装为包

```bash
pip install . -i https://pypi.org/simple
```

装完会得到一个 `wechat-mcp-server` 命令，与 `python run.py` 完全等价
（两者都走 `wechat_mcp/cli.py`，参数和行为一致）。

> **为什么一定要写 `-i https://pypi.org/simple`**
> 明文 HTTP 的镜像源可以被中间人替换任意 wheel 包。加 `-i` 覆盖本机 `pip.conf`，
> 不需要改动系统配置。

依赖是锁死版本的，其中 `mcp==1.30.0` 不能放开：mcp 2.x 把 `FastMCP` 改名为
`MCPServer`，并把 `mcp.server.fastmcp` 变成了导入即抛异常的存根，装到 2.x 服务直接起不来。

## 部署一：stdio（本机使用）

由 MCP 客户端（Claude Desktop、Cursor、Claude Code 等）直接拉起子进程，
**不暴露任何网络端口，也不需要认证令牌**。

手工验证能否启动：

```bash
python run.py --transport stdio
```

然后在客户端配置里指向 `run.py` 的**绝对路径**（见[客户端接入](#客户端接入)）。

> stdio 模式不经过认证——客户端能拉起进程就说明已具备本地执行权限。
> 请不要把它误当成"有令牌保护"。

## 部署二：streamable-http（远程常驻）

服务装在远程 Linux 设备上跑常驻进程，其他机器通过 HTTP 调用。以下步骤假定
源码放在 **`/opt/wechat-mcp-server`**、虚拟环境在它的 `.venv` 子目录里；
路径不同就相应改 `wechat-mcp.service` 里的 `WorkingDirectory` 与 `ExecStart`。

### 1. 放源码

```bash
sudo chown -R root:root /opt/wechat-mcp-server
sudo chmod -R a+rX /opt/wechat-mcp-server
```

服务以专用账号运行，只需要读权限。

### 2. 建虚拟环境装依赖

```bash
sudo python3 -m venv /opt/wechat-mcp-server/.venv
sudo /opt/wechat-mcp-server/.venv/bin/pip install --upgrade pip \
  -i https://pypi.org/simple
sudo /opt/wechat-mcp-server/.venv/bin/pip install -r \
  /opt/wechat-mcp-server/requirements.txt -i https://pypi.org/simple
```

可选但推荐——预编译字节码：

```bash
sudo /opt/wechat-mcp-server/.venv/bin/python -m compileall -q \
  /opt/wechat-mcp-server/wechat_mcp
```

单元里设了 `ProtectSystem=strict`，`/opt` 对服务是只读的，运行时写不了
`__pycache__`。提前编好可以省掉每次启动的重复编译（不编也不报错）。

### 3. 创建运行用户

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin wechat-mcp
```

### 4. 写配置文件

生成认证令牌并**记下来**，客户端要用同一个：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
sudo install -d -m 0750 -o root -g wechat-mcp /etc/wechat-mcp
sudo install -m 0640 -o root -g wechat-mcp \
  /opt/wechat-mcp-server/deploy/wechat-mcp.env.example \
  /etc/wechat-mcp/wechat-mcp.env
sudo editor /etc/wechat-mcp/wechat-mcp.env
```

要填的内容：

| 变量 | 说明 |
|---|---|
| `WECHAT_MCP_ALLOWED_HOST` | **最容易出错的一项。** 必须等于客户端访问时用的主机名/IP，也就是 HTTP `Host` 头的值。客户端访问 `http://192.168.1.10:8000/mcp`，这里就填 `192.168.1.10`。填错服务会拒绝所有请求，甚至拒绝启动。 |
| `WECHAT_MCP_AUTH_TOKEN` | 填上面生成的令牌。至少 32 字符，否则启动会被拒绝。 |
| `WECHAT_APPID` / `WECHAT_APPSECRET` | 公众号凭证。不填则每次工具调用都要自己传。 |
| `WECHAT_MCP_LISTEN_HOST` / `_PORT` | 默认 `0.0.0.0` / `8000`，按需改。 |

> 这个文件是给 systemd 读的，**不是 shell 脚本**：不要加 `export`、不要加引号、
> 不要用 `$变量`，`#` 只在行首才是注释。写错了服务起不来，具体报错看
> `journalctl -u wechat-mcp`。

### 5. 安装 systemd 单元

```bash
sudo install -m 0644 /opt/wechat-mcp-server/deploy/wechat-mcp.service \
  /etc/systemd/system/wechat-mcp.service
sudo systemctl daemon-reload
```

### 6. 启动并验证

```bash
sudo systemctl enable --now wechat-mcp
systemctl status wechat-mcp
```

```bash
# 未带令牌应当返回 401，说明进程在监听且认证生效
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/mcp

# 列出工具（不需要公众号凭证）
WECHAT_MCP_URL=http://127.0.0.1:8000/mcp \
WECHAT_MCP_AUTH_TOKEN='<你的令牌>' \
/opt/wechat-mcp-server/.venv/bin/python \
  /opt/wechat-mcp-server/examples/http_client_usage.py
```

放行防火墙端口：

```bash
sudo ufw allow 8000/tcp     # 如启用了 ufw
```

端点路径为 **`/mcp`**。

### 安装后的文件布局

| 路径 | 说明 |
|---|---|
| `/opt/wechat-mcp-server/` | 源码与虚拟环境（root 所有，服务只读） |
| `/etc/wechat-mcp/wechat-mcp.env` | **全部可变配置**，`0640 root:wechat-mcp` |
| `/etc/systemd/system/wechat-mcp.service` | systemd 单元 |

配置与单元是分开的：改端口、令牌、访问地址都只动 `wechat-mcp.env`，
单元文件不用碰，升级时配置也不会被覆盖。

### 常用运维命令

```bash
systemctl status wechat-mcp          # 状态
journalctl -u wechat-mcp -f          # 跟踪日志
sudo systemctl restart wechat-mcp    # 重启（改完配置后执行）
sudo systemctl stop wechat-mcp       # 停止
sudo systemctl disable wechat-mcp    # 取消开机自启
```

### 升级

```bash
cd /opt/wechat-mcp-server && sudo git pull
sudo /opt/wechat-mcp-server/.venv/bin/pip install -r \
  /opt/wechat-mcp-server/requirements.txt -i https://pypi.org/simple
sudo /opt/wechat-mcp-server/.venv/bin/python -m compileall -q \
  /opt/wechat-mcp-server/wechat_mcp
sudo systemctl restart wechat-mcp
```

配置在 `/etc` 下，不会被 `git pull` 碰到。

### 加固调优

单元里的 `ProtectSystem=strict`、`MemoryDenyWriteExecute`、`SystemCallFilter`
等会限制进程能力。自定义代码或换了依赖后若启动失败，可以逐条注释掉
`wechat-mcp.service` 里 `# --- Hardening ---` 以下的行，再重新安装并重启：

```bash
sudo install -m 0644 /opt/wechat-mcp-server/deploy/wechat-mcp.service \
  /etc/systemd/system/wechat-mcp.service
sudo systemctl daemon-reload && sudo systemctl restart wechat-mcp
systemd-analyze security wechat-mcp   # 查看当前暴露面评分
```

当前配置的评分是 **1.9 OK**（满分 10，越低越好）。本仓库已在加固沙箱内实测过
DNS 解析与 TLS 握手，正常使用不需要动这部分。

### 卸载

```bash
sudo systemctl disable --now wechat-mcp
sudo rm /etc/systemd/system/wechat-mcp.service
sudo systemctl daemon-reload
sudo rm -rf /opt/wechat-mcp-server /etc/wechat-mcp
sudo userdel wechat-mcp
```

## 配置参考

配置来源优先级：**命令行参数 > 环境变量 > 代码默认值**。
所有默认值都取"安全的那一侧"：只监听回环、开启限流、不放宽图片限制。

### 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--transport` | `stdio` | `stdio` 或 `streamable-http` |
| `--host` | `127.0.0.1` | HTTP 监听地址 |
| `--port` | `8000` | HTTP 监听端口 |
| `--auth-token` | 读环境变量 | HTTP 传输的 Bearer 令牌 |
| `--allowed-host` | 空 | 允许的 `Host` 头，**可重复指定**；绑定非回环地址时必填 |
| `--requests-per-minute` | `30` | 每个凭证每分钟请求数，`-1` 表示不限制 |
| `--max-image-bytes` | `10485760` | 封面图最大字节数（10MB，与微信上限一致） |
| `--http-timeout` | `15.0` | 访问微信接口与下载图片的超时（秒） |
| `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

改端口后记得同步放行防火墙，并更新 `WECHAT_MCP_ALLOWED_HOST`（如果客户端用端口访问
时填的地址变了）。

### 环境变量

| 变量 | 被谁读取 | 说明 |
|---|---|---|
| `WECHAT_MCP_AUTH_TOKEN` | 服务进程 | HTTP 传输的 Bearer 令牌，也可用 `--auth-token` 传 |
| `WECHAT_APPID` | 服务进程 | 默认公众号 AppID |
| `WECHAT_APPSECRET` | 服务进程 | 默认公众号 AppSecret |
| `WECHAT_MCP_LISTEN_HOST` / `_PORT` | systemd 单元 | 展开成 `--host` / `--port` |
| `WECHAT_MCP_ALLOWED_HOST` | systemd 单元 | 展开成 `--allowed-host` |
| `WECHAT_MCP_URL` | `examples/http_client_usage.py` | 示例客户端要连的地址 |

### 启动期的 fail-safe 校验

绑定到非回环地址（如 `0.0.0.0`）时，以下两条缺一不可，否则**直接拒绝启动**（退出码 2）：

1. **必须配置 `WECHAT_MCP_AUTH_TOKEN`**（至少 32 字符）。
   没有认证就对外监听，等于把"用你的公众号发文"这个能力开放给整个网段。
2. **必须用 `--allowed-host` 声明客户端实际使用的主机名**（可重复）。
   上游 mcp SDK 只在 host 为 `127.0.0.1` / `localhost` / `::1` 时才自动开启
   DNS rebinding 防护；绑到 `0.0.0.0` 等地址时该防护为**关闭**状态，
   且不会有任何警告，所以这里强制要求显式声明。

## 客户端接入

### Claude Code

```bash
# 本机 stdio：-- 之后的参数才属于被拉起的服务进程，不能省
claude mcp add wechat \
  -e WECHAT_APPID=你的AppID -e WECHAT_APPSECRET=你的AppSecret \
  -- python /绝对路径/wechat-mcp-server/run.py --transport stdio

# 远程 HTTP
claude mcp add --transport http wechat \
  http://192.168.1.10:8000/mcp \
  --header "Authorization: Bearer <你的令牌>"
```

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
      "url": "http://192.168.1.10:8000/mcp",
      "headers": {
        "Authorization": "Bearer 你的令牌"
      }
    }
  }
}
```

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

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 服务起不来，`status=2` | 启动期 fail-safe 拒了配置：令牌缺失/短于 32 字符、没声明 `--allowed-host`、或 `wechat-mcp.env` 语法错误（加了引号或 `export`）。看 `journalctl -u wechat-mcp -n 30 --no-pager` |
| 反复重启，`status=1` 且无 Python 报错 | `ExecStart` 路径不对，虚拟环境不在 `/opt/wechat-mcp-server/.venv` 或 `run.py` 位置变了。用 `systemctl cat wechat-mcp` 核对 |
| 客户端收到 **401** | 令牌不一致。`curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <令牌>" http://127.0.0.1:8000/mcp`：返回 400 说明令牌对了（已进到 MCP 层，只是 GET 姿势不对），返回 401 说明令牌不对 |
| 客户端收到 **421 / 400**，日志有 DNS rebinding 或 Invalid Host | `WECHAT_MCP_ALLOWED_HOST` 与客户端实际用的地址不一致。它比对的是**主机名**，不带 `http://`、不带端口 |
| 工具调用返回 **40164** | 微信 IP 白名单没配。把**服务器出口 IP** 加进「设置与开发 → 开发接口管理 → IP 白名单」 |
| 工具调用返回 **48001** | 账号没有该接口权限，与部署无关。发布类接口在个人主体、未认证企业主体账号上会被回收 |

## 授权

MIT，见 [LICENSE](LICENSE)。
