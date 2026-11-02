# 验证环境规格说明（Verification Environment Specification）

| 项 | 值 |
|---|---|
| 项目 | `qb-mcp` |
| 文档版本 | **2.0** |
| 适用版本 | v0.1.0 |
| 配套文档 | [`TESTLIST.md`](./TESTLIST.md) |
| 配套工具 | [`scripts/env_check.py`](../scripts/env_check.py) |
| 最后更新 | 2026-10-26 |
| 状态 | 现行 |

> **v2.0 重构说明：** 把测试按**被测目标是"模拟"还是"真实"**分成两类（Class A / Class B），并把两个维度拆成**正交轴**。解释器环境（ENV-A/B/C）作为共享基座被两类测试**完全复用**，不为 Class B 新建任何 Python 环境。§8 给出逐项复用清单。

---

## 1. 目的与范围

本文定义 **qb-mcp 在发布前必须通过验证的环境规格**，回答五个问题：

1. 测试分几类？边界在哪？
2. 每类测试需要什么环境？
3. **哪些环境资产可以复用、复用率多少？**（§8）
4. 怎么自动判断"当前这个 shell 到底可不可信"？（§10）
5. 每一项检查的判定逻辑是什么？（§11）

**范围内：** 单元测试、静态检查、协议冒烟、Docker 集成、真机手工所依赖的环境与判定规则。
**范围外：** qBittorrent 自身部署运维、飞牛/QNAP 的 OS 层、网络链路质量。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| **SUT** | 被测系统 = `qb-mcp` 包 |
| **Class A** | 行为模拟测试：不连任何真 qB，被测目标是伪造的 |
| **Class B** | 真实连接测试：被测目标是真 qBittorrent（Docker 或 NAS） |
| **Target** | 被测目标（Axis 2 的取值）：Simulated / Docker / NAS |
| **ENV-A/B/C** | 三个 Python 解释器环境（Axis 1） |
| **Gate** | 环境门禁，不通过则后续结果作废 |
| **污染 / 覆盖层 / P0/P1/P2** | 见 v1.1 定义，未变 |

---

## 3. 两个正交轴与两类测试

### 3.1 核心拆分

**这是 v2.0 的结构基础：把"解释器环境"和"被测目标"当成两个互不相干的轴。**

```
        Axis 1：Python 解释器环境（决定 mcp 版本兼容性）
        ┌──────────┬──────────┬──────────┐
        │  ENV-A   │  ENV-B   │  ENV-C   │
        │ mcp 2.x  │ mcp 1.x  │ mcp 1.x  │
        └──────────┴──────────┴──────────┘
                    ×  （正交相乘）
        Axis 2：被测目标（决定真实性）
        ┌────────────┬────────────┬────────────┐
        │ Simulated  │  Docker    │    NAS     │
        │ 假 qB      │  真 qB 容器 │ 你的两台真机 │
        └────────────┴────────────┴────────────┘
```

**关键性质：**

| 性质 | 含义 |
|---|---|
| **Axis 1 完全复用** | 两类测试用**同一批 venv**，不为 Class B 建新环境 |
| **Axis 2 是新增维度** | 只新增"目标"，不新增"环境" |
| Axis 2 与 Axis 1 无依赖 | Docker 里的 qB 行为不随 `mcp` 版本变化 |

### 3.2 两类测试的定义

| | **Class A：行为模拟** | **Class B：真实连接** |
|---|---|---|
| 被测目标 | 假 qB（进程内 mock 或本地假服务器） | 真 qBittorrent |
| 运行位置 | 本地 + CI | **仅 CI**（Docker）+ 发版前 |
| 需要网络 | ❌ 零网络 | ✅ 需要容器/真机 |
| 单次耗时 | 毫秒 | 秒~十秒 |
| 数量 | 26 个（现有）+ 新增 | 约 10 个 |
| 主打能力 | **造边界**：0 种子、5000 种子、IP 封禁、超时 | **证真实**：真协议、真字段、真版本差异 |

### 3.3 两类的关系：Class B 是 Class A 的真值来源

**这是全篇最重要的一条纪律。**

Class A 的假 qB 有一个根本风险：**它可能是"我以为的 qB"，而不是真的 qB。**

解法不是引入第三套 model（v1.1 曾这样设想），而是：

```
        ┌──────────────────────────────────────────┐
        │  Class B：真实连接                        │
        │  观察到 qB 的真实行为                      │
        └──────────────────┬───────────────────────┘
                           │ 证据
                           ▼
        ┌──────────────────────────────────────────┐
        │  Class A：行为模拟                        │
        │  按观察到的行为编写假 qB                   │
        └──────────────────────────────────────────┘
```

**⇒ 强制纪律：Class A 中任何"断言 qB 行为"的用例，必须在注释里标明证据来源。**

```python
def test_login_success_with_204_empty_body():
    # 证据来源：TC-LIVE-03（Docker qB 5.2.4 实测）
    # 观察日期：2026-10-26
    ...
```

**没有证据来源的 Class A 行为断言，视为"待定"，不得作为发布依据。**

这条纪律取代了 v1.1 设想的独立 `QBModel` + 校准测试 —— **不需要额外维护一个模型，Class B 本身就是校准器。**

