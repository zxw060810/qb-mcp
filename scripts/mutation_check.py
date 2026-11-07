#!/usr/bin/env python3
"""变异验证：把被测分支改坏，确认对应测试确实会红。

用法：python scripts/mutation_check.py（需要 .venv 里装好 pytest）

补测试最大的风险是写出"永远通过"的用例。这个脚本对每条新用例做一次：
临时改坏被测代码 → 只跑这条用例 → 期望失败 → 恢复原文件。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"

MUTATIONS = [
    (
        "qb_list 的异常分支",
        "src/qb_mcp/server.py",
        "        torrents = qb.torrents(**params)\n    except (QBError, ConfigError) as exc:",
        "        torrents = qb.torrents(**params)\n    except ZeroDivisionError as exc:",
        "tests/test_write_tools.py::test_list_surfaces_api_errors_with_the_instance_name",
    ),
    (
        "全局下载读回不一致",
        "src/qb_mcp/server.py",
        '        if dl_limit is not None and not same_rate(after.get("dl_rate_limit"), dl_limit):',
        "        if False and dl_limit is not None:",
        "tests/test_write_tools.py::test_global_download_limit_mismatch_is_reported",
    ),
    (
        "备用限速写入异常",
        "src/qb_mcp/server.py",
        (
            '        qb.post("/api/v2/app/setPreferences", '
            'data={"json": json.dumps(payload)})\n        after = qb.preferences()\n'
            "    except (QBError, ConfigError) as exc:"
        ),
        (
            '        qb.post("/api/v2/app/setPreferences", '
            'data={"json": json.dumps(payload)})\n        after = qb.preferences()\n'
            "    except ZeroDivisionError as exc:"
        ),
        "tests/test_write_tools.py::test_alternative_limit_write_failure_is_caught",
    ),
    (
        "备用下载读回不一致",
        "src/qb_mcp/server.py",
        '    if dl_limit is not None and not same_rate(after.get("alt_dl_limit"), dl_limit):',
        "    if False and dl_limit is not None:",
        "tests/test_write_tools.py::test_alternative_download_limit_mismatch_is_reported",
    ),
    (
        "错误正文截断",
        "src/qb_mcp/client.py",
        "            if len(detail) > 200:",
        "            if False:",
        "tests/test_client.py::test_long_error_bodies_are_truncated",
    ),
    (
        "capability.major",
        "src/qb_mcp/capability.py",
        "        return self.version[0] if self.version else None",
        "        return 0",
        "tests/test_capability.py::test_major_is_derived_from_the_version",
    ),
    (
        "诊断里的接口版本",
        "src/qb_mcp/server.py",
        '        webapi = qb.webapi_version() if hasattr(qb, "webapi_version") else "未知"',
        '        webapi = "未知"',
        "tests/test_diagnose.py::test_diagnosis_header_reports_the_webapi_version",
    ),
    (
        "client.webapi_version",
        "src/qb_mcp/client.py",
        '        value = self.get("/api/v2/app/webapiVersion")',
        '        value = "0.0.0"',
        "tests/test_client.py::test_webapi_version_is_read_and_stripped",
    ),
]


def run(test: str) -> int:
    return subprocess.run(
        [str(PY), "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).returncode


def main() -> int:
    failures = 0
    print(f"{'目标':24s} {'改坏后':10s} {'判定'}")
    print("-" * 52)
    for name, relative, old, new, test in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        if old not in original:
            print(f"{name:24s} {'无法定位':10s} 跳过（锚点不匹配）")
            failures += 1
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            mutated = run(test)
        finally:
            path.write_text(original, encoding="utf-8")

        restored = run(test)
        ok = mutated != 0 and restored == 0
        if not ok:
            failures += 1
        print(
            f"{name:24s} {'红' if mutated != 0 else '仍绿':10s} "
            f"{'有效' if ok else '无效！'}"
        )
    print("-" * 52)
    print(f"{len(MUTATIONS) - failures}/{len(MUTATIONS)} 条用例通过变异验证")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
