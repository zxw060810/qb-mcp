# v0.2.0 修复方案（待执行）

| 项 | 值 |
|---|---|
| 项目 | `qb-mcp` |
| 文档版本 | 1.0 |
| 目标版本 | v0.2.0 |
| 依据 | [`COMPATIBILITY.md`](./COMPATIBILITY.md)、[`compatibility.json`](./compatibility.json) |
| 覆盖问题 | 外部 issue #1–#5，加审计新发现 3 项 |
| 最后更新 | 2026-11-04 |
| 状态 | **待评审，代码未改动** |

---

## 1. 缺陷清单

| 编号 | 问题 | 严重度 | 来源 |
|---|---|---|---|
| D1 | `qb_control` 在 5.x 上暂停/恢复 404 | 高 | issue #2 |
| D2 | `qb_set_limit` 全局写入打错端点，静默无效；读回字段不存在 | 高 | issue #3 |
| D3 | `qb_set_share` 缺必需参数，**两代版本都 400，功能 100% 失效** | 高 | 审计新发现 |
| D4 | `qb_set_share` 时长单位按秒传，实际是分钟（放大 60 倍） | 高 | 审计新发现 |
| D5 | `qb_add` 加种暂停字段在 5.x 被忽略，种子会直接开始下载 | 高 | 审计新发现 |
| D6 | `qb_list` 筛选值透传，未知值返回全部（静默欺骗） | 中 | issue #2 附带 |
| D7 | `qb_detail` 输出 tracker 链接前 60 字符，泄露密钥前缀 | 中 | issue #1 |
| D8 | 实盘测试只覆盖只读操作，写路径零覆盖 | 中 | issue #4 |
| D9 | `mcp` 依赖无上限，跨两大版本使用私有 API | 低 | issue #5 |

---

## 2. 新增文件

### 2.1 `src/qb_mcp/capability.py`

```python
@dataclass(frozen=True)
class Capabilities:
    version: tuple[int, int, int]
    pause_route: str        # 4.6: torrents/pause   | 5.x: torrents/stop
    resume_route: str
    add_stop_field: str     # 4.6: paused           | 5.x: stopped
    paused_filter: str      # 4.6: paused           | 5.x: stopped
```

- `parse_version("v5.2.4") -> (5,2,4)`，容忍 `v4.6.4` / `5.2.4` / 带后缀的 `v5.0.0beta1`，异常返回 `None` 并降级为 4.x 行为 + 输出警告。
- `capabilities_for(version)` 按主次版本判定（≥5.0 换路径与字段名）。
- **不硬编码 5.2/5.3 的参数差异**：分享限制走"超集参数"，多出来的字段由服务端忽略。

### 2.2 `src/qb_mcp/limits.py`（单位换算，可单测）

- `bytes_per_second(value)`：接口入参统一字节/秒。
- `minutes(seconds_or_minutes, unit)`：`qb_set_share` 对外单位改为**分钟**（与官方一致）。
- `normalize_limit(value)`：`0` / `-1` / `None` 一律归一化为"不限"，供读回比对使用。

### 2.3 `scripts/audit_compat.py`（随仓库开源）

把本次一次性审计固化成可重跑的脚本：

- 输入：一个或多个 `release-x.y.z` tag（默认扫 4.6.0 起的全部稳定 tag）；
- 抽取：`torrentscontroller.cpp`（路由、加种字段、`setShareLimits` 必需参数）、`serialize_torrent.cpp`（状态字符串）、`torrentfilter.cpp`（筛选词）、`transfercontroller.cpp`（全局限速端点）；
- 完整性校验不通过时写 `unavailable` 并以非零退出码结束，绝不产出推断值；
- 输出：`docs/compatibility.json`，格式与当前文件一致，供契约测试直接消费；
- 用途：qBittorrent 发新版后重跑一次，测试会自动指出实现落后于哪一段分叉。

---

## 3. 修改清单（按函数）

### `client.py`

