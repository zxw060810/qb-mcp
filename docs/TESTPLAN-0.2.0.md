# v0.2.0 测试方案

| 项 | 值 |
|---|---|
| 项目 | `qb-mcp` |
| 文档版本 | 1.0 |
| 目标版本 | v0.2.0 |
| 配套文档 | [`PLAN-0.2.0.md`](./PLAN-0.2.0.md)、[`COMPATIBILITY.md`](./COMPATIBILITY.md)、[`TESTLIST.md`](./TESTLIST.md) |
| 最后更新 | 2026-11-04 |
| 状态 | 待评审 |

---

## 1. 验收标准（先定"什么叫做完了"）

| 编号 | 标准 | 判定方式 |
|---|---|---|
| AC-1 | 9 个缺陷每个至少有一条**在旧代码上会失败**的用例 | 检出旧代码跑一遍，用例必须标红，并记录失败原因 |
| AC-2 | 四段版本分叉全部被覆盖 | 契约测试逐 tag 断言 + 实盘四档容器各一次 |
| AC-3 | 每个写操作都有"写后读回"断言 | 静态检查：写工具的测试必须读取返回体并比对 |
| AC-4 | 离线测试全绿，覆盖率不低于 0.1.2 | `pytest --cov` 与基线对比，不得下降超过 1 个百分点 |
| AC-5 | 实盘四档全绿，全部在 GitHub Actions 上完成 | 四个 `live` 作业的绿色记录，附运行链接 |
| AC-6 | 环境门禁数值同步更新 | `env_check.py --expect-tests` 与实际收集数一致 |
| AC-7 | 本地不启动任何容器、不连接任何真实实例 | 工作日志与 `docker ps` 无新增；两台真实实例的种子数与全局限速保持审计完成时的数值 |

### 1.1 本地与 CI 的分工（硬约束）

| 层 | 在哪跑 | 说明 |
|---|---|---|
| L0 环境门禁、L1–L4 离线用例 | **本地** | 只调 Python，不碰网络、不启容器 |
| L5 实盘用例 | **仅 GitHub Actions** | 四档容器由 CI 拉起与销毁 |
| 真实实例（用户的 fnos / QNAP） | **任何阶段都不碰** | 发布后由用户自行升级，届时日常使用即是最真实的回归 |

---

## 2. 工作流：先红后绿（这条是整套方案的地基）

不允许"改完代码再补测试"。顺序固定：

1. 先把每条新用例写在**未修改的代码**上运行，确认它**失败**，并记录失败原因（404 / 400 / 值不符 / 泄露）。
   - 离线用例：本地跑，立刻能看到红。
   - 实盘用例：推分支开 PR，由 CI 跑，红的结果以作业链接为证（见 §6.1）。
2. 失败原因与预期缺陷编号对应上（D1…D9），否则说明测试写错了。
3. 再改实现，用例转绿。
4. 每条用例在 `TESTLIST.md` 里注明"红→绿的证据"（失败时的一句话现象 + 对应 CI 作业链接）。

理由：如果测试和实现同时写，两边错在同一处时测试照样通过——这正是 0.1.2 里"断言了错误端点却能全绿"的原因。

---

## 3. 测试分层（沿用现有 L0–L5，不新造体系）

| 层 | 前缀 | 数量（新增/改） | 回答的问题 |
|---|---|---|---|
| L0 环境门禁 | `TC-ENV` | 5（改 1） | 这次跑测试的环境本身可信吗 |
| L1–L3 单元 | `TC-CAP` `TC-CTR` `TC-LIM` `TC-SHR` `TC-ADD` `TC-LST` `TC-SEC` `TC-CON` | 新增约 58 | 给定版本与输入，发出的请求与输出对不对 |
| L4 协议冒烟 | `TC-SMK` | 4（不变） | 包还能作为 MCP 服务被拉起吗 |
| L5 实盘 | `TC-LIVE` | 新增 12×4 档 | 对着真实 qBittorrent，功能真的生效吗 |

---

## 4. 单元层用例（离线，全部可穷举）

### 4.1 能力判定 `TC-CAP`（覆盖 D1、D3、D5、D6）

