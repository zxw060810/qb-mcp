#!/usr/bin/env python3
"""把 pytest-cov 的 JSON 报告压缩成 README 徽章读取的小文件。

README 里的覆盖率徽章是 shields.io 的 dynamic/json 类型，直接读
``docs/coverage.json``，所以这里只保留徽章需要的几个字段，避免每次提交
都产生一份几十 KB 的 diff。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else ROOT / "coverage.json"
    target = ROOT / "docs" / "coverage.json"

    if not source.exists():
        print(f"missing coverage report: {source}", file=sys.stderr)
        return 1

    report = json.loads(source.read_text(encoding="utf-8"))
    totals = report["totals"]
    payload = {
        "percent": round(float(totals["percent_covered"]), 1),
        "statements": int(totals["num_statements"]),
        "missed": int(totals["missing_lines"]),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{target.relative_to(ROOT)}: {payload['percent']}% "
          f"({payload['missed']}/{payload['statements']} 行未覆盖)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
