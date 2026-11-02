# 测试清单（Test List）

| 项 | 值 |
|---|---|
| 项目 | `qb-mcp` |
| 文档版本 | 1.0 |
| 适用版本 | v0.1.0 |
| 对应环境规格 | [`VERIFICATION_ENV.md`](./VERIFICATION_ENV.md) v1.0 |
| 用例总数 | 26 自动化 + 18 手工 |
| 最后更新 | 2026-10-26 |

---

## 1. 覆盖策略

测试分五层，每层回答一个不同的问题。**下层不能替代上层。**

| 层 | 编号前缀 | 数量 | 方式 | 回答的问题 |
|---|---|---|---|---|
| L0 环境门禁 | `TC-ENV` | 5 | 命令 | 本次环境可信吗？ |
| L1 配置 | `TC-CFG` | 10 | 单测 | 用户配错了会怎样？ |
| L2 客户端 | `TC-CLI` | 10 | 单测 | 三种认证怪癖处理对了吗？ |
| L3 格式化 | `TC-FMT` | 6 | 单测 | 输出单位与字段对了吗？ |
| L4 协议 | `TC-SMK` | 4 | 真实 stdio | 包真能作为 MCP 服务被拉起吗？ |
| L5 验收 | `TC-MAN` | 18 | 人工 | 11 个工具在真机上干的事对吗？ |

**优先级定义：**

- **P0** — 挂了就不能发布（认证、启动、数据正确性）
- **P1** — 挂了功能受损但可用（格式化、容错）
- **P2** — 体验问题（错误文案、字段裁剪）

---

## 2. 执行方式

```bash
# 全量自动化（必须在 ENV-A 与 ENV-B 各跑一次）
env -u PYTHONPATH /tmp/ci-repro/bin/python -m pytest -q     # ENV-A: mcp 2.3.0
.venv/bin/python -m pytest -q                                # ENV-B: mcp 1.28.1

# 静态检查
env -u PYTHONPATH /tmp/ci-repro/bin/ruff check .
env -u PYTHONPATH /tmp/ci-repro/bin/mypy src

# 协议冒烟（需真实 .instances.json）
env -u PYTHONPATH .venv/bin/python scripts/mcp_smoke.py qb_instances
```

**判定规则：**

- 自动化层（L0~L3）**两个环境都必须全绿**，否则判失败
- L4 冒烟在 ENV-B / ENV-C 执行；ENV-A 下为已知问题 K1（见环境规格 §8）
- L5 手工层每次发版前执行一轮，覆盖两台真机

---

## 3. L0 环境门禁（TC-ENV）

| # | 检查项 | 命令/方法 | 期望 | 优先级 |
|---|---|---|---|---|
| TC-ENV-01 | PYTHONPATH 无污染 | `env -u PYTHONPATH <py> -c "import sys;print([p for p in sys.path if 'octop' in p])"` | `[]` | P0 |
| TC-ENV-02 | mcp 版本符合预期 | `-c "from importlib.metadata import version;print(version('mcp'))"` | ENV-A `2.3.0` / ENV-B `1.28.1` | P0 |
| TC-ENV-03 | 包以 editable 装入 | `-c "import qb_mcp;print(qb_mcp.__file__)"` | 指向 `src/qb_mcp/__init__.py` | P0 |
| TC-ENV-04 | 用例数正确 | `pytest --collect-only -q \| tail -1` | `26 tests collected` | P1 |
| TC-ENV-05 | 真机可达（仅 L5） | `curl -s -o /dev/null -w '%{http_code}' <url>` | 两台均 `200` | P0 |

**TC-ENV-01 失败 = 本次全部结果作废，不做任何归因分析。**

---

## 4. L1 配置层（TC-CFG，10 个）

文件：`tests/test_config.py`（90 行）