| 编号 | 输入 | 期望 | 覆盖 |
|---|---|---|---|
| TC-CAP-01 | `v4.6.4` | `pause_route=torrents/pause`、`add_stop_field=paused`、`paused_filter=paused` | D1 D5 D6 |
| TC-CAP-02 | `4.6.7`（无 v 前缀） | 同上 | 解析健壮性 |
| TC-CAP-03 | `v5.0.0` | `stop` / `stopped` / `stopped` | D1 D5 D6 |
| TC-CAP-04 | `v5.2.4` | 同 5.0 | — |
| TC-CAP-05 | `v5.3.0` | 同 5.0 | — |
| TC-CAP-06 | `v5.0.0beta1` | 按 5.0 处理 | 后缀容忍 |
| TC-CAP-07 | `""` / `"unknown"` | 返回空能力 + 标记 unknown，调用方必须走"显式警告"分支 | 失败可见 |

### 4.2 控制端点 `TC-CTR`（覆盖 D1）

| 编号 | 版本 | 期望发出的路径 |
|---|---|---|
| TC-CTR-01 | 4.6 | `torrents/pause` |
| TC-CTR-02 | 4.6 | `torrents/resume` |
| TC-CTR-03 | 5.2 | `torrents/stop` |
| TC-CTR-04 | 5.2 | `torrents/start` |
| TC-CTR-05 | 任一 | `reannounce` / `force_start` 不变 |
| TC-CTR-06 | 空种子列表 | 不发出任何请求，返回提示 |

### 4.3 限速 `TC-LIM`（覆盖 D2）

| 编号 | 场景 | 期望 |
|---|---|---|
| TC-LIM-01 | 全局上传 | POST `transfer/setUploadLimit`，参数 `limit=<bytes>` |
| TC-LIM-02 | 全局下载 | POST `transfer/setDownloadLimit` |
| TC-LIM-03 | 读回 | 读 `transfer/info` 的 `up_rate_limit` / `dl_rate_limit`，不读 `up_limit` |
| TC-LIM-04 | 归一化 | 设定 0，读回 0 或 -1，均判为"不限"，不报错 |
| TC-LIM-05 | 静默失败 | 假客户端读回值不变 → 必须返回 `ERROR:`，不得返回"已更新" |
| TC-LIM-06 | 单种限速 | 走 `torrents/setUploadLimit`，参数含 `hashes` |
| TC-LIM-07 | 未给参数 | `up` 与 `dl` 皆空 → 报错 |

### 4.4 分享限制 `TC-SHR`（覆盖 D3、D4）

| 编号 | 场景 | 期望 |
|---|---|---|
| TC-SHR-01 | 最小调用（只给 ratio） | 请求体**同时**包含 6 个参数（超集） |
| TC-SHR-02 | 时长换算 | 入参 60（分钟）→ 请求体 `seedingTimeLimit=60`，不做 ×60 |
| TC-SHR-03 | 闲置时长默认 | 未指定 → `inactiveSeedingTimeLimit=-2` |
| TC-SHR-04 | 动作默认 | 未指定 → `shareLimitAction=stop` 对应枚值，且**不得**出现 `remove*` |
| TC-SHR-05 | 危险动作 | `action=remove_with_content` → 直接拒绝并提示改用 `qb_remove` |
| TC-SHR-06 | 哨兵 | ratio=-1 / -2 原样透传，不被换算 |
| TC-SHR-07 | 缺失 hashes | 报错，不发请求 |
| TC-SHR-08 | 写后校验 | 读 `properties` 的 `ratio_limit`/`seeding_time_limit` 与设定一致；不一致报错 |

### 4.5 加种 `TC-ADD`（覆盖 D5）

| 编号 | 场景 | 期望 |
|---|---|---|
| TC-ADD-01 | 4.6 加种（默认暂停） | 表单含 `paused=true`，也含 `stopped=true`（超集，被忽略无妨） |
| TC-ADD-02 | 5.x 加种（默认暂停） | 表单含 `stopped=true` |
| TC-ADD-03 | 5.x `paused=False` | `stopped=false`，不得残留 `paused` |
| TC-ADD-04 | 加完状态不符 | 假客户端返回 `downloading` 而请求是暂停 → 输出必须带警告，不得声称成功 |
| TC-ADD-05 | 本地文件不存在 | 报错，不发请求 |