| 改动 | 说明 |
|---|---|
| `QBError` 携带响应体 | 截断 200 字符。当前 5.x 的 `缺少必需参数：inactiveSeedingTimeLimit, shareLimitAction` 完全看不到 |
| 新增 `webapi_version()` | 取 `/api/v2/app/webapiVersion`，仅用于日志与诊断输出 |
| 新增 `post_verified(path, data, readback)` | 写→读→归一化比对，不一致抛 `QBError`，不返回虚假成功 |

### `server.py`

| 工具 | 现状 | 改为 |
|---|---|---|
| `qb_control` | 硬编码 `pause`/`resume` | 按 `Capabilities` 选路径；返回文案带上实际使用的能力档位；`action` 对外名称不变 |
| `qb_list` | 筛选值直接透传 | 白名单常量 `FILTERS`；`paused` 别名翻译为当前版本的 `paused_filter`；未知值返回 `ERROR: filter must be one of ...` |
| `qb_set_limit` | 写 `transfer/uploadLimit`，读 `up_limit` | 写 `transfer/setUploadLimit`、`setDownloadLimit`；读 `up_rate_limit`、`dl_rate_limit`；写后读回并归一化比对 |
| `qb_set_share` | 3 参数、秒、无校验 | 参数超集（hashes/ratioLimit/seedingTimeLimit/inactiveSeedingTimeLimit/shareLimitAction/shareLimitsMode）；单位为**分钟**；新增 `inactive_seeding_time`（默认 -2）与 `action`（仅 `stop` / `enable_super_seeding`，默认 `stop`）；写后读 `properties` 校验 |
| `qb_add` | 只发 `paused` | 同时发 `paused` 与 `stopped`；加完按名字查一次，状态与预期不符时在输出里给出警告 |
| `qb_detail` | `url[:60]` | 新增 `redact_tracker_url()`：只保留 `scheme://host`；新增参数 `show_tracker_urls: bool = False` |

### 输出文案

- 所有"改前 → 改后"使用归一化后的值，避免 `0 → -1` 被读成"没生效"。
- `qb_diagnose` 里"上传限速"读的是 `preferences.up_limit`（会话/备用限速，单位 KiB/秒，`-1` 不限），与 `transfer/info` 的全局限速是**两个不同设置**；输出里明确标注是哪一个，不混用。

---

## 4. 测试方案

### 4.1 单元测试（离线，可穷举）

| 用例 | 内容 |
|---|---|
| `TC-CAP-*` | 版本解析 6 例（`v4.6.4` / `4.6.7` / `v5.0.0` / `v5.2.4` / `v5.3.0` / 垃圾值） |
| `TC-CAP-*` | 四段能力表：给定版本 → 路径、字段名、筛选词 |
| `TC-CTR-*` | 4.6 与 5.x 各断言一次 `qb_control` 发出的路径 |
| `TC-LIM-*` | 全局限速：端点名、读回字段、`0/-1` 归一化、写后不一致时报错 |
| `TC-SHR-*` | 分享限制：参数超集（6 个）、单位换算（60 → `seedingTimeLimit=60` 分钟）、哨兵 `-2/-1` |
| `TC-ADD-*` | 加种：两个字段同时发；5.x 上 `stopped=true` |
| `TC-LST-*` | 筛选：`paused` 在 5.x 翻译为 `stopped`；未知值报错 |
| `TC-SEC-*` | 脱敏：`https://tracker.example/announce/SECRET_PASSKEY?credential=xyz` 输出不得含 `SECRET`/`credential`；`show_tracker_urls=True` 时才出现 |
| `TC-CON-*` | 契约测试：读 `docs/compatibility.json`，逐 tag 断言实现选出的能力与表一致 |

### 4.2 实盘测试（容器矩阵，覆盖四段）

| 容器 tag | 覆盖分叉 | 必须跑的断言 |
|---|---|---|
| `4.6.7` | 第一段 | `pause`/`resume` 路径；`paused` 筛选；`paused` 加种字段；4 参数分享限制 |
| `5.1.4` | 第二段 | `stop`/`start` 路径；`stopped` 筛选与字段；4 参数分享限制 |
| `5.2.4` | 第三段 | 上面 + 5 参数分享限制生效 |
| `5.3.0` | 第四段 | 上面 + 6 参数分享限制生效 |

