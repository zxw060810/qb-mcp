# qBittorrent WebUI 接口兼容性说明

| 项 | 值 |
|---|---|
| 项目 | `qb-mcp` |
| 文档版本 | 1.0 |
| 适用版本 | `qb-mcp` v0.2.0 起 |
| 数据来源 | `qbittorrent/qBittorrent` 源码逐 tag 审计 |
| 机器可读表 | [`compatibility.json`](./compatibility.json) |
| 最后更新 | 2026-11-05 |

---

## 1. 为什么需要这份文档

qBittorrent 的 WebUI 接口在 5.0 和 5.2、5.3 三个版本上各改了一批名字，而**官方 wiki 有版本断层且存在错误**（5.0 页写的 `torrents/add` 参数 `paused`，5.0.0 源码实际只读 `stopped`）。因此本项目的接口契约**以源码为准**，逐 tag 抽取后固化在 `compatibility.json`，测试从该文件生成，不再手写。

审计范围：25 个稳定 tag —— 4.6.0–4.6.7 / 5.0.0–5.0.5 / 5.1.0–5.1.4 / 5.2.0–5.2.4 / 5.3.0。

抽取时的完整性校验：HTTP 200 + 最小字节数 + 关键符号存在，任一不满足记 `unavailable`，**不填推断值**。

---

## 2. 分叉点：四段

| 版本区间 | 加种暂停字段 | 控制端点 | 筛选词（不含 `all`） | 分享限制必需参数 | 状态字符串 |
|---|---|---|---|---|---|
| 4.6.0 起 | `paused` | `pause` `resume` | `paused` `resumed` + 其余 | 4 个 | `pausedUP` `pausedDL` |
| 5.0.0 起 | `stopped` | `stop` `start` | `stopped` `running` + 其余 | 4 个 | `stoppedUP` `stoppedDL` |
| 5.2.0 起 | `stopped` | `stop` `start` | 同上 | **5 个**（+`shareLimitAction`） | `stoppedUP` `stoppedDL` |
| 5.3.0 起 | `stopped` | `stop` `start` | 同上 | **6 个**（+`shareLimitsMode`） | `stoppedUP` `stoppedDL` |

### 分享限制必需参数的逐段清单

| 版本区间 | 必需参数 |
|---|---|
| 4.6.0 起 | `hashes` `inactiveSeedingTimeLimit` `ratioLimit` `seedingTimeLimit` |
| 5.2.0 起 | 上述 + `shareLimitAction` |
| 5.3.0 起 | 上述 + `shareLimitsMode` |

### 4.6.7 → 5.3.0 全程未变（不需要版本分支）

- `torrents/setUploadLimit` / `setDownloadLimit`：必需 `hashes` `limit`
- `torrents/delete`：必需 `hashes` `deleteFiles`
- `torrents/setCategory`：必需 `hashes` `category`
- `torrents/addTags`：必需 `hashes` `tags`
- `torrents/setForceStart`：必需 `hashes` `value`
- `torrents/reannounce`：必需 `hashes`
- `transfer/setUploadLimit` / `setDownloadLimit`：必需 `limit`

---

## 3. 单位与哨兵值

| 字段 | 单位 | 说明 |
|---|---|---|
| `transfer/setUploadLimit`、`setDownloadLimit` 的 `limit` | 字节/秒 | 传 `0` 会被服务端改写成 `-1`（不限） |
| `torrents/setUploadLimit`、`setDownloadLimit` 的 `limit` | 字节/秒 | 同上 |
| `setShareLimits.seedingTimeLimit` | **分钟** | 源码 `maxSeedingTimeValue * 60` 换算成秒比较 |
| `setShareLimits.inactiveSeedingTimeLimit` | **分钟** | 同上 |
| `setShareLimits.ratioLimit` | 倍数（浮点） | `-2` = 用全局值，`-1` = 不限 |
| `transfer/info` 的 `up_rate_limit`、`dl_rate_limit` | 字节/秒 | `<= 0` 表示不限 |

