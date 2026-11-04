#!/usr/bin/env python3
"""从 qBittorrent 源码逐 tag 抽取 WebUI 接口契约，产出 docs/compatibility.json。

为什么需要它：qBittorrent 的 WebUI 接口在 5.0 / 5.2 / 5.3 各改了一批名字，官方
wiki 又只有按大版本分的两页（而且有错：5.0 页写的 add 参数 `paused`，源码实际
只读 `stopped`）。所以本项目的契约以**源码**为准，用本脚本逐 tag 抽取并固化成
机器可读的表，测试再从表生成。

用法：

    python scripts/audit_compat.py                      # 扫默认范围并覆盖 docs/compatibility.json
    python scripts/audit_compat.py --tags release-5.3.0 # 只扫一个 tag
    python scripts/audit_compat.py --out /tmp/x.json    # 写到别处

完整性规则：每个文件都要求 HTTP 200 + 最小字节数 + 关键符号存在，任一不满足
就写 `unavailable` 并以非零退出码结束——**绝不把残缺内容当事实**。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW = "https://raw.githubusercontent.com/qbittorrent/qBittorrent/{tag}/{path}"

DEFAULT_TAGS = (
    [f"release-4.6.{i}" for i in range(8)]
    + [f"release-5.0.{i}" for i in range(6)]
    + [f"release-5.1.{i}" for i in range(5)]
    + [f"release-5.2.{i}" for i in range(5)]
    + ["release-5.3.0"]
)


@dataclass(frozen=True)
class SourceFile:
    key: str
    path: str
    marker: str
    min_bytes: int


FILES = (
    SourceFile(
        "controller", "src/webui/api/torrentscontroller.cpp", "TorrentsController::", 40_000
    ),
    SourceFile("serialize", "src/webui/api/serialize/serialize_torrent.cpp", "torrentState", 5_000),
    SourceFile("filter", "src/base/torrentfilter.cpp", "TorrentFilter", 5_000),
    SourceFile("transfer", "src/webui/api/transfercontroller.cpp", "setDownloadLimit", 5_000),
)

FILTER_WORDS = (
    "all|downloading|seeding|completed|paused|stopped|active|inactive|resumed|"
    "running|stalled_uploading|stalled_downloading|stalled|errored|checking|moving"
)


def fetch(tag: str, source: SourceFile, timeout: float = 20.0, retries: int = 2) -> str | None:
    """取一个文件；只有通过三重校验才返回值，否则返回 None。"""
    url = RAW.format(tag=tag, path=source.path)
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                if response.status != 200:
                    continue
                body = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        if len(body) < source.min_bytes:
            continue
        if source.marker not in body:
            continue
        return body
    return None


def extract(tag: str, documents: dict[str, str | None]) -> dict[str, Any]:
    controller = documents.get("controller")
    serialize = documents.get("serialize")
    filterfile = documents.get("filter")
    transfer = documents.get("transfer")

    row: dict[str, Any] = {"tag": tag}

    if controller:
        row["add_pause_field"] = "/".join(
            sorted(set(re.findall(r'params\(\)\[u"(paused|stopped)"_s\]', controller)))
        ) or None
        row["control_endpoints"] = sorted(
            set(re.findall(r"TorrentsController::(pause|resume|stop|start)Action", controller))
        )
        body = _function_body(controller, "setShareLimitsAction")
        match = re.search(r"requireParams\(\{([^}]*)\}\)", body, re.S)
        row["share_limit_required"] = (
            sorted(re.findall(r'u"(\w+)"_s', match.group(1))) if match else None
        )
    else:
        row["add_pause_field"] = "unavailable"
        row["control_endpoints"] = []
        row["share_limit_required"] = None

    if serialize:
        row["state_strings"] = sorted(
            set(re.findall(r'"(paused(?:UP|DL)|stopped(?:UP|DL))"', serialize))
        )
    else:
        row["state_strings"] = "unavailable"

    vocabulary: set[str] = set()
    if controller:
        index = controller.find("parseTorrentStatus")
        if index >= 0:
            window = controller[index : index + 2500]
            vocabulary |= set(re.findall(r'statusStr == u"([a-z_]+)"', window))
    if filterfile:
        vocabulary |= set(re.findall(rf'"({FILTER_WORDS})"', filterfile))
    vocabulary.discard("all")
    row["filter_vocab"] = sorted(vocabulary) or "unavailable"

    if transfer:
        row["global_limit_endpoints"] = sorted(
            set(re.findall(r"TransferController::(set(?:Upload|Download)Limit)Action", transfer))
        )
    else:
        row["global_limit_endpoints"] = "unavailable"

    return row


def _function_body(text: str, name: str) -> str:
    index = text.find(f"TorrentsController::{name}")
    return text[index : index + 1500] if index >= 0 else ""


def is_complete(row: dict[str, Any]) -> bool:
    return not any(value == "unavailable" for value in row.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tags", nargs="*", default=None, help="要扫描的 tag，默认全量")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "compatibility.json"),
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=20.0, help="单次请求超时（秒）")
    args = parser.parse_args(argv)

    tags = args.tags or DEFAULT_TAGS
    jobs = [(tag, source) for tag in tags for source in FILES]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fetched = pool.map(lambda job: fetch(*job, timeout=args.timeout), jobs)
        results = dict(zip(jobs, fetched, strict=False))

    rows = []
    for tag in tags:
        documents = {source.key: results[(tag, source)] for source in FILES}
        rows.append(extract(tag, documents))
        missing = [key for key, value in documents.items() if value is None]
        if missing:
            print(f"  ! {tag}: 未取到 {', '.join(missing)}", file=sys.stderr)

    document = {
        "source": "qbittorrent/qBittorrent 源码逐 tag 审计（WebUI 接口层）",
        "integrity_check": (
            "HTTP 200 + 最小字节数 + 关键符号存在；任一不满足记 unavailable，绝不填推断值"
        ),
        "scanned_tags": len(tags),
        "tags": rows,
    }
    Path(args.out).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    incomplete = [row["tag"] for row in rows if not is_complete(row)]
    print(f"写入 {args.out}（{len(rows)} 个 tag）")
    if incomplete:
        print(f"不完整的 tag：{', '.join(incomplete)}", file=sys.stderr)
        print("请重跑后再提交；残缺的表会污染契约测试。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