| # | 用例 | 验证内容 | 优先级 |
|---|---|---|---|
| TC-CFG-01 | `test_loads_single_instance` | 单实例配置正确加载，字段映射无误 | P0 |
| TC-CFG-02 | `test_label_overrides_display` | `label` 覆盖默认显示名，列表里用人话而非 key | P1 |
| TC-CFG-03 | `test_env_placeholder_is_expanded` | `${QB_PASS}` 从环境变量展开 | P0 |
| TC-CFG-04 | `test_missing_env_placeholder_raises` | **占位符对应环境变量缺失必须报错**，不得静默当空密码 | P0 |
| TC-CFG-05 | `test_password_from_file` | `{"file": "/path"}` 形式从文件读密码 | P0 |
| TC-CFG-06 | `test_multiple_instances_with_different_credentials` | **每个实例独立账号密码**，互不串用 | P0 |
| TC-CFG-07 | `test_missing_required_field_raises` | 缺 `url` / `username` 时报错并指出缺哪个 | P1 |
| TC-CFG-08 | `test_url_scheme_is_validated` | 只接受 `http` / `https` | P1 |
| TC-CFG-09 | `test_unknown_instance_lists_known_keys` | 实例名写错时**列出可用实例名**，而非只说"找不到" | P2 |
| TC-CFG-10 | `test_missing_file_raises` | 显式路径不存在**不回退当前目录**，防拼错后加载到真实配置 | P0 |

**重点说明 TC-CFG-06：** 这是用户明确要求的核心特性——"任意多台，每台独立账号"。原方案的"全局一对账号"已被否，此用例是那条决策的守卫。

**重点说明 TC-CFG-10：** 曾经的实现缺陷——显式路径不存在时会回退读当前目录的 `.instances.json`，导致拼错路径反而静默加载了生产配置。已修，此用例防复发。

---

## 5. L2 客户端层（TC-CLI，10 个）

文件：`tests/test_client.py`（126 行）—— **本项目最核心的 10 个用例。**

| # | 用例 | 验证内容 | 优先级 |
|---|---|---|---|
| TC-CLI-01 | `test_new_session_sends_referer_and_origin_matching_host` | `Referer` / `Origin` 必须与请求 `Host` **完全一致（含端口）** | P0 |
| TC-CLI-02 | `test_trailing_slash_in_url_is_normalised` | 配置 URL 带尾斜杠不得拼出 `//api/v2/...` | P1 |
| TC-CLI-03 | `test_login_success_with_200_ok` | 标准成功形态：`200` + `Ok.` | P0 |
| TC-CLI-04 | `test_login_success_with_204_empty_body` | **反代成功形态：`204` + 空 body**（QNAP QTS 实测） | P0 |
| TC-CLI-05 | `test_login_failure_returns_200_with_fails_body` | **密码错误也返 `200`**，正文 `Fails.` → 必须判为失败 | P0 |
| TC-CLI-06 | `test_login_403_reports_ip_ban` | `403` 应报"IP 被封禁"而非"密码错误" | P1 |
| TC-CLI-07 | `test_login_5xx_raises` | `5xx` 属服务端故障，不得误判为认证失败 | P1 |
| TC-CLI-08 | `test_login_posts_credentials` | 断言确实以表单 POST 了 `username` / `password` | P0 |
| TC-CLI-09 | `test_reauthenticates_once_on_401` | 会话过期自动重登，**且只重登一次**（防无限循环） | P0 |
| TC-CLI-10 | `test_reset_forces_new_login` | `reset()` 真能清掉 cookie 并重新登录 | P1 |

### 5.1 三个认证怪癖（本层存在的理由）

这三条**全部来自真机实测，文档里查不到**：

| 怪癖 | 天真实现 | 后果 |
|---|---|---|
| `Referer` 必须等于 `Host`（含端口） | 不设或设成固定值 | 一律 `401 Unauthorized` |
| 密码错误返回 `200` | `if status == 200: 成功` | 把密码错误当成登录成功，后续全 403 |
| 反代成功返回 `204` 空 body | `if body == "Ok.": 成功` | 连 QNAP 永远登录失败 |

**登录成功判定必须是：**

```python
status < 400 and body.strip() in ("", "Ok.")
```

TC-CLI-03 / 04 / 05 三条用例就是在守这一行。**任何人重构 `client.py` 而不带这三条，等于拆掉防雷针。**

---

## 6. L3 格式化层（TC-FMT，6 个）

文件：`tests/test_tools.py`（75 行）

| # | 用例 | 验证内容 | 优先级 |
|---|---|---|---|
| TC-FMT-01 | `test_gb_uses_si_units` | 用 `10^9`（SI）而非 `2^30`，对齐 qB WebUI 与日报口径 | P1 |
| TC-FMT-02 | `test_speed_formats_bytes_per_second` | 速度单位换算正确 | P1 |
| TC-FMT-03 | `test_speed_handles_garbage` | `-1` / `null` / 字符串不得抛异常 | P1 |
| TC-FMT-04 | `test_limit_text_zero_means_unlimited` | **限速 `0` = 不限速**，显示"无限"而非 `0 B/s` | P1 |
| TC-FMT-05 | `test_slim_keeps_only_curated_fields` | 响应字段白名单（30+ → 12），不多不漏 | P2 |
| TC-FMT-06 | `test_slim_tolerates_missing_fields` | 不同 qB 版本字段缺失时容错，不 KeyError | P1 |

