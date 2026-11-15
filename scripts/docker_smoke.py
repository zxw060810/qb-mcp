#!/usr/bin/env python3
"""Build the container image and speak MCP to it over stdio.

This reproduces what a directory service does before it will list the server:
build the image, start it with no credentials present, and inspect what it
declares. Glama runs exactly this sequence, so a red build here means the
listing would break there.

The check that matters is the last one: ``tools/list`` has to succeed with no
configuration mounted. ``qb-mcp`` loads its config when a tool is called, not
at import time, and that is what makes introspection possible in a sandbox
that has no access to a real qBittorrent.

Usage:
    python scripts/docker_smoke.py
    python scripts/docker_smoke.py --skip-build --image qb-mcp:ci
    python scripts/docker_smoke.py --docker-prefix "sudo -n"   # local, unprivileged user
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = [
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
]


def build(image: str, prefix: list[str]) -> None:
    print(f"building {image}")
    subprocess.run([*prefix, "docker", "build", "-t", image, "."], check=True)


async def inspect(image: str, prefix: list[str]) -> int:
    command = prefix[0] if prefix else "docker"
    # Keep "docker" in the argument list: the prefix only says *how* to run it
    # (e.g. "sudo -n"), it does not replace it.
    args = [
        *prefix[1:], "docker",
        "run", "--rm", "-i",
        # /tmp is empty, so the server cannot accidentally find a config file
        # and the check stays honest about needing no credentials.
        "-w", "/tmp",
        image,
    ]
    # Inherit the environment so the child can resolve `docker` on PATH.
    params = StdioServerParameters(command=command, args=args)

    async with stdio_client(params, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            server = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            tools = await session.list_tools()

    names = sorted(tool.name for tool in tools.tools)
    print(f"server: {server.name} {server.version}")
    print(f"tools/list: {len(names)}")

    problems = []
    if server.name != "qb-mcp":
        problems.append(f"server name is {server.name!r}, expected 'qb-mcp'")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="qb-mcp:ci")
    parser.add_argument(
        "--docker-prefix",
        default="",
        help="command prepended to docker, e.g. 'sudo -n' when the user is not in the docker group",
    )
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing image")
    args = parser.parse_args()

    prefix = args.docker_prefix.split() if args.docker_prefix else []
    if not args.skip_build:
        build(args.image, prefix)
    return asyncio.run(inspect(args.image, prefix))


if __name__ == "__main__":
    sys.exit(main())