每档跑同一条闭环（测试种子自制，`announce` 指向 `127.0.0.1:1`，不联网、不产生下载流量）：

1. 加种（暂停）→ 列表确认存在，状态属于"停止"族
2. 开始 → 状态属于"运行"族；停止 → 回到停止族
3. 单种限速 → 读回等于设定值
4. 全局限速 → 记录原值、设置、读回、**还原**
5. 分享限制 → 不再 400，`properties` 内的值符合
6. 分类 + 标签 → 读回
7. 删除（含文件）→ 列表中消失
8. 清理放进 fixture 的 finalizer，失败也执行

**每个写操作后必须读回**——这是唯一能抓住"返回 200 但没生效"的手段。

### 4.3 门禁

- `scripts/env_check.py --expect-tests` 更新为新总数。
- CI 增加 `live` 矩阵（四个 tag），失败不重试、视为真实回归。
- `ruff` / `mypy` 保持现有强度。

---

## 5. 版本与发布

| 项 | 内容 |
|---|---|
| 版本号 | `0.2.0` |
| Breaking 变更 | ① `qb_set_share` 的 `seeding_time` 单位由秒改为分钟 ② `qb_list` 未知筛选值改为报错 ③ `qb_detail` 默认隐藏 tracker 路径 |
| CHANGELOG | 三条 breaking 单列，附迁移说明 |
| 发布流程 | 本地分支 → 全量测试 → 提交 → 由你确认后推远程与打 tag |

---

## 6. 风险与回滚

| 风险 | 处理 |
|---|---|
| 现有调用方按"秒"传时长，升级后行为变化 | CHANGELOG 明示；版本号走次版本；必要时保留 `seeding_time_seconds` 兼容参数一个版本 |
| 筛选白名单过严，用户传了文档里有但实现漏的筛选值 | 白名单取自 `compatibility.json` 的并集，测试逐 tag 校验，不会少 |
| 5.3.0 之后继续加必填参数 | 超集策略覆盖"多余参数"，但新增必填项必须重扫才能发现 → 把审计脚本收进仓库 `scripts/audit_compat.py`，升级 qBittorrent 时重跑 |
| 打包差异导致与源码不符 | 实盘矩阵保留；任一档失败即视为回归，不重试 |

---

## 7. 已定决策（2026-11-04 评审）

### 7.1 审计脚本随仓库开源

`scripts/audit_compat.py` 纳入仓库，任何人可对任意 qBittorrent tag 重跑，产出/更新 `docs/compatibility.json`。

要求：

- 完整性校验（HTTP 200 + 最小字节数 + 关键符号），不满足记 `unavailable`，**禁止填推断值**；
- 抽取失败退出码非零，避免"静默产出残缺表"；
- 支持 `--tags release-4.6.7 release-5.3.0` 指定范围、`--out docs/compatibility.json`；
- 脚本本身不联网以外的副作用，不接触任何用户实例。

### 7.2 `qb_set_share` 只暴露安全旋钮

| 分享限制控制项 | 是否暴露 | 说明 |
|---|---|---|
| `ratioLimit` | 暴露（`ratio`） | 现已支持 |
| `seedingTimeLimit` | 暴露（`seeding_time`，分钟） | 单位修正 |
| `inactiveSeedingTimeLimit` | **暴露**（`inactive_seeding_time`，分钟，默认 `-2` = 用全局） | 只是上限，无破坏性 |
| `shareLimitAction` | **仅暴露 `stop` / `enable_super_seeding`**（`action`，默认 `stop`） | 达标后的删除动作不在此工具 |

`remove` / `remove_with_content` 不作为 `qb_set_share` 的取值：删除是破坏性且不可逆的操作，必须走 `qb_remove(delete_files=...)`，让动作在工具名上就可见。若将来确有"达标即删种"的批量需求，再加 `action=remove*` 并强制要求 `confirm_delete=true` 二次确认，不默认开放。