**TC-FMT-01 的由来：** `2^30` 与 `10^9` 差 7.4%，在 4000 GB 量级上差约 300 GB。用户日报用的是 SI，两边必须一致，否则数字对不上。

---

## 7. L4 协议层（TC-SMK，4 个）

文件：`scripts/mcp_smoke.py` —— **不是 pytest，需要真实配置。**

| # | 用例 | 执行 | 期望 | 优先级 |
|---|---|---|---|---|
| TC-SMK-01 | 握手 | `python scripts/mcp_smoke.py qb_instances` | `server: qb-mcp 0.1.0` / `protocol: 2025-11-25` | P0 |
| TC-SMK-02 | 工具注册 | 同上 | `tools: 11` 且名称与设计一致 | P0 |
| TC-SMK-03 | 真实调用 | 同上 | 返回 2 台实例状态，正文含 `OK, qBittorrent v4.6.4` / `v5.2.4` | P0 |
| TC-SMK-04 | 入口点 | `<py> -m qb_mcp.server` | 进程正常启动并响应 stdio | P0 |

**这一层证明的是单测证明不了的事：** 入口点包装正确、stdio 传输可用、`python -m` 能起。

**已知限制 K1：** ENV-A（mcp 2.3.0）下 TC-SMK 全部无法执行（客户端侧 API 变更，未兼容）。当前在 ENV-B / ENV-C 验证。**这不影响 CI**——CI 不含本层。

---

## 8. L5 手工验收（TC-MAN，18 个）

**每次发版前，在两台真机各执行一轮。** 使用真实 `.instances.json`。

### 8.1 读操作（6 个）

| # | 工具 | 操作 | 期望 | 优先级 |
|---|---|---|---|---|
| TC-MAN-01 | `qb_instances` | 列出实例 | 显示 2 台，含版本与匿名化状态；**密码不得出现** | P0 |
| TC-MAN-02 | `qb_overview` | 看总览 | 累计上/下行、分享率、连接数**全部非空** | P0 |
| TC-MAN-03 | `qb_list` | 列出种子 | 字段已瘦身，分页/过滤可用 | P1 |
| TC-MAN-04 | `qb_detail` | 单种详情 | hash 可定位，字段完整 | P1 |
| TC-MAN-05 | `qb_diagnose` | 诊断 | verdict 合理，不把"有限速"误判为瓶颈 | P0 |
| TC-MAN-06 | `qb_diagnose` | 诊断（`leechers < 10` 的种） | 不得判"连不上对等方" | P1 |

### 8.2 写操作（8 个）

| # | 工具 | 操作 | 期望 | 优先级 |
|---|---|---|---|---|
| TC-MAN-07 | `qb_control` | `pause` | 状态变暂停，**回显"改前 → 改后"** | P0 |
| TC-MAN-08 | `qb_control` | `resume` | 恢复做种 | P0 |
| TC-MAN-09 | `qb_control` | `reannounce` | 返回成功 | P1 |
| TC-MAN-10 | `qb_set_limit` | 设 `0` | 显示"无限"，实际不限速 | P1 |
| TC-MAN-11 | `qb_set_limit` | 设 `1024`（KB/s） | 生效，回显新旧值 | P1 |
| TC-MAN-12 | `qb_set_share` | 设分享率上限 | 生效，回显新旧值 | P1 |
| TC-MAN-13 | `qb_add` | 加一个 magnet | 出现在列表中 | P0 |
| TC-MAN-14 | `qb_remove` | 删除（不传 `delete_files`） | **仅移除任务，文件保留** | P0 |

### 8.3 边界与安全（4 个）

| # | 场景 | 操作 | 期望 | 优先级 |
|---|---|---|---|---|
| TC-MAN-15 | 分类管理 | `qb_category` 建/改分类 | 生效 | P1 |
| TC-MAN-16 | 错误密码 | 用错密码的实例 | 报"认证失败"，**不得报"网络错误"** | P0 |
| TC-MAN-17 | 未知实例 | 传不存在的实例名 | 报错并**列出可用实例名** | P2 |
| TC-MAN-18 | 危险操作 | `qb_remove` 传 `delete_files=true` | 必须显式确认语义；默认永不删文件 | P0 |