### 4.6 列表筛选 `TC-LST`（覆盖 D6）

| 编号 | 场景 | 期望 |
|---|---|---|
| TC-LST-01 | 4.6 传 `paused` | 透传 `filter=paused` |
| TC-LST-02 | 5.x 传 `paused` | 翻译为 `filter=stopped` |
| TC-LST-03 | 5.x 传 `stopped` | 原样透传 |
| TC-LST-04 | 传 `zzz` | 报错且**不发请求**（防"返回全部"） |
| TC-LST-05 | 大小写 | `PAUSED` 归一化为小写后处理 |

### 4.7 脱敏 `TC-SEC`（覆盖 D7）

| 编号 | 场景 | 期望 |
|---|---|---|
| TC-SEC-01 | `https://tracker.example/announce/SECRET_PASSKEY?credential=xyz` | 输出只含 `https://tracker.example`，不含 `SECRET`、`PASSKEY`、`credential` |
| TC-SEC-02 | `show_tracker_urls=True` | 才输出完整链接 |
| TC-SEC-03 | 非法/空 URL | 不抛异常，显示占位符 |
| TC-SEC-04 | 状态与 peers | 脱敏后 `status`、`num_peers` 仍保留（诊断可用） |

### 4.8 契约测试 `TC-CON`（覆盖 AC-2）

从 `docs/compatibility.json` 逐 tag 生成断言：

- 该 tag 的能力判定结果必须与表中 `control_endpoints`、`add_pause_field`、`share_limit_required`、`state_strings` 一致；
- 表中 `share_limit_required` 的每一项，实现发出的请求体必须包含；
- 实现输出的状态标签必须能覆盖该 tag 的 `state_strings`（不漏档）；
- 新增 tag 时自动多一条用例，无需改代码。

---

## 5. 实盘层用例（`TC-LIVE`，四档容器）

### 5.1 矩阵

| 容器 tag | 覆盖分叉 | 必须额外断言 |
|---|---|---|
| `4.6.7` | 第一段 | `pause` 路径有效；`paused` 加种真的停住；4 参数分享限制生效 |
| `5.1.4` | 第二段 | `stop` 路径有效；`stopped` 加种真的停住 |
| `5.2.4` | 第三段 | 5 参数分享限制生效 |
| `5.3.0` | 第四段 | 6 参数分享限制生效（含 `shareLimitsMode`） |

### 5.2 每档跑同一条闭环（12 条断言）

| 编号 | 步骤 | 断言 | 覆盖 |
|---|---|---|---|
| TC-LIVE-01 | 加种（默认暂停） | 出现在列表，状态属于"停止"族 | D5 |
| TC-LIVE-02 | 该状态字符串 | 与 `compatibility.json` 中该 tag 的状态名一致 | AC-2 |
| TC-LIVE-03 | 开始 | 状态属于"运行"族 | D1 |
| TC-LIVE-04 | 停止 | 回到"停止"族 | D1 |
| TC-LIVE-05 | 重新校验 | 回 `reannounce` 不报错 | — |
| TC-LIVE-06 | 单种限速 1 MiB/s | 读回等于 1048576 | D2 |
| TC-LIVE-07 | 全局限速（记录原值） | 读回等于设定值 | D2 |
| TC-LIVE-08 | 全局限速还原 | 读回等于开跑前原值 | 无副作用 |
| TC-LIVE-09 | 分享限制 ratio=2、时长=60 | 不再 400；`properties` 里 `seeding_time_limit=60`（分钟） | D3 D4 |
| TC-LIVE-10 | 设分类 + 标签 | 读回一致 | 回归 |
| TC-LIVE-11 | 筛选 `paused` | 返回集合 = 全表中处于停止族的集合，且**不等于全部** | D6 |
| TC-LIVE-12 | 删除（含文件） | 列表消失 | 回归 |

### 5.3 隔离与清理（全部发生在 CI 运行器内）