---

## 4. 环境结构图

### 4.1 分层结构

```
╔══════════════════════════════════════════════════════════════════════╗
║ L4  测试资产层                                                        ║
║  Class A 专用            │  共享              │  Class B 专用          ║
║  ┌──────────────┐        │  ┌────────────┐   │  ┌──────────────┐     ║
║  │ fake_qb/     │        │  │ conftest   │   │  │ tests/live/  │     ║
║  │ FakeSession  │        │  │ harness    │   │  │ docker 编排   │     ║
║  │ 假 HTTP 服务器│        │  │ env_check  │   │  │ CI job       │     ║
║  └──────────────┘        │  └────────────┘   │  └──────────────┘     ║
╚═════════════════════════════╤═══════════════════════════════════════╝
                              │ 在以下解释器中执行（两类共用）
╔═════════════════════════════▼═══════════════════════════════════════╗
║ L3  解释器层（Axis 1，单变量矩阵）                                     ║
║   ENV-A          ENV-B           ENV-C                               ║
║  ┌────────┐     ┌────────┐      ┌────────┐                           ║
║  │mcp 2.3.0│     │mcp 1.28│      │mcp 1.28│                           ║
║  │代表 CI  │     │代表运行时│      │Octop 真 │                           ║
║  └────────┘     └────────┘      └────────┘                           ║
╚═════════════════════════════╤═══════════════════════════════════════╝
                              │ 运行于
╔═════════════════════════════▼═══════════════════════════════════════╗
║ L2  宿主层                                                            ║
║   飞牛 FnOS · N100 · 16GB · Python 3.12.4                             ║
║   ⚠ 环境注入：PYTHONPATH = <octop>/site-packages                     ║
║   ⚠ Docker Hub 不可达（本地无法拉镜像）                                ║
╚═════════════════════════════╤═══════════════════════════════════════╝
                              │ 访问
╔═════════════════════════════▼═══════════════════════════════════════╗
║ L1  被测目标层（Axis 2）                                              ║
║  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐       ║
║  │ Simulated    │   │ Docker qB    │   │ NAS（flynn/qnap）   │       ║
║  │ 进程内/本地   │   │ CI runner 上  │   │ 两台真机            │       ║
║  │ Class A      │   │ Class B      │   │ Class B（手工）     │       ║
║  └──────────────┘   └──────────────┘   └────────────────────┘       ║
╚══════════════════════════════════════════════════════════════════════╝
```

### 4.2 依赖供给（与 v1.1 相同，已实测修正）

```
┌──────────────────────────────────────────────────────┐
│ Octop 服务进程  environ: PYTHONPATH ────────┐         │
└─────────────────────────────────────────────┼─────────┘
                                              │ 子进程继承
┌─────────────────────────────────────────────▼─────────┐
│ /opt/agent/site-packages           │
│   mcp 1.28.1 · pytest 9.1.1 · requests · pygments     │
└────────┬──────────────────────────────┬───────────────┘
         │                              │
    ┌────▼─────────┐          ┌─────────▼────────────┐
    │ ENV-B .venv  │          │ ENV-C python3.12     │
    │ 自带：空      │          │ 自带：无 mcp/pytest   │
    │ 借：全部      │          │ 借：全部              │
    └──────────────┘          └──────────────────────┘

    ┌───────────────────────────────┐
    │ ENV-A /tmp/ci-repro           │
    │ 自带：mcp 2.3.0 · pytest       │  ← 唯一自给自足
    └───────────────────────────────┘
```

**两类测试共用这三条供给链，不做区分。**

### 4.3 两类测试的数据流

**Class A（进程内 mock 路径）：**

```
pytest → conftest 注入 src/
   → test_*.py
   → QBClient → FakeSession 拦截（不出 socket）
                 └─ FakeResponse(status, text, payload)
   → 断言 FakeSession.calls
```

**Class A（本地假 HTTP 路径，可选）：**

```
pytest → 启动 ThreadingHTTPServer(port=0)
   → QBClient → 真实 socket → 假服务器 → 固定响应
   → 断言 HTTP 层行为（header、204 空 body）
```

**Class B（Docker / NAS）：**

```
pytest → 读 $QB_TEST_URL
   → QBClient → 真实 socket → 真 qBittorrent
   → 断言真实响应
```

**共用部分：`QBClient`、`InstanceConfig`、断言助手 —— 三种路径完全相同。**

### 4.4 目标拓扑

```
Class B · Docker（CI）
┌────────────────────────────────────┐
│ GitHub Actions runner              │
│  ┌────────────┐   ┌──────────────┐ │
│  │ pytest     │──▶│ qB container │ │
│  │ QB_TEST_URL│   │ :8080        │ │
│  └────────────┘   └──────────────┘ │
└────────────────────────────────────┘

Class B · NAS（发版前手工）
┌───────────────────────────────────────┐
│ .253 飞牛           .254 QNAP          │
│ qB 4.6.4 :8085      qB 5.2.4 :6363    │
│ 直连                 QTS 反代           │
└───────────────────────────────────────┘
```

---