---

## 9. 缺陷回归对照表

**真机实测发现的 3 个真实 bug，必须长期有用例守着。当前状态：⚠️ 无自动化覆盖，仅手工用例。**

| # | 缺陷 | 根因 | 修法 | 守护用例 | 状态 |
|---|---|---|---|---|---|
| B1 | `qb_overview` 累计上/下行、分享率、连接数全空 | 用错端点：`transfer/info` 只有实时速度与限速 | 改读 `sync/maindata` 的 `server_state` | TC-MAN-02 | ⚠️ 仅手工 |
| B2 | `qb_diagnose` 把"有限速"当"限速是瓶颈"（当时上传为 0） | 只看配置值不看实际吞吐 | 改为吞吐 ≥ 上限 80% 才判；另加备用限速模式检查 | TC-MAN-05 | ⚠️ 仅手工 |
| B3 | `qb_diagnose` 滥用"连不上对等方" | `leechers > 0` 即判 | 改为 `leechers >= 10` 才判 | TC-MAN-06 | ⚠️ 仅手工 |
| B4 | 显式配置路径不存在会回退读当前目录 | 缺省路径逻辑与显式路径逻辑混用 | 显式路径不存在直接报错 | TC-CFG-10 | ✅ 已自动化 |

**B1~B3 是当前最大的测试债。** 它们只写在代码注释和本文档里，重构时没有任何东西会报警。补自动化用例是下一优先事项（见 §10）。

---

## 10. 未覆盖清单（Gap Register）

诚实登记，不留盲区：

| # | 缺口 | 影响 | 风险 | 计划 |
|---|---|---|---|---|
| G1 | 11 个工具函数的业务逻辑几乎无单测 | 覆盖约 30% | **高** | 把 B1/B2/B3 固化为 mock 单测 |
| G2 | `scripts/mcp_smoke.py` 客户端未兼容 mcp 2.x | 冒烟无法在 ENV-A 跑 | 中 | 待修（问题 K1） |
| G3 | 无并发测试 | 多请求并发行为未知 | 低 | 后续 |
| G4 | 无性能测试 | 数千种子时列表耗时未知 | 低 | 后续 |
| G5 | 无 Python 3.10 / 3.11 本地验证 | 仅靠 CI | 中 | 依赖 CI 矩阵 |
| G6 | 无跨平台验证（Windows/macOS） | 纯 Python HTTP 客户端，风险低 | 低 | 接受 |
| G7 | 无 qB 5.3+ 验证 | 未来版本字段可能变 | 低 | 发版跟进 |

### 10.1 补测优先级

**第一优先（消除 B1~B3 的裸奔）：**

1. `qb_overview` 的 mock 单测 —— 断言累计数据取自 `server_state`，而非 `transfer/info`
2. `qb_diagnose` 的 mock 单测 —— 三条 verdict 分支各一例
3. 预计新增 6~8 个用例，覆盖 `server.py` 最核心的两个函数

**第二优先：** G2（冒烟客户端兼容 2.x）
**第三优先：** 其余

---

## 11. 执行记录模板

每次发版前填写：

```
版本：v0.1.0
日期：____-__-__
执行人：______

【L0 环境门禁】
TC-ENV-01  □ 通过   TC-ENV-02  □ 通过   TC-ENV-03  □ 通过
TC-ENV-04  □ 通过   TC-ENV-05  □ 通过

【自动化 · ENV-A（mcp 2.3.0）】
ruff    □ 通过    mypy    □ 通过    pytest  □ __/26

【自动化 · ENV-B（mcp 1.28.1）】
ruff    □ 通过    mypy    □ 通过    pytest  □ __/26

【L4 协议冒烟 · ENV-B】
TC-SMK-01 □   TC-SMK-02 □   TC-SMK-03 □   TC-SMK-04 □

【L5 手工验收】
飞牛（v4.6.4）：TC-MAN-01 ~ 18  □ __/18
QNAP（v5.2.4）：TC-MAN-01 ~ 18  □ __/18

【结论】□ 通过  □ 有条件通过（列出）：________  □ 不通过

【已知遗留】
K1 冒烟 2.x 未兼容 / G1 业务逻辑覆盖不足 / …
```

---

## 12. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-26 | 1.0 | 首版：5 层结构、26 自动化 + 18 手工、4 项缺陷回归、7 项缺口登记 |
