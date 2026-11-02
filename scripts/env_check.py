#!/usr/bin/env python3
"""Environment gate checker for qb-mcp.

Implements TC-ENV-01 .. TC-ENV-05 from docs/TESTLIST.md.

The gates answer one question: *is this shell trustworthy enough to run the
test suite in?*  If any P0 gate fails, every pytest result produced afterwards
is meaningless -- most often because Octop's PYTHONPATH leaked into the
interpreter and pytest is importing somebody else's packages.

Usage
-----
    env -u PYTHONPATH python scripts/env_check.py --env A
    env -u PYTHONPATH python scripts/env_check.py --env B --instances .instances.json
    env -u PYTHONPATH python scripts/env_check.py --env C --no-net

Exit codes
----------
    0   all gates passed
    1   at least one gate failed
    2   usage or internal error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from importlib.util import find_spec
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Which mcp major each environment is supposed to carry.
EXPECTED_MCP_MAJOR = {"A": 2, "B": 1, "C": 1}

ENV_DESC = {
    "A": "CI reproduction - clean venv, PyPI latest mcp",
    "B": "Octop runtime replica - --system-site-packages",
    "C": "Octop child interpreter",
}

#: Substrings that only ever appear on an Octop-injected path.
#: Deliberately NOT the bare word "octop": this repo lives under
#: <repo>, so a naive "octop" filter would
#: flag the repo itself as pollution.
POLLUTION_MARKERS = (
    "@appcenter/octop-native/site-packages",
    "octop-native/data/.local",
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Result:
    gate: str
    name: str
    status: str
    detail: str
    priority: str = "P0"


def _polluted_paths() -> list[str]:
    found = [p for p in sys.path if any(m in p for m in POLLUTION_MARKERS)]
    env_pp = os.environ.get("PYTHONPATH", "")
    for entry in env_pp.split(os.pathsep):
        if entry and any(m in entry for m in POLLUTION_MARKERS):
            found.append(f"PYTHONPATH={entry}")
    return sorted(set(found))


def gate_01(allow_octop: bool) -> Result:
    """TC-ENV-01: the interpreter's search path matches its declared shape.

    Two legitimate shapes exist and they are opposites:

    * ENV-A (clean): Octop paths must be ABSENT -- anything else means the
      venv is silently borrowing Octop's packages.
    * ENV-B (overlay): Octop paths must be PRESENT -- they are the declared
      source of ``mcp`` 1.x, so their absence means the overlay broke and
      the run no longer reproduces Octop's runtime.
    """
    found = _polluted_paths()
    label = "overlay present" if allow_octop else "no PYTHONPATH pollution"
    if allow_octop:
        if not found:
            return Result(
                "TC-ENV-01",
                label,
                FAIL,
                "no Octop path found - ENV-B must overlay Octop's site-packages",
            )
        return Result("TC-ENV-01", label, PASS, f"overlay: {found[0]}")
    if found:
        return Result("TC-ENV-01", label, FAIL, "leaked: " + "; ".join(found))
    return Result("TC-ENV-01", label, PASS, "clean sys.path")


def gate_02(expect_major: int) -> Result:
    """TC-ENV-02: mcp major version matches the environment's contract."""
    try:
        raw = pkg_version("mcp")
    except PackageNotFoundError:
        return Result("TC-ENV-02", "mcp version", FAIL, "mcp not installed")

    major = int(raw.split(".")[0])
    if major != expect_major:
        return Result(
            "TC-ENV-02",
            "mcp version",
            FAIL,
            f"got {raw} (major {major}), expected major {expect_major}",
        )
    return Result("TC-ENV-02", "mcp version", PASS, f"mcp {raw} (major {major})")


def gate_03() -> Result:
    """TC-ENV-03: qb_mcp resolves to this repo's src/, not a stale copy."""
    spec = find_spec("qb_mcp")
    if spec is None or not spec.origin:
        return Result("TC-ENV-03", "qb_mcp importable", FAIL, "module not found")

    origin = Path(spec.origin).resolve()
    src_root = (REPO / "src" / "qb_mcp").resolve()
    try:
        origin.relative_to(src_root)
    except ValueError:
        return Result(
            "TC-ENV-03",
            "qb_mcp importable",
            FAIL,
            f"resolved to {origin}, expected under {src_root}",
        )
    return Result("TC-ENV-03", "qb_mcp importable", PASS, str(origin))


