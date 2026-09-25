"""Regenerate docs/TOOLS.md from the server's tool definitions:  python scripts/gen_tools_doc.py"""

import asyncio
import inspect
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

OUT = Path(__file__).resolve().parent.parent / "docs" / "TOOLS.md"


def _type(schema: dict) -> str:
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    if "anyOf" in schema:
        return " or ".join(_type(s) for s in schema["anyOf"] if s.get("type") != "null")
    t = schema.get("type", "any")
    if t == "array":
        return f"list of {_type(schema.get('items', {}))}"
    return t


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "efinity_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools

    lines = [
        "# Tool reference",
        "",
        "Generated from the server's tool definitions by `scripts/gen_tools_doc.py`. The descriptions",
        "are exactly what the assistant sees.",
        "",
    ]
    for t in tools:
        hints = []
        a = t.annotations
        if a is not None:
            if a.read_only_hint:
                hints.append("read-only")
            if a.destructive_hint and not a.read_only_hint:
                hints.append("changes files or hardware")
        lines += [f"## `{t.name}`" + (f"  ({', '.join(hints)})" if hints else ""), ""]
        lines += [inspect.cleandoc(t.description or ""), ""]
        props = (t.input_schema or {}).get("properties", {})
        required = set((t.input_schema or {}).get("required", []))
        if props:
            lines += ["| Parameter | Type | Default |", "|---|---|---|"]
            for name, schema in props.items():
                default = "required" if name in required else f"`{schema.get('default')!r}`" if "default" in schema else ""
                lines.append(f"| `{name}` | {_type(schema)} | {default} |")
            lines.append("")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(tools)} tools)")


if __name__ == "__main__":
    asyncio.run(main())