## 5. L2 宿主层规格

| 项 | 规格 |
|---|---|
| 主机名 | `<nas-host>` |
| OS | 飞牛 FnOS（Debian 系） |
| CPU | Intel N100，4 核（AVX2，无 AVX512） |
| 内存 | 16 GB（常驻 9~11 GB） |
| 系统 Python | 3.12.4 |
| 安装形态 | FPK 原生（`OCTOP_INSTALL_MODE=fpk-native`） |
| 时区 | UTC+8 |

### 5.1 环境注入机制（污染的根源）

Octop 服务进程环境含：

```
PYTHONPATH=/opt/agent/site-packages
```

Agent 的每个 shell 都是其子进程，**无条件继承**：

```
继承 PYTHONPATH
   └─► sys.path 追加 Octop site-packages
        └─► import mcp 命中 1.28.1（而非 venv 的 2.3.0）
             ├─► pip 判定"已满足"，不装
             ├─► pytest 用的是 Octop 的
             └─► 结论与 CI 无关
```

**⇒ 强制规则：任何声称"干净"的环境，命令必须带 `env -u PYTHONPATH`。**

### 5.2 网络约束：Docker Hub 不可达

**实测（2026-10-26）：**

| registry | 可达性 |
|---|---|
| `registry-1.docker.io` | **000 不通** |
| `auth.docker.io` | **000 不通** |
| `ghcr.io` | 401 ✅ |
| `quay.io` | 401 ✅ |
| `docker.m.daocloud.io` | 401 ✅ |

**⇒ 推论：**

| 结论 | 影响 |
|---|---|
| 本机**无法拉取** qB 镜像 | Class B 的 Docker 目标**只能跑在 CI** |
| GitHub Actions runner 可直连 Docker Hub | CI 不受影响 |
| 本地 `docker` CLI 存在但 daemon 不可用 | 不作为本地依赖 |

**本条直接决定了 Class B 的运行位置：仅 CI。**

---

## 6. L3 解释器层规格（两类测试共用）

### 6.1 环境矩阵

| 代号 | 二进制 | 自带依赖 | 借入依赖 | 代表 | Class A | Class B |
|---|---|---|---|---|---|---|
| **ENV-A** | `/tmp/ci-repro/bin/python` | mcp **2.3.0**、pytest、requests | 无 | **GitHub Actions** | ✅ 必须 | ✅ CI 实际使用 |
| **ENV-B** | `qb-mcp/.venv/bin/python` | （几乎为空） | 全部来自 Octop | 开发便利层 | ✅ 必须 | ⬜ 可选 |
| **ENV-C** | `/var/apps/python312/target/bin/python3.12` | （无 mcp/pytest） | 全部来自 Octop | **Octop 真实解释器** | ⬜ 可选 | ⬜ 可选 |

**共同点（刻意的）：** Python 均 3.12.4、`requests` 均 2.34.2、`pytest` 均 9.1.1。
**唯一变量：** `mcp` 大版本（A=2.x，B/C=1.x）。

### 6.2 ENV-A：CI 复现环境（基准）

```bash
rm -rf /tmp/ci-repro
python3.12 -m venv /tmp/ci-repro
env -u PYTHONPATH /tmp/ci-repro/bin/pip install -U pip
env -u PYTHONPATH /tmp/ci-repro/bin/pip install -e ".[dev]"
```

| 项 | 值 |
|---|---|
| mcp 大版本 | **2** |
| Octop 路径 | 不得出现 |
| 权威性 | **唯一有权代表 CI 的环境** |

### 6.3 ENV-B：开发便利层（覆盖层）

```bash
python3.12 -m venv --system-site-packages .venv
env -u PYTHONPATH .venv/bin/pip install -e ".[dev]" --no-deps
```

#### ⚠️ 实测修正：`--system-site-packages` 未起作用

**证据 1：**

```
$ env -u PYTHONPATH .venv/bin/python -c "import mcp"
ModuleNotFoundError: No module named 'mcp'
```

**证据 2：**

```
$ env -u PYTHONPATH .venv/bin/python -m pytest -q
ModuleNotFoundError: No module named 'pygments'
```

**证据 3：**

```
$ .venv/bin/python -c "from importlib.metadata import version; print(version('mcp'))"
1.28.1
```

**结论：** `--system-site-packages` 指向 `/var/apps/python312` 的基础 `site-packages`，**那里没有 mcp、也没有 pytest**。ENV-B 的全部依赖来自 `PYTHONPATH` 注入的 Octop 路径。

**连带事实：** 此前所有「`.venv` 26 passed」记录中，pytest 本身也由 Octop 提供。这不影响结论成立，但"ENV-B 是独立环境"不成立。

| 项 | 值 |
|---|---|
| mcp 大版本 | **1** |
| Octop 路径 | **必须出现**（定义如此） |
| 干净性 | 不适用（它是覆盖层） |

**⇒ 调用必须显式加 `--allow-octop`**，否则门禁会正确地判它失败。

### 6.4 ENV-C：Octop 真实解释器

| 项 | 值 |
|---|---|
| 路径 | `/var/apps/python312/target/bin/python3.12` |
| mcp 大版本 | **1** |
| `qb_mcp` | **默认未安装** |

