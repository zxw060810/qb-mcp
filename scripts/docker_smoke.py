#!/usr/bin/env python3
"""Build the container image and speak MCP to it over stdio.

This reproduces what a directory service does before it will list the server:
build the image, start it with no credentials present, and ask what it exposes.
Glama runs exactly this sequence, so a red build here means the listing breaks
there instead.

The check that matters is the tool listing: it has to succeed with **no**
configuration mounted. ``qb-mcp`` reads its config when a tool is called, not
at import time, and that is what makes introspection possible in a sandbox
with no access to a real qBittorrent.

JSON-RPC is written straight to the container's stdin and read back from its
stdout, so the check needs nothing but the standard library.

Usage:
    python scripts/docker_smoke.py
    python scripts/docker_smoke.py --skip-build --image qb-mcp:ci
    python scripts/docker_smoke.py --docker-prefix "sudo -n"   # if not in the docker group
"""

from __future__ import annotations

import argparse
import json
import selectors
import subprocess
import sys

EXPECTED_TOOLS = sorted([
    "qb_add",
    "qb_category",
    "qb_control",
    "qb_detail",
    "qb_diagnose",
    "qb_instances",
    "qb_list",
    "qb_overview",
    "qb_remove",
    "qb_set_limit",
    "qb_set_share",
])

READ_TIMEOUT = 30.0


def run(prefix: list[str], args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([*prefix, *args], check=False, **kwargs)


def read_message(stream, deadline: float) -> dict:
    """Read one JSON-RPC message, letting the container take its time."""
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    try:
        while True:
            if not selector.select(timeout=max(0.1, deadline)):
                raise TimeoutError("the server did not answer in time")
            line = stream.readline()
            if not line:
                raise EOFError("the container closed stdout before answering")
            line = line.strip()
            if line:
                return json.loads(line)
            deadline -= 0.1
    finally:
        selector.close()


def send(proc: subprocess.Popen, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def inspect(image: str, prefix: list[str]) -> int:
    command = [*prefix, "docker", "run", "--rm", "-i",
               # /tmp is empty, so the server cannot pick up a config file by
               # accident and the "no credentials" claim stays honest.
               "-w", "/tmp", image]
    print(f"running: {' '.join(command)}")
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "docker-smoke", "version": "0"},
            },
        })
        assert proc.stdout is not None
        init = read_message(proc.stdout, READ_TIMEOUT)["result"]
        server = init["serverInfo"]

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = read_message(proc.stdout, READ_TIMEOUT)["result"]["tools"]
        names = sorted(tool["name"] for tool in tools)
    except (TimeoutError, EOFError, KeyError) as exc:
        detail = proc.stderr.read() if proc.stderr else ""
        print(f"FAIL {exc}", file=sys.stderr)
        if detail.strip():
            print(detail.strip(), file=sys.stderr)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"server: {server['name']} {server['version']}")
    print(f"tools/list: {len(names)}")

    problems = []
    if server["name"] != "qb-mcp":
        problems.append(f"server name is {server['name']!r}, expected 'qb-mcp'")
    if names != EXPECTED_TOOLS:
        missing = sorted(set(EXPECTED_TOOLS) - set(names))
        extra = sorted(set(names) - set(EXPECTED_TOOLS))
        problems.append(f"tool set differs — missing {missing}, unexpected {extra}")

    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1

    print("ok: the container answers introspection with no configuration mounted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the image and introspect it over stdio."
    )
    parser.add_argument("--image", default="qb-mcp:ci")
    parser.add_argument(
        "--docker-prefix",
        default="",
        help="command placed before docker, e.g. 'sudo -n' if not in the docker group",
    )
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing image")
    args = parser.parse_args()

    prefix = args.docker_prefix.split() if args.docker_prefix else []

    if not args.skip_build:
        print(f"building {args.image}")
        built = run(prefix, ["docker", "build", "-t", args.image, "."])
        if built.returncode != 0:
            print("FAIL the image did not build", file=sys.stderr)
            return 1

    return inspect(args.image, prefix)


if __name__ == "__main__":
    sys.exit(main())