- 测试种子由测试自己生成（单文件 1 字节，`announce` 指向 `http://127.0.0.1:1/announce`），**不联网、不产生任何真实下载**；
- 每档容器用 `qBittorrent` 官方镜像 + 临时密码（沿用现有 CI 的 `docker logs` 取密码逻辑），容器与运行器同生共死，作业结束即销毁；
- 全部资源在 fixture 的 finalizer 删除，失败也执行；
- 失败时把容器日志与 `torrents/info` 快照作为构建产物上传，便于离线复查；
- **开发机不得执行 `docker run`、不得配置指向真实实例的 `QB_TEST_*`**：`tests/live` 在没有 `QB_TEST_URL` 时保持 skip，本地永远不会误跑。

---

## 6. 环境门禁与流水线改动

| 改动 | 内容 |
|---|---|
| `TC-ENV-04` | `--expect-tests` 更新为新的收集数（当前 130，预计 188 左右，以实际为准） |
| `live` 作业改造 | 由单版本改为 `strategy.matrix.qb_version: ["4.6.7","5.1.4","5.2.4","5.3.0"]`，`fail-fast: false`，任一档失败即整体红 |
| 触发条件 | 从"仅 main"改为 **`pull_request` + `workflow_dispatch` + `main`**，否则先用 PR 拿到"红"的证据做不到 |
| 时间预算 | `timeout-minutes: 25`；容器就绪等待上限 180 秒（原 90 秒，避免拉镜像慢造成假红） |
| 失败策略 | 实盘失败**不重试**，视为真实回归；仅"容器未在 180 秒内就绪"允许重跑一次 |
| 并发 | 加 `concurrency` 组，同一 PR 的新提交取消上一次运行，避免四个容器排队 |
| 费用 | 仓库为公开仓库，Actions 分钟不计量；四档并行各约 3–5 分钟 |

### 6.1 "红"的证据怎么取（全部在 CI，不碰本地）

1. **第一个提交只放测试**（`test: 补齐 0.2.0 用例`），实现一行不改；
2. 推分支 → 开 PR → CI 自动跑：
   - 离线作业：`TC-CTR/LIM/SHR/ADD/LST/SEC/CON` 应当大面积红；
   - 实盘四档：`TC-LIVE-01/03/07/09/11` 应当红（404 / 值不符 / 400）；
3. 把红的作业链接与失败摘要（哪条用例、什么现象）记入 `TESTLIST.md`；
4. 第二个提交放实现 → 同一 PR 重跑 → 全绿；
5. 合并前由你确认红→绿两轮记录都在。

> 这一步需要往你的 GitHub 仓库推分支并开 PR。属于对外操作，我会在推之前单独跟你确认。

---

## 7. 明确不测的东西

- 4.6.0 之前的版本（不在支持范围，`compatibility.json` 也没有数据）；
- 真实 PT 站点的 tracker 连通性与 H&R 规则（属于部署环境问题，不是本项目职责）；
- qBittorrent 图形界面行为、libtorrent 内部调度；
- 打包差异：实盘矩阵测官方镜像，发行版自定义编译不在覆盖内（文档已声明）。

---

## 8. 工作量与耗时

| 项 | 预计 |
|---|---|
| 单元用例（约 58 条） | 3–4 小时，含先红后绿的验证（本地跑，只调 Python） |
| 契约测试与 JSON 消费 | 1 小时 |
| 实盘矩阵脚本改造 | 2 小时 |
| 验证轮次 | 每轮 CI 约 5–8 分钟（四档并行、含拉镜像）；红一轮 + 绿一轮 |
| 合计 | 约 1 个工作日，期间本地不启动任何容器 |

---

## 9. 交付物

1. `tests/test_capability.py`、`test_limits.py`、`test_contract.py`，以及改造后的 `test_write_tools.py`
2. `tests/live/` 的矩阵化改造与 `expectations` 断言
3. `scripts/audit_compat.py`（开源）
4. 更新后的 `TESTLIST.md`，每条新用例带"红→绿证据"
5. 一份测试报告：红→绿两轮 CI 运行链接、四档容器结果、离线覆盖率对比