运行前需让 `qb_mcp` 可导入：

```bash
# 方式 1：装进该解释器
env -u PYTHONPATH /var/apps/python312/target/bin/python3.12 -m pip install -e . --user

# 方式 2：临时挂源码
PYTHONPATH=<repo>/src:/opt/agent/site-packages \
  /var/apps/python312/target/bin/python3.12 -m qb_mcp.server
```

### 6.5 依赖归因表

出 `ImportError` / 版本异常时先查这张表：

| 包 | ENV-A | ENV-B | ENV-C |
|---|---|---|---|
| `mcp` | 自带 2.3.0 | Octop | Octop |
| `pytest` | 自带 | Octop | **无** |
| `requests` | 自带 | Octop | Octop |
| `pygments` | 自带 | Octop | 无 |
| `qb_mcp` | editable | `repo/src` | **默认无** |

---

## 7. L1 被测目标层规格（Axis 2）

### 7.1 Simulated（Class A）

两种实现，按需选用：

| 实现 | 位置 | 拦截层 | 能测什么 |
|---|---|---|---|
| `FakeSession` | `tests/conftest.py` | `requests` 层 | 请求内容、响应解析（**不出 socket**） |
| 假 HTTP 服务器 | `tests/fake_qb/server.py` | socket 层 | **真实 HTTP 行为**：header 序列化、204 空 body、连接错误 |

| 项 | 值 |
|---|---|
| 端口 | `0`（系统分配，不冲突） |
| 依赖 | 纯 stdlib（`http.server`） |
| 出网 | ❌ 无 |
| 可用性 | 本地 / CI 均可 |

### 7.2 Docker（Class B 主战场）

| 项 | 值 |
|---|---|
| 运行位置 | **GitHub Actions runner** |
| 镜像 | `qbittorrentofficial/qbittorrent-nox`（待验证）或 `ghcr.io/hotio/qbittorrent` |
| 端口 | `8080`（容器内 → runner） |
| 版本矩阵 | `4.6.4` / `5.2.4`（对齐两台真机） |
| 生命周期 | job 内起停，跑完销毁 |

#### ⚠️ 未验证项（按"无实证不下结论"纪律明确标注）

**以下四项本地无法核验（Docker Hub 不可达），属推断，不是事实：**

| # | 待确认 | 风险 | 首次验证方式 |
|---|---|---|---|
| U1 | 官方镜像 tag 的命名与是否存在 | 中 | CI 首跑 `docker pull` 的返回码 |
| U2 | 4.6.4 老 tag 是否仍被保留 | **中** | 同上 |
| U3 | 容器首次启动**临时密码**如何取得（日志格式） | **中** | `docker logs` 首跑输出 |
| U4 | WebUI 端口是否可环境变量配置 | 低 | 镜像文档或 `docker inspect` |

**U1~U4 必须在第一次 CI 运行中逐一确认，确认前不得把 Class B 写入"已通过"。**

### 7.3 NAS（Class B 收尾）

| 别名 | 地址 | 版本 | libtorrent | Qt | WebAPI | 形态 |
|---|---|---|---|---|---|---|
| `flynn` | `192.0.2.11:8080` | 4.6.4 | 2.0.10.0 | 6.6.2 | 2.9.3 | 飞牛原生 |
| `qnap` | `192.0.2.10:6363` | 5.2.4 | 2.0.15.0 | 6.11.2 | 2.15.1 | QPKG + QTS 反代 |

**只用于发版前收尾（4 项），不参与日常回归。**

| 项 | 值 |
|---|---|
| 凭据 | `~/.config/qb-mcp/.env`，**不进仓库** |
| 仓库内示例 | `instances.example.json`（占位符） |
| 本地配置 | `.instances.json`，`600`，已 gitignore |

---

## 8. 环境复用清单

**用户要求：能复用的尽量复用。本章逐项列明。**

### 8.1 复用矩阵

| # | 资产 | Class A | Class B | 复用 | 备注 |
|---|---|---|---|---|---|
| R1 | **Python 解释器（ENV-A/B/C）** | ✅ | ✅ | **100%** | **不为 Class B 新建 venv** |
| R2 | `pip install -e ".[dev]"` | ✅ | ✅ | **100%** | 同一条命令，无分支 |
| R3 | `InstanceConfig` | ✅ | ✅ | **100%** | 只换 `url` 来源 |
| R4 | `QBClient`（被测代码） | ✅ | ✅ | **100%** | 同一份代码 |
| R5 | 根 `conftest.py` | ✅ | ✅ | **100%** | 追加一个 fixture 分支即可 |
| R6 | 断言助手（字段/单位） | ✅ | ✅ | **100%** | `tests/harness.py` |
| R7 | `scripts/env_check.py` | ✅ | ✅ | **100%** | Class B 复用 gate_05 实现 |
| R8 | CI job 的 setup 步骤 | ✅ | ✅ | **100%** | 同一 job 内串行两步 |
| R9 | 假服务器 `fake_qb/` | ✅ | ❌ | 0 | 仅 Class A |
| R10 | Docker 容器编排 | ❌ | ✅ | 0 | 仅 Class B |
| R11 | 凭据来源 | 假值 | 容器生成 | 0 | 语义不同，不可复用 |
| R12 | `ruff` / `mypy` 配置 | ✅ | ✅ | 100% | 同一份配置 |

