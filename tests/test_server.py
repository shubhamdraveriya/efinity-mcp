"""The server over real MCP stdio: tool list, schemas, and readable errors."""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "get_efinity_info", "list_projects", "get_project_info", "create_project", "update_project",
    "run_flow", "get_job_status", "list_jobs", "cancel_job",
    "get_build_status", "get_timing_summary", "get_timing_paths", "get_utilization", "get_messages",
    "get_io_assignments", "list_reports", "read_report", "run_sta_tcl",
    "get_interface_design", "get_interface_block", "list_device_resources", "check_interface",
    "calc_pll", "edit_interface", "export_interface_isf",
    "list_programmer_cables", "program_device",
}


def _session(fn):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "efinity_mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return await fn(s)

    return asyncio.run(run())


def test_tools_listed():
    async def go(s):
        return (await s.list_tools()).tools

    tools = _session(go)
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description and len(t.description) > 40, t.name
    read_only = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
    assert "get_timing_summary" in read_only
    assert "edit_interface" not in read_only and "program_device" not in read_only


def test_errors_are_readable(tmp_path):
    async def go(s):
        return await s.call_tool("get_project_info", {"project": str(tmp_path / "nothing_here")})

    result = _session(go)
    assert result.is_error
    text = result.content[0].text
    # the real reason reaches the client, not just "Error executing tool"
    assert "nothing_here" in text and "not found" in text


def test_cli_version():
    import subprocess

    out = subprocess.run([sys.executable, "-m", "efinity_mcp", "--version"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert out.stdout.startswith("efinity-mcp ")
