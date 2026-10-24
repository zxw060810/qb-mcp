# qb-mcp

**qBittorrent 的 MCP 服务器 —— 用自然语言管理种子。**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-streamable%20http%20%7C%20stdio-8b5cf6.svg)](https://modelcontextprotocol.io)

> 本项目非官方项目，与 qBittorrent 团队无关，也未获其背书或支持。

**English** | [简体中文](README.zh-CN.md)

`qb-mcp` 封装 qBittorrent WebUI API v2，把它变成 11 个 AI 智能体可以调用的工具。
它支持**任意多个实例**，每个实例有自己的账号密码，所以一个智能体可以同时管理家里的 NAS 和远程盒子。

## 为什么做这个

qBittorrent 有完整的 HTTP API，但没有 MCP 接口。接上之后，智能体回答"为什么不上传"时可以**真的去查**
tracker 状态、监听端口、限速和 peer 供给，而不是猜。

11 个工具按**用户意图**分组，而不是按 API 端点映射：把 80 多个端点一一对应会产生一个模型根本选不过来的工具列表。

## 特性

- **多实例**，每个实例独立账号密码
- **`qb_diagnose`** —— 把"为什么没上传"变成一次调用
- **字段裁剪** —— API 每个种子返回 30 多个字段，这里只返回 12 个
- **写操作回显改前 → 改后**
- **`delete_files` 默认关闭** —— 删种子绝不默认删数据
- **stdio 与 HTTP 双传输**
- 凭据不出本机；任何敏感信息都不写日志

## 快速开始

```bash
pip install qb-mcp
cp instances.example.json .instances.json
# 编辑 .instances.json，填入你的 qBittorrent 地址和凭据
chmod 600 .instances.json
qb-mcp
```

## 配置

`qb-mcp` 从当前目录读取 `.instances.json`，或读取 `QB_MCP_CONFIG` 环境变量指定的路径。

```json
{
  "instances": {
    "nas": {
      "label": "家里 NAS",
      "url": "http://192.168.1.100:8080",
      "username": "admin",
      "password": "${QB_PASS_NAS}"
    },
    "seedbox": {
      "url": "https://seedbox.example.com",
      "username": "me",
      "password": {"file": "~/.secrets/seedbox"}
    }
  }
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `url` | ✅ | — | 必须以 `http://` 或 `https://` 开头 |
| `username` | ✅ | — | 每个实例独立，可以不同 |
| `password` | ✅ | — | 明文、`"${环境变量}"` 或 `{"file": "/路径"}` |
| `label` | — | 实例 key | 显示名 |
| `verify_ssl` | — | `true` | 自签证书设为 `false` |
| `timeout` | — | `8.0` | 单请求超时（秒） |

**务必把 `.instances.json` 排除在版本控制之外** —— `.gitignore` 里已经写好了。
如果文件对同组或其他用户可读，`qb-mcp` 会打印警告。

## 工具

### 读（安全）

| 工具 | 用途 |
|---|---|
| `qb_instances` | 列出已配置实例，以及各自是否可达 |
| `qb_overview` | 一个或全部实例的全局状态 |
| `qb_list` | 种子列表，支持过滤、排序、行数限制 |
| `qb_detail` | 单个种子的详细信息，含 tracker 状态 |
| `qb_diagnose` | 解释某实例为什么在上传 / 为什么不上传 |

### 写（改变状态）

| 工具 | 用途 |
|---|---|
| `qb_control` | `pause` / `resume` / `reannounce` / `force_start` |
| `qb_set_limit` | 全局或单种的上下行限速 |
| `qb_set_share` | 分享率与做种时长上限 |
| `qb_add` | 从磁力、URL 或本地 `.torrent` 添加（默认暂停） |
| `qb_remove` | 删除种子；`delete_files` 需要显式开启 |
| `qb_category` | 设置分类与标签 |

## 客户端接入

**stdio**（Claude Desktop、Cursor 等本地客户端）：

```json
{
  "mcpServers": {
    "qbittorrent": {
      "command": "qb-mcp",
      "env": { "QB_MCP_CONFIG": "/绝对路径/.instances.json" }
    }
  }
}
```

**HTTP**（远程或多客户端）：

```bash
qb-mcp --transport http --host 127.0.0.1 --port 17890
```

```json
{
  "servers": {
    "qbittorrent": {
      "url": "http://127.0.0.1:17890/mcp",
      "transport": "streamable_http"
    }
  }
}
```

## 兼容性

| qBittorrent | WebUI API | 状态 |
|---|---|---|
| 4.x | 2.8+ | 支持 |
| 5.x | 2.11+ | 支持（在 5.2.4 上验证过） |

官方发行版和第三方 NAS 包（QNAP QPKG、群晖 SPK）都能用，下面的反代问题已经处理。

## 常见问题

### 提示 `login rejected (Fails.)`

qBittorrent **密码错误时也返回 HTTP 200** —— 只是 body 是 `Fails.` 而不是 `Ok.`。
`qb-mcp` 会检查 body，所以看到这条说明凭据确实错了。

### 挂在反向代理后面登录失败

官方 API 要求 `Referer` 或 `Origin` 与请求的 `Host` **完全一致（包括端口）**。
`qb-mcp` 用配置里的 `url` 自动设置这两个头，所以请确保 `url` 用的主机和端口与你在浏览器里输入的一致。

### 用 curl 能通，但走反代不行

部分反向代理（QNAP QTS 就是其中一个）登录成功时返回 **`204 No Content` 空 body**，而不是 `200 Ok.`。
`qb-mcp` 把"状态码小于 400 且 body 为空或 `Ok.`"都当作成功。

### 提示 `login blocked - too many failed attempts`

qBittorrent 在多次失败后会临时封禁来源 IP。等几分钟，或者去 WebUI 的封禁列表里解封。

## 开发

```bash
git clone https://github.com/zxw060810/qb-mcp.git
cd qb-mcp
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check .
mypy src
pytest
```

测试全部使用 mock，不需要在线的 qBittorrent 实例。

## 许可

[Apache-2.0](LICENSE)