**复用率：12 项中 9 项完全共享（75%）。**

### 8.2 设计原则：不为第二类测试新建环境

**明确禁止的做法：**

| ❌ 禁止 | 原因 |
|---|---|
| 建 `ENV-D` 专门跑 Class B | Class B 与 `mcp` 版本无关，复用 ENV-A 即可 |
| 建 `tests/live/conftest.py` 独立配置 | 根 conftest 已提供所需 fixture，重复配置会漂移 |
| CI 里两个独立 job | setup 步骤重复执行，浪费 30~40 秒 |
| Class B 单独一套断言函数 | 断言规则与目标无关，共用 `harness.py` |

**⇒ 唯一新增的复杂度是一个 fixture 分支：**

```python
# tests/conftest.py（在现有文件末尾追加，不改动已有内容）
@pytest.fixture
def live_qb():
    url = os.environ.get("QB_TEST_URL")
    if not url:
        pytest.skip("Class B: 未设置 QB_TEST_URL（本地默认跳过）")
    return InstanceConfig(
        name="docker",
        url=url,
        username=os.environ.get("QB_TEST_USER", "admin"),
        password=os.environ["QB_TEST_PASS"],
        label="Docker qB",
    )
```

**约 10 行，这就是两种目标复用的全部代价。**

### 8.3 目录结构（体现复用）

```
tests/
├── conftest.py          # 根：FakeSession + instance + live_qb  ← 两类共用
├── harness.py           # 断言助手                              ← 两类共用
├── fake_qb/             # 假服务器                              ← 仅 Class A
├── test_config.py       # Class A
├── test_client.py       # Class A
├── test_tools.py        # Class A
└── live/                # Class B（无 conftest，继承根）
    ├── test_smoke.py
    └── test_tools_live.py
```

**`tests/live/` 故意不放 `conftest.py`** —— 强制继承根配置，防止两套 fixture 漂移。

### 8.4 CI 中的复用（同一 job 串行）

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      # ── R2：两类共用同一次安装 ──
      - name: Install
        run: pip install -e ".[dev]"

      # ── Class A ──
      - name: Lint
        run: ruff check .
      - name: Type check
        run: mypy src
      - name: "Class A · simulated"
        run: pytest tests -q --ignore=tests/live

      # ── Class B：仅 py3.12 跑，复用上面全部步骤 ──
      - name: "Class B · start qB container"
        if: matrix.python-version == '3.12'
        run: |
          docker run -d --name qb -p 8080:8080 \
            -e QBT_WEBUI_PORT=8080 \
            qbittorrentofficial/qbittorrent-nox:5.2.4
          for i in $(seq 60); do
            curl -sf http://127.0.0.1:8080/api/v2/app/version && break
            sleep 1
          done

      - name: "Class B · live"
        if: matrix.python-version == '3.12'
        env:
          QB_TEST_URL: http://127.0.0.1:8080
          QB_TEST_USER: admin
          QB_TEST_PASS: ${{ steps.qbpass.outputs.value }}
        run: pytest tests/live -q
```

**复用点：** checkout / setup-python / pip install 只执行一次，Class B 直接复用 Class A 的解释器与已装好的包。

---

## 9. 测试流程

### 9.1 双流水线（共享前置）

```
                    ┌────────────────────────┐
                    │ S0  选择环境 ENV-X      │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ S1  门禁 env_check      │
                    │  （两类共用）            │
                    └───────────┬────────────┘
                        FAIL ───┴───► 中止
                                │ PASS
                    ┌───────────▼────────────┐
                    │ S2  ruff + mypy         │
                    │  （两类共用）            │
                    └───────────┬────────────┘
                                │
                ┌───────────────┴────────────────┐
                ▼                                ▼
    ┌───────────────────────┐      ┌───────────────────────────┐
    │ Class A               │      │ Class B                    │
    │  S3a pytest tests/    │      │  S3b 起容器 / 连 NAS        │
    │       --ignore=live   │      │  S4b pytest tests/live      │
    │  期望 26+ passed       │      │  期望 ~10 passed            │
    └───────────┬───────────┘      └─────────────┬─────────────┘
                │                                │
                └───────────────┬────────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ S5  手工收尾（NAS 4 项） │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ S6  判定与归档           │
                    └────────────────────────┘
