"""Framework demo: LangGraph agent using web-mcp tools.

An LLM agent calls the MCP tools from platform/web-mcp/src/server.py to
ingest web pages and answer questions through search. The agent decides which
tool to call based on the task description.

Requires the MCP server to be running in a separate terminal:

    cd platform/web-mcp
    BLUE_WEB_EMBEDDING=openai \\
    OPENAI_API_KEY=sk-... \\
    BLUE_WEB_REDIS_HOST=localhost \\
    python src/web_mcp/server.py

Then run this demo:

    OPENAI_API_KEY=sk-... python3 examples/framework-demos/mcp_demo.py

Deps: pip install langgraph langchain-openai langchain-mcp-adapters
      OPENAI_API_KEY, MCP server running (see above)
"""
from __future__ import annotations

import asyncio
import os
import sys

if not os.getenv("OPENAI_API_KEY"):
    print("ERROR: OPENAI_API_KEY environment variable not set.")
    sys.exit(1)

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent
except ImportError:
    print("ERROR: required packages not installed.")
    print("  pip install langgraph langchain-openai langchain-mcp-adapters")
    sys.exit(1)

MCP_SERVER_PATH = os.path.join(
    os.path.dirname(__file__), "../../platform/web-mcp/src/web_mcp/server.py"
)

W = 68
TASK = (
    "You are a web research assistant. "
    "First ensure the index exists for collection 'demo', then ingest the following URLs: "
    "https://en.wikipedia.org/wiki/Climate_change and https://en.wikipedia.org/wiki/Renewable_energy. "
    "After ingesting, search for 'solar energy growth and capacity' and summarise the top results."
)


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


async def run_agent():
    print()
    print("═" * W)
    print("  blue-data-web-source — MCP Agent Demo")
    print("═" * W)
    print()
    print("  SETUP")
    _hr()
    _ok(f"MCP server path : {MCP_SERVER_PATH}")
    _ok( "Model           : gpt-4o-mini")
    _ok( "Tools exposed   : ensure_index, ingest_url, ingest_urls, crawl_site, ingest_feed, search, search_web, extract")

    print()
    print("  Connecting to MCP server via stdio transport…")

    async with MultiServerMCPClient(
        {
            "blue-web": {
                "command": "python",
                "args": [MCP_SERVER_PATH],
                "transport": "stdio",
            }
        }
    ) as client:
        tools = client.get_tools()
        tool_names = [t.name for t in tools]
        _ok(f"Tools loaded: {tool_names}")

        llm   = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        agent = create_react_agent(llm, tools)

        print()
        print("  AGENT TASK")
        _hr()
        print(f"  {TASK[:W - 2]}")
        if len(TASK) > W - 2:
            print(f"  {TASK[W - 2:]}")

        print()
        print("  Running agent…")
        _hr()

        result = await agent.ainvoke({"messages": [{"role": "user", "content": TASK}]})

        messages = result.get("messages", [])
        for msg in messages:
            role = getattr(msg, "type", "?")
            content = getattr(msg, "content", "")
            if not content:
                continue
            if role == "tool":
                print(f"\n  [tool result]")
            elif role == "ai":
                print(f"\n  [assistant]")
            else:
                continue
            lines = str(content).splitlines()
            for line in lines[:8]:
                print(f"    {line[:W - 4]}")
            if len(lines) > 8:
                print(f"    … ({len(lines) - 8} more lines)")

        # Final AI message
        final = next(
            (m for m in reversed(messages) if getattr(m, "type", "") == "ai"),
            None,
        )
        if final:
            print()
            print("  FINAL ANSWER")
            _hr()
            for line in str(final.content).splitlines():
                print(f"  {line}")

    print()
    print("  SUMMARY")
    _hr()
    _ok("Agent used MCP tools: ensure_index → ingest_url × 2 → search")
    _ok("All vector ops happened inside the MCP server process")
    _ok("Agent only saw JSON tool results — never touched the pipeline directly")
    print()
    print("  To run fully offline (no Wikipedia): swap URLs with local fixture URLs")
    print("  and patch the MCP server's fetcher in a test context.")
    print()
    print("═" * W)
    print()


def main():
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