def gate_04(expect_tests: int) -> Result:
    """TC-ENV-04: pytest collects the expected number of cases."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return Result("TC-ENV-04", "test count", FAIL, f"pytest not runnable: {exc}")

    collected = 0
    for line in proc.stdout.splitlines():
        if "tests collected" in line or "test collected" in line:
            head = line.split(" tests collected")[0].split(" test collected")[0]
            try:
                collected = int(head.strip().split()[-1])
            except (ValueError, IndexError):
                collected = 0

    if collected != expect_tests:
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        hint = tail[-1] if tail else "no output"
        return Result(
            "TC-ENV-04",
            "test count",
            FAIL,
            f"collected {collected}, expected {expect_tests} | {hint}",
        )
    return Result("TC-ENV-04", "test count", PASS, f"{collected} tests collected")


def gate_05(instances_path: Path | None) -> Result:
    """TC-ENV-05: every configured qBittorrent instance answers HTTP."""
    if instances_path is None:
        return Result("TC-ENV-05", "qBittorrent reachable", SKIP, "no --instances given")
    if not instances_path.is_file():
        return Result("TC-ENV-05", "qBittorrent reachable", FAIL, f"{instances_path} not found")

    try:
        raw = json.loads(instances_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Result("TC-ENV-05", "qBittorrent reachable", FAIL, f"cannot parse: {exc}")

    targets = raw.get("instances", raw) if isinstance(raw, dict) else raw
    if isinstance(targets, dict):
        pairs = [(name, cfg) for name, cfg in targets.items() if isinstance(cfg, dict)]
    else:
        pairs = [(cfg.get("name", str(i)), cfg) for i, cfg in enumerate(targets)]

    if not pairs:
        return Result("TC-ENV-05", "qBittorrent reachable", FAIL, "no instances in file")

    problems, passed = [], []
    for name, cfg in pairs:
        url = str(cfg.get("url", "")).rstrip("/")
        if not url.startswith(("http://", "https://")):
            problems.append(f"{name}: bad url {url!r}")
            continue
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (urllib.error.URLError, OSError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        if code >= 500:
            problems.append(f"{name}: HTTP {code}")
        else:
            passed.append(f"{name}=HTTP {code}")

    if problems:
        return Result("TC-ENV-05", "qBittorrent reachable", FAIL, "; ".join(problems))
    return Result("TC-ENV-05", "qBittorrent reachable", PASS, ", ".join(passed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the qb-mcp environment gates (TC-ENV-01..05).",
    )
    parser.add_argument("--env", choices=sorted(EXPECTED_MCP_MAJOR), required=True)
    parser.add_argument("--instances", type=Path, default=None)
    parser.add_argument("--expect-tests", type=int, default=26)
    parser.add_argument("--no-net", action="store_true")
    parser.add_argument(
        "--allow-octop",
        action="store_true",
        help="ENV-B overlay mode: require Octop paths to be present, not absent",
    )
    args = parser.parse_args(argv)

    expect_major = EXPECTED_MCP_MAJOR[args.env]
    results = [
        gate_01(args.allow_octop),
        gate_02(expect_major),
        gate_03(),
        gate_04(args.expect_tests),
        gate_05(None if args.no_net else args.instances),
    ]

    print(f"qb-mcp environment gates | ENV-{args.env} | {ENV_DESC[args.env]}")
    print(f"interpreter: {sys.executable}")
    print(f"python     : {sys.version.split()[0]}")
    print()
    for item in results:
        print(f"  {item.gate}  {item.status:<4}  {item.name:<24}  {item.detail}")
    print()

    failed = [r for r in results if r.status == FAIL]
    hard = [r for r in failed if r.priority == "P0"]
    if hard:
        print(f"RESULT: FAIL ({len(hard)} P0 gate(s) failed) - test results are void")
        return 1
    if failed:
        print(f"RESULT: WARN ({len(failed)} non-blocking gate(s) failed)")
        return 0
    print("RESULT: PASS - environment is trustworthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