```

### 9.2 阶段定义

| 阶段 | 进入条件 | 动作 | 产出 | 失败处置 |
|---|---|---|---|---|
| **S0** | — | 依 §6.1 定环境 | 环境代号 | — |
| **S1** | S0 | `env_check.py --env X` | 5 项三态 | **P0 失败即中止** |
| **S2** | S1 全 PASS | `ruff` + `mypy` | 0 error | 修复重跑 |
| **S3a** | S2 通过 | `pytest tests --ignore=tests/live` | `N passed` | 修复重跑 |
| **S3b** | S2 通过 | 起容器 / 连 NAS | `curl` 通 | 记 U1~U4 |
| **S4b** | S3b 通 | `pytest tests/live` | `~10 passed` | 修复重跑 |
| **S5** | S4b 通 | NAS 4 项手工 | 通过表 | 记缺陷 |
| **S6** | S5 完成 | 汇总 | 记录单 | — |

### 9.3 判定矩阵

| 阶段 | 失败影响 | 阻断发布 |
|---|---|---|
| S1 门禁 | 全部后续结论作废 | ✅ |
| S2 静态 | 代码质量不合规 | ✅ |
| **S3a Class A** | 已知行为可能退化 | ✅ |
| **S3b 容器启动** | Class B 无法执行 | ✅（视为环境故障） |
| **S4b Class B** | 真实协议未验证 | ✅ |
| S5 手工 | 真机未验证 | ✅ |

### 9.4 各环境该跑什么

| 环境 | Class A | Class B | 说明 |
|---|---|---|---|
| **ENV-A**（本地开发） | ✅ 必跑 | ❌ 跳过 | 无 `QB_TEST_URL`，自动 skip |
| **ENV-A**（CI） | ✅ | ✅ | 容器在 runner 上 |
| **ENV-B**（本地） | ✅ 必跑 | ⬜ 可选 | 对 NAS 手工跑时用 |
| **ENV-C** | ⬜ | ⬜ | 仅冒烟 `-m qb_mcp.server` |

**⇒ 本地日常只需一条命令：**

```bash
env -u PYTHONPATH /tmp/ci-repro/bin/python -m pytest tests -q --ignore=tests/live
```

**Class B 用例在本地会自动 skip，不会报错，也不会尝试拉镜像。**

---

## 10. Checker

### 10.1 定位

**是什么：** 把"环境可信度"变成可执行判定；每次开新 shell 先跑一次。
**不是什么：** 不测业务逻辑、不测凭据正确性、不替代 CI。

### 10.2 调用接口

```
scripts/env_check.py --env {A|B|C} [选项]

  --env {A,B,C}        声明环境形态，决定 mcp 期望大版本
  --allow-octop        ENV-B/C 覆盖层模式
  --instances PATH     指定 .instances.json → 启用 TC-ENV-05
  --live-url URL       指定容器/真机 URL → 启用 TC-ENV-06（Class B）
  --no-net             跳过网络类检查
  --expect-tests N     期望用例数，默认 26
```

### 10.3 三类调用

```bash
# Class A · ENV-A
env -u PYTHONPATH /tmp/ci-repro/bin/python scripts/env_check.py --env A --no-net

# Class A · ENV-B + 真机连通
.venv/bin/python scripts/env_check.py --env B --allow-octop --instances .instances.json

# Class B · CI 容器
env -u PYTHONPATH python scripts/env_check.py --env A --live-url http://127.0.0.1:8080
```

### 10.4 退出码

| 码 | 含义 |
|---|---|
| `0` | 全 PASS，或仅非阻断项 |
| `1` | 至少一个 P0 失败 → 中止 |
| `2` | 参数错误 / 内部异常 |

### 10.5 Gate 一览

| Gate | 名称 | P | Class A | Class B |
|---|---|---|---|---|
| TC-ENV-01 | 环境形状（双向） | P0 | ✅ | ✅ |
| TC-ENV-02 | mcp 大版本 | P0 | ✅ | ✅ |
| TC-ENV-03 | `qb_mcp` 解析路径 | P0 | ✅ | ✅ |
| TC-ENV-04 | 用例数量 | P1 | ✅ | — |
| TC-ENV-05 | NAS 可达 | P0 | — | ⬜ 手工时 |
| **TC-ENV-06** | **容器可达（`--live-url`）** | P0 | — | ✅ |

**TC-ENV-06 复用 TC-ENV-05 的实现**，仅 URL 来源不同 —— 这是 §8 R7 的具体体现。

---

## 11. 检查逻辑

### 11.1 总模型

每个 gate 输出三态：`PASS` / `FAIL` / `SKIP`。

```
hard_fail = FAIL 且 priority == P0