### 枚举（`src/base/bittorrent/sharelimits.h`）

| 枚举 | 取值 | 起始版本 |
|---|---|---|
| `ShareLimitAction` | `Default=-1` `Stop=0` `Remove=1` `EnableSuperSeeding=2` `RemoveWithContent=3` | 5.2.0 起成为必填 |
| `ShareLimitsMode` | `Default=-1` `MatchAny=0` `MatchAll=1` | 5.3.0 起成为必填 |

---

## 4. 三条硬规则

### 规则一：路径探测，参数超集

- **路径**（`pause` vs `stop`）无法用超集绕过，必须按版本探测后二选一。
- **参数**可以超集：`requireParams` 只校验必需项存在，多余参数被忽略。因此分享限制永远发送当前已知的最新超集（5.3.0 的六个参数），4.6/5.0/5.1/5.2 会安静忽略多出来的字段。加种同理，`paused` 与 `stopped` 两个字段一起发。

已验证：4.6.4 收到 5 个参数返回 200；4.6.4 收到 `stopped` 字段不报错（只是忽略）。

### 规则二：筛选值必须客户端白名单

服务端对**未知**筛选值不报错，而是**返回全部**。因此：

- `paused` 在 5.x 上是未知值 → 若直接透传，会拿到全部种子（而不是空列表），这是最危险的静默失败。
- 客户端维护白名单，`paused` 作为别名翻译成 `stopped`；未知值直接报错，不透传。

### 规则三：写后读回，且要先归一化

`setUploadLimit` 等写接口对**所有情况**都返回 200（官方文档原文 "Returns 200 / All scenarios"），200 不代表生效。因此每个写操作必须读回校验；但读回前要把 `0` 与 `-1` 都归一化成"不限"，否则必然误报。

---

## 5. 这份表怎么用

1. `src/qb_mcp/capability.py` 从实例的 `app/version` 解析主次版本，按上表选出路径与字段名。
2. `tests/test_contract.py` 直接读 `compatibility.json`，逐 tag 断言：给定版本 → 应选中的路径/字段/必需参数集合。
3. 实盘测试的容器矩阵按四段各取一个 tag（4.6.7 / 5.1.4 / 5.2.4 / 5.3.0）。
4. qBittorrent 发新版时，重跑审计脚本更新 `compatibility.json`，测试会自动要求实现跟上。

---

## 6. 限速的三个来源（别混用）

| 概念 | 接口 | 单位 | 语义 |
|---|---|---|---|
| 全局限速（生效值） | `transfer/info` 的 `up_rate_limit` / `dl_rate_limit` | 字节/秒 | 当前真正生效的上限（备用模式开启时是备用值） |
| 全局限速（正常模式） | `preferences` 的 `up_limit` / `dl_limit` | 字节/秒 | 关闭备用模式时使用的值 |
| 备用限速 | `preferences` 的 `alt_up_limit` / `alt_dl_limit` | 字节/秒 | 备用模式开启时使用的值 |

> 官方 wiki 写的是「KiB/s」，但源码（`appcontroller.cpp` 直接读写
> `session->globalUploadSpeedLimit()`）表明实际单位是**字节/秒**。本项目按字节处理。

`qb_set_limit` 的 `mode` 参数决定改哪一个：`normal`（默认）改正常模式，`alternative`
改备用限速（走 `app/setPreferences`）。备用模式开启时写入全局限速不会立刻改变实际速率，
工具会给出说明而不是报错。

## 7. 已知边界

- 只覆盖 **4.6.0 及以上**；4.5 及更早未审计，不在支持范围。
- 审计脚本抽取的是**源码事实**，不包含打包差异（发行版可能自行打补丁），因此仍需实盘验收。
- 5.3.0 之后若继续新增必填参数，本文档的超集策略仍然有效（多余参数被忽略），但**新增的必填项**必须重扫才能发现。
