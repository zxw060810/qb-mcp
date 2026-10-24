"""End-to-end smoke test: speak the MCP protocol to qb-mcp over stdio.

Unlike the unit tests, this spawns the real server process and performs a
full MCP handshake (initialize -> tools/list -> tools/call), so it also
proves the package entry point and stdio transport work.

Usage:
    python scripts/mcp_smoke.py [tool_name]

It needs a working .instances.json, which is why it is not part of pytest.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(tool: str, arguments: dict) -> int:
    config = os.environ.get("QB_MCP_CONFIG", ".instances.json")
    if not os.path.isabs(config):
        config = os.path.abspath(config)
    if not os.path.isfile(config):
        print(f"ERROR: config not found: {config}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["QB_MCP_CONFIG"] = config

    params = StdioServerParameters(command=sys.executable, args=["-m", "qb_mcp.server"], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"server   : {info.serverInfo.name} {info.serverInfo.version}")
            print(f"protocol : {info.protocolVersion}")

            listing = await session.list_tools()
            print(f"tools    : {len(listing.tools)}")
            for item in listing.tools:
                print(f"  - {item.name}")

            result = await session.call_tool(tool, arguments)
            print(f"\n--- {tool} {json.dumps(arguments, ensure_ascii=False)} ---")
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print(text)
            if result.isError:
                print("(tool reported an error)")
                return 1
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "qb_instances"
    raw = sys.argv[2] if len(sys.argv) > 2 else "{}"
    raise SystemExit(asyncio.run(run(target, json.loads(raw))))