if hard_fail ≠ ∅ :  RESULT = FAIL (exit 1)
elif 有 FAIL      :  RESULT = WARN (exit 0)
else              :  RESULT = PASS (exit 0)
```

### 11.2 TC-ENV-01：环境形状（双向断言）

**检查对象：** `sys.path` 与 `PYTHONPATH` 中属于 Octop 的路径。

**标记定义：**

```python
POLLUTION_MARKERS = (
    "@appcenter/octop-native/site-packages",
    "octop-native/data/.local",
)
```

#### ⚠️ 为什么不用 `"octop"` 子串

**本仓库自己就住在** `<repo>`，含子串 `octop`。用子串匹配会把仓库自身判成污染，**门禁永远红**。

⇒ 标记必须收窄到**只可能由注入产生的完整片段**。这是**假阳性防护**。

#### 判定表

| `--allow-octop` | 找到 Octop 路径 | 判定 | 理由 |
|---|---|---|---|
| 否（ENV-A） | 没有 | PASS | 干净，符合声明 |
| 否（ENV-A） | **有** | **FAIL** | 被污染，结论作废 |
| 是（ENV-B/C） | 有 | PASS | 覆盖层就位 |
| 是（ENV-B/C） | **没有** | **FAIL** | 覆盖层断裂 |

**两个方向都是 FAIL 分支** —— 环境形状是双向契约。

#### 伪代码

```
P ← { p ∈ sys.path ∪ split(env.PYTHONPATH) | ∃ m ∈ MARKERS, m ∈ p }
if allow_octop:  PASS if P ≠ ∅ else FAIL("overlay missing")
else:            FAIL("leaked") if P ≠ ∅ else PASS
```

### 11.3 TC-ENV-02：mcp 大版本

```
expected = {"A": 2, "B": 1, "C": 1}[env]
raw ← version("mcp")          # PackageNotFoundError → FAIL
PASS if int(raw.split(".")[0]) == expected else FAIL
```

**只比大版本，不比精确版本。** 兼容层要保证的是"1.x 和 2.x 都成立"，比精确版本会让门禁每周变红一次，最终被无视。

**与 G1 交叉验证：**

| 情形 | G1 | G2 | 诊断 |
|---|---|---|---|
| 正常 ENV-A | PASS | PASS（2.x） | 正确 |
| **ENV-A 被污染** | **FAIL** | **FAIL（1.28.1）** | **双重确认** |
| 正常 ENV-B | PASS（overlay） | PASS（1.x） | 正确 |

### 11.4 TC-ENV-03：测的是不是本仓库代码

```
spec ← find_spec("qb_mcp")
origin ← realpath(spec.origin)
src_root ← realpath(<repo>/src/qb_mcp)
PASS if origin 在 src_root 下 else FAIL
```

**防三种误判：**

| 场景 | origin 指向 | 后果 |
|---|---|---|
| 装了 PyPI 旧版 | `site-packages/qb_mcp/` | **测的是旧代码** |
| conftest 的 `sys.path.insert` 失效 | 同上 | 同上 |
| 源码被移动 | `None` | 及时暴露 |

### 11.5 TC-ENV-04：用例数量

```
解析 "N tests collected"
PASS if N == expect else FAIL (P1)
```

**`= 0` 时 detail 必须附首行错误**，因为三种原因截然不同：

| 现象 | 原因 |
|---|---|
| `0` + `ModuleNotFoundError: pygments` | **解释器缺依赖**（ENV-B 清 PYTHONPATH 即如此） |
| `0` + `no tests ran` | `testpaths` 配错 |
| `0` + `ImportError` | 被测代码 import 失败（真 bug） |

**实测证据：**

```
TC-ENV-04 FAIL collected 0, expected 26 | ModuleNotFoundError: No module named 'pygments'
```

这一行直接指向"覆盖层被切断"，而非"测试写错了"。

### 11.6 TC-ENV-05 / 06：目标可达性

**两者共用同一实现，只有 URL 来源不同：**

| Gate | URL 来源 | 用途 |
|---|---|---|
| TC-ENV-05 | `.instances.json` 全部实例 | 本地连 NAS |
| TC-ENV-06 | `--live-url` 单个 | CI 连容器 |

#### 判定逻辑

```
对每个 url:
    if 不以 http:// 或 https:// 开头: problem
    GET url, timeout=5s
        HTTPError → code = exc.code
        URLError/OSError → problem
    code >= 500 → problem
    else        → passed(f"{name}=HTTP {code}")
```

#### ⚠️ 关键设计：门禁不做登录

**只发匿名 GET，不提交任何凭据。**

理由 —— **故障域分离：**

| 如果门禁去登录 | 后果 |
|---|---|
| 密码错了 | 门禁红 → 你以为环境坏了，实际是配置错了 |
| IP 被封 | 指向完全错误的方向 |
| 反代返 204 | 需走完整成功判定，把业务代码拖进门禁 |

**凭据正确性是 `test_client.py`（Class A，TC-CLI-03/04/05）与 `tests/live`（Class B）的职责。**

#### 为什么 `code >= 500` 才算失败

| 响应码 | 含义 | 判定 |
|---|---|---|
| `200` | 登录页 | PASS |
| `302` / `401` / `403` / `404` | 需认证 / 拒绝 / 路径不对 | **PASS**（服务活着） |
| `5xx` / 连接失败 / 超时 | 服务不存在或崩了 | **FAIL** |

**只有"服务不在了"才是环境问题。**

### 11.7 决策树

```
开跑
 │
 ├─► G1 环境形状 ── FAIL ──► 立即中止
 ├─► G2 mcp 大版本 ─ FAIL ──► 立即中止
 ├─► G3 qb_mcp 路径 ─ FAIL ─► 立即中止
 ├─► G4 用例数 ── FAIL ─────► 警告，继续
 ├─► G5 NAS 可达 ── FAIL ───► 中止 Class B（NAS 路径）
 └─► G6 容器可达 ── FAIL ───► 中止 Class B（Docker 路径），Class A 仍有效
```

#### 部分失败语义

| 失败项 | Class A | Class B(Docker) | Class B(NAS) |
|---|---|---|---|
| G1 | ✗ 作废 | ✗ 作废 | ✗ 作废 |
| G2 | ✗ 作废 | ✓ 有效 | ✓ 有效 |
| G3 | ✗ 作废 | ✗ 作废 | ✗ 作废 |
| G4 | ⚠ 需核对 | ✓ 有效 | ✓ 有效 |
| G5 | ✓ 有效 | ✓ 有效 | ✗ 不可执行 |
| G6 | ✓ 有效 | ✗ 不可执行 | ✓ 有效 |

**读法：** Class A 不联网，所以 G5/G6 失败时不必重跑它。这是分域排查依据。

---

## 12. 与 CI 的偏差登记

| # | 偏差 | CI | 本地 | 风险 | 处置 |
|---|---|---|---|---|---|
| D1 | `mcp` 版本 | PyPI 最新（2.x） | ENV-A 同 / ENV-B 1.x | **高** | 双环境都跑 |
| D2 | Python 版本 | 3.10/3.11/3.12 | 仅 3.12.4 | 中 | 依赖 CI |
| D3 | OS | `ubuntu-latest` | 飞牛 FnOS | 低 | 同为 Linux |
| D4 | Docker 可达 | ✅ | ❌ **不可达** | **高** | Class B 仅 CI |
| D5 | 依赖供给 | 全部自带 | ENV-B/C 靠覆盖层 | **高** | 见 §6.3 |
| D6 | 真机 | 无 | 两台 | — | 仅 S5 |

**⇒ 结论：本地"全绿"不能推导"CI 绿"，除非 ENV-A 也绿。**

---

## 13. 已知问题

| # | 问题 | 影响 | 状态 | 规避 |
|---|---|---|---|---|
| **K1** | `mcp_smoke.py` 在 ENV-A（mcp 2.3.0）无输出 | 冒烟无法在 2.x 验证 | 未解决 | 用 ENV-B/C |
| **K2** | 业务函数 Class A 覆盖不足（约 30%） | 重构可能静默退化 | 未解决 | 由 Class B 补 |
| **K3** | 无并发/性能测试 | 大规模种子下未知 | 接受 | 后续 |
| **K4** | ENV-C 默认无法 `import qb_mcp` | 需先安装 | 已知设计 | §6.4 |
| **K5** | **Docker 镜像 tag / 临时密码未验证** | Class B 可能启动失败 | **未验证** | 首次 CI 跑确认 U1~U4 |
| **K6** | **204 的成因未定**（反代 or qB 5.x） | 影响 Class A 假服务器建模 | **待证伪** | Class B 双版本可判定 |

### 13.1 K6：一个待证伪的归因

v1.1 及 `MEMORY.md` 中记录：「QNAP 登录返 `204` 是 QTS 反向代理造成的」。

**该归因存在未排除的替代解释：** 飞牛是 qB 4.6.4、QNAP 是 qB 5.2.4，**版本与网络路径两个变量同时变了**。

**Class B 双版本矩阵可直接判定：**

| 观察结果 | 结论 |
|---|---|
| 5.2.4 容器也返 `204` | **原归因错误**，是版本行为，与反代无关 |
| 5.2.4 容器返 `200 Ok.` | 原归因成立 |
| 4.6.4 容器返 `204` | 两者都不对，另有原因 |

**⇒ 这是 Class B 的附加收益：它同时是一个证伪实验。**

---

## 14. 环境快照命令

```bash
cd /path/to/qb-mcp
for py in /tmp/ci-repro/bin/python .venv/bin/python /var/apps/python312/target/bin/python3.12; do
  echo "--- $py"
  env -u PYTHONPATH "$py" -c "
import sys
from importlib.metadata import version as v
print(' python ', sys.version.split()[0])
for p in ('mcp','requests','pytest','ruff','mypy'):
    try: print(f' {p:8s}', v(p))
    except Exception: print(f' {p:8s} -')
print(' octop   ', [x for x in sys.path if 'octop-native' in x] or 'none')
"
done
```

**注意：** 快照带 `env -u PYTHONPATH`，故 ENV-B 会显示 `mcp -`。**这是正确结果**，如实反映 §6.3 的结论。

---

## 15. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-26 | 1.0 | 首版：三层结构、三环境矩阵、6 项偏差、3 项已知问题 |
| 2026-10-26 | 1.1 | 新增环境结构图（4 张）、测试流程、Checker、检查逻辑；修正 ENV-B 依赖来源 |
| 2026-10-26 | **2.0** | ① **拆出两个正交轴**：解释器环境（Axis 1）× 被测目标（Axis 2）② **测试分两类**：Class A 行为模拟 / Class B 真实连接 ③ 新增 §8 **环境复用清单**（12 项，9 项共享，75%）④ 确立纪律：**Class B 是 Class A 的真值来源**，取代 v1.1 的独立 model 方案 ⑤ 新增 TC-ENV-06 容器可达（复用 TC-ENV-05 实现）⑥ 新增 §5.2 Docker Hub 不可达实测 ⑦ 新增 U1~U4 未验证项登记 ⑧ 新增 K6「204 归因待证伪」⑨ 补全 CI 单 job 双 Class 的 YAML 结构 |
