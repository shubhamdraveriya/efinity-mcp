"""Efinix Efinity MCP server.

Drives the Efinity command-line flow (efx_run), parses its reports, runs STA Tcl
queries, and programs boards through the Efinity command-line programmer.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import env, interface, reports
from . import project as prj
from .jobs import CREATE_NO_WINDOW, JobManager

INSTRUCTIONS = """\
Tools for Efinix FPGAs (Trion, Titanium, Topaz) through the local Efinity install.

Typical loop: list_projects -> get_project_info -> run_flow(flow="compile") ->
get_build_status / get_timing_summary / get_messages -> fix RTL or SDC -> run_flow again.

- `project` arguments accept a project .xml path, a project folder, or a project name
  known to the Efinity GUI (see list_projects).
- run_flow waits up to wait_seconds and then returns a job_id; poll with get_job_status.
- Without an SDC file Efinity constrains every clock to 1 ns (1000 MHz), so large negative
  slack on an unconstrained design is expected, not a real failure.
- Interface Designer (I/O, PLLs, LVDS, MIPI, DDR, SerDes...): get_interface_design ->
  get_interface_block (exact property names + allowed values) -> edit_interface (validated,
  design-checked, backed up) -> run_flow(compile). list_device_resources finds free pins;
  calc_pll solves PLL dividers for target frequencies.
- program_device touches real hardware: JTAG mode loads volatile SRAM; active / passive /
  jtag_bridge modes overwrite the configuration flash. Confirm with the user before
  programming flash.
"""

_server = MCPServer("efinity", instructions=INSTRUCTIONS)
jobs = JobManager()


class _Server:
    """Registers tools so that anticipated failures reach the model as readable errors.

    The SDK hides the text of any exception other than ToolError ("Error executing tool x"),
    which would swallow messages like "No routed design found. Run the flow first."
    """

    def tool(self, **kwargs):
        def register(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kw):
                try:
                    return fn(*args, **kw)
                except ToolError:
                    raise
                except (ValueError, RuntimeError, OSError, KeyError, ET.ParseError) as exc:
                    raise ToolError(str(exc)) from exc

            _server.tool(**kwargs)(wrapper)
            return fn

        return register

    def run(self, transport="stdio"):
        _server.run(transport)


mcp = _Server()

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

FLOW_ALIASES = {
    "compile": "compile",
    "full": "compile",
    "synthesis": "map",
    "synth": "map",
    "map": "map",
    "interface": "interface",
    "pnr": "pnr",
    "place_and_route": "pnr",
    "bitstream": "pgm",
    "pgm": "pgm",
}


def _key(proj: prj.Project) -> str:
    return str(proj.xml).lower()


def _find_report(proj: prj.Project, report: str) -> Path:
    report = report.strip()
    candidates = [
        proj.outflow / report,
        proj.outflow / f"{proj.name}{report}",
        proj.outflow / f"{proj.name}.{report.lstrip('.')}",
        proj.dir / report,
    ]
    for cand in candidates:
        cand = cand.resolve()
        if cand.is_file() and proj.dir.resolve() in cand.parents:
            return cand
    raise FileNotFoundError(f"Report '{report}' not found. Use list_reports to see what is available.")


def _load_summary(proj: prj.Project) -> dict:
    out = {}
    route_xml = proj.output(".route.rpt.xml")
    if route_xml.is_file():
        data = reports.parse_tool_report(route_xml)
        out["core_resources"] = data.get("Core Resources")
        out["timing_final"] = data.get("Timing (Final)")
        out["interface_check"] = data.get("Interface")
    pt_xml = proj.output(".pt.rpt.xml")
    if pt_xml.is_file():
        periphery = reports.parse_tool_report(pt_xml).get("Periphery Resource", {})
        out["periphery_used"] = {k: v for k, v in periphery.items() if not str(v).startswith("0 /")}
    return out


def _job_result(job, proj: prj.Project | None = None) -> dict:
    result = job.summary(tail=0 if job.state == "succeeded" else 25)
    if job.state == "running":
        result["hint"] = "Still running. Call get_job_status with this job_id to wait for it."
    elif proj is not None and job.kind == "flow":
        if job.state == "succeeded":
            result.update(_load_summary(proj))
        errors = reports.collect_messages(proj, "error", extra_logs=[job.log_path])
        if errors:
            result["errors"] = errors[:20]
    return result


# --------------------------------------------------------------------------- install / projects

@mcp.tool(annotations=READ_ONLY)
def get_efinity_info() -> dict:
    """Show the Efinity install this server uses: path, version, user settings folder, recent projects."""
    home = env.efinity_home()
    return {
        "efinity_home": str(home),
        "version": env.efinity_version(),
        "python": str(env.efinity_python()),
        "user_dir": str(env.user_dir()),
        "default_project_dir": str(prj.default_project_root()),
        "recent_projects": [str(p) for p in prj.recent_projects()],
        "job_logs_dir": str(env.logs_dir()),
    }


@mcp.tool(annotations=READ_ONLY)
def list_projects(search_dir: str | None = None) -> list[dict]:
    """List Efinity projects: the GUI's recent projects plus projects found under the default
    project folder (or `search_dir`). Shows device and when the bitstream was last built."""
    paths = list(prj.recent_projects())
    paths += prj.scan_projects(Path(search_dir) if search_dir else prj.default_project_root())
    seen, rows = set(), []
    for path in paths:
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            proj = prj.Project(path.resolve())
            info = prj.read_project_info(proj)
            rows.append(
                {
                    "name": info["name"],
                    "project_file": str(proj.xml),
                    "device": f"{info['family']} {info['device']} ({info['timing_model']})",
                    "top_module": info["top_module"],
                    "bitstream_built": reports.mtime(proj.output(".bit")) or reports.mtime(proj.output(".hex")),
                }
            )
        except Exception as exc:  # unreadable project file; still list it
            rows.append({"project_file": str(path), "error": str(exc)})
    return rows


@mcp.tool(annotations=READ_ONLY)
def get_project_info(project: str) -> dict:
    """Read a project's settings: device, top module, design/constraint/interface files and
    synthesis, place-and-route, and bitstream options."""
    proj = prj.resolve_project(project)
    info = prj.read_project_info(proj)
    issue = prj.debug_profile_issue(proj)
    if issue:
        info["warning"] = issue
    return info


@mcp.tool(annotations=LOCAL_WRITE)
def create_project(
    name: str,
    directory: str,
    family: str,
    device: str,
    timing_model: str,
    top_module: str,
    design_files: list[str],
    sdc_files: list[str] | None = None,
    verilog_version: str = "verilog_2k",
    vhdl_version: str = "vhdl_2008",
) -> dict:
    """Create a new Efinity project .xml.

    family: Trion | Titanium | Topaz. device: e.g. Ti375N1156, Ti60F225, T20F256.
    timing_model: speed grade such as C4, C3, I4. design_files and sdc_files may be absolute or
    relative to `directory`. verilog_version: verilog_2k | sv_09 | ...; vhdl_version: vhdl_2008 | vhdl_93.
    The Interface Designer file (<name>.peri.xml) is created by the first edit_interface call
    (create the GPIOs / PLLs the top module's ports need) before a full compile.
    """
    proj = prj.create_project(
        name, directory, family, device, timing_model, top_module, design_files, sdc_files, verilog_version, vhdl_version
    )
    return prj.read_project_info(proj)


@mcp.tool(annotations=LOCAL_WRITE)
def update_project(
    project: str,
    add_files: list[str] | None = None,
    remove_files: list[str] | None = None,
    top_module: str | None = None,
    family: str | None = None,
    device: str | None = None,
    timing_model: str | None = None,
    add_sdc: list[str] | None = None,
    remove_sdc: list[str] | None = None,
) -> dict:
    """Edit a project .xml: add or remove HDL files and SDC files, change the top module or the
    device. A .xml.bak backup is written first."""
    proj = prj.resolve_project(project)
    return prj.update_project(proj, add_files, remove_files, top_module, family, device, timing_model, add_sdc, remove_sdc)


# --------------------------------------------------------------------------- flows / jobs

@mcp.tool(annotations=LOCAL_WRITE)
def run_flow(
    project: str,
    flow: Literal["compile", "synthesis", "interface", "pnr", "bitstream"] = "compile",
    wait_seconds: int = 600,
    timeout_minutes: int = 180,
) -> dict:
    """Run the Efinity flow with efx_run.

    compile = full RTL-to-bitstream (synthesis, debug core, interface, place & route, bitstream).
    The other flows run one stage and expect the earlier stages to be up to date.
    Waits up to `wait_seconds` (0 = return immediately); if the run is not done by then, returns
    a job_id to poll with get_job_status. When it finishes, the result includes resource usage,
    final timing, and any errors.
    """
    proj = prj.resolve_project(project)
    efx_flow = FLOW_ALIASES[flow]
    if efx_flow in ("compile", "map"):
        issue = prj.debug_profile_issue(proj)
        if issue:
            raise RuntimeError(issue)
    cmd = env.efx_run_command([str(proj.xml), "--flow", efx_flow, "--output_dir", str(proj.outflow)])
    job = jobs.start(
        kind="flow",
        description=f"{flow} {proj.name}",
        cmd=cmd,
        cwd=proj.dir,
        env_vars=env.efinity_env(),
        project_key=_key(proj),
        timeout=timeout_minutes * 60,
    )
    job.project = proj
    jobs.wait(job, wait_seconds)
    return _job_result(job, proj)


@mcp.tool(annotations=READ_ONLY)
def get_job_status(job_id: str, wait_seconds: int = 0, tail_lines: int = 40) -> dict:
    """Status of a background job (flow run or programming). Optionally wait up to wait_seconds
    for it to finish. Shows stage results and the last console lines."""
    job = jobs.get(job_id)
    jobs.wait(job, wait_seconds)
    result = _job_result(job, getattr(job, "project", None))
    if tail_lines:
        result["output_tail"] = list(job.lines)[-tail_lines:]
    return result


@mcp.tool(annotations=READ_ONLY)
def list_jobs() -> list[dict]:
    """List the jobs started in this server session, newest first."""
    return [j.summary(tail=0) for j in jobs.all()]


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=False))
def cancel_job(job_id: str) -> dict:
    """Stop a running job and its child processes."""
    return jobs.cancel(job_id).summary(tail=10)


# --------------------------------------------------------------------------- results

@mcp.tool(annotations=READ_ONLY)
def get_build_status(project: str) -> dict:
    """Summarise the latest build: which outputs exist and when they were made, whether sources
    changed since the bitstream was built, resource usage, final timing, and error/warning counts."""
    proj = prj.resolve_project(project)
    info = prj.read_project_info(proj)
    outputs = {
        "synthesis_netlist": proj.output(".map.v"),
        "interface_report": proj.output(".pt.rpt"),
        "route_report": proj.output(".route.rpt"),
        "timing_report": proj.output(".timing.rpt"),
        "bitstream_bit": proj.output(".bit"),
        "bitstream_hex": proj.output(".hex"),
    }
    status = {
        "project": proj.name,
        "device": f"{info['family']} {info['device']} ({info['timing_model']})",
        "outputs": {k: reports.mtime(v) for k, v in outputs.items()},
        "gui_state": reports.parse_proj_state(proj.output(".proj_state.ini")),
    }
    bit = outputs["bitstream_bit"] if outputs["bitstream_bit"].is_file() else outputs["bitstream_hex"]
    if bit.is_file():
        # The project .xml itself is rewritten by efx_run at the end of every run, so it can't be used here.
        sources = [proj.dir / f["file"] for f in info["design_files"]]
        sources += [proj.dir / f for f in info["sdc_files"] + info["interface_files"] + info["isf_files"]]
        if info["debugger"].get("auto_instantiation") == "on" and info["debugger"].get("profile"):
            sources.append(proj.dir / info["debugger"]["profile"])
        newer = [str(s.name) for s in sources if s.is_file() and s.stat().st_mtime > bit.stat().st_mtime]
        status["bitstream_stale"] = bool(newer)
        if newer:
            status["changed_since_bitstream"] = newer
    status.update(_load_summary(proj))
    msgs = reports.collect_messages(proj, "warning")
    status["message_counts"] = {
        sev: sum(1 for m in msgs if m["severity"] == sev) for sev in ("error", "critical", "warning")
    }
    return status


@mcp.tool(annotations=READ_ONLY)
def get_timing_summary(project: str) -> dict:
    """Post-route timing: constrained vs achievable clock frequencies, setup/hold slack per
    clock pair, worst slack, and whether timing is met (from <project>.timing.rpt)."""
    proj = prj.resolve_project(project)
    path = proj.output(".timing.rpt")
    summary = reports.parse_timing_summary(reports.read_text(path))
    summary["report"] = str(path)
    summary["generated"] = reports.mtime(path)
    return summary


@mcp.tool(annotations=READ_ONLY)
def get_timing_paths(
    project: str,
    analysis: Literal["setup", "hold"] = "setup",
    max_paths: int = 10,
    clock_filter: str | None = None,
    include_detail: bool = False,
) -> dict:
    """Worst critical paths from the timing report, sorted by slack. clock_filter keeps paths
    whose 'launch vs capture' clock pair contains the text. include_detail adds the full
    cell/net breakdown of each path. For custom queries (-from/-to/-through), use run_sta_tcl."""
    proj = prj.resolve_project(project)
    paths = reports.parse_timing_paths(reports.read_text(proj.output(".timing.rpt")), analysis, include_detail)
    if clock_filter:
        paths = [p for p in paths if clock_filter.lower() in (p.get("clock_pair") or "").lower()]
    return {"analysis": analysis, "total_paths_in_report": len(paths), "paths": paths[:max_paths]}


@mcp.tool(annotations=READ_ONLY)
def get_utilization(project: str, hierarchy: bool = False, max_depth: int = 2) -> dict:
    """Resource usage: core (XLRs, memory, DSP, I/O, clocks) and periphery after place & route,
    plus synthesis estimates. hierarchy=True adds a per-module breakdown (values are total(self))."""
    proj = prj.resolve_project(project)
    result = _load_summary(proj)
    res_csv = proj.output(".res.csv")
    if res_csv.is_file():
        result["synthesis_estimate"] = reports.parse_res_csv(res_csv)
    if hierarchy:
        hier = proj.output(".hier_util.rpt")
        if hier.is_file():
            result["hierarchy"] = reports.parse_hier_util(hier, max_depth)
        else:
            result["hierarchy"] = "hier_util.rpt not found (run place & route first)"
    if not result:
        raise FileNotFoundError("No utilization reports found. Run the flow first.")
    return result


@mcp.tool(annotations=READ_ONLY)
def get_messages(
    project: str,
    severity: Literal["error", "critical", "warning", "info"] = "warning",
    pattern: str | None = None,
    limit: int = 100,
) -> dict:
    """Errors and warnings from the latest run of each stage (synthesis, debug core, place,
    route, bitstream), most severe first. `severity` is the minimum level; `pattern` is a
    case-insensitive regex filter."""
    proj = prj.resolve_project(project)
    msgs = reports.collect_messages(proj, severity)
    if pattern:
        rx = re.compile(pattern, re.I)
        msgs = [m for m in msgs if rx.search(m["message"])]
    return {"total": len(msgs), "shown": min(len(msgs), limit), "messages": msgs[:limit]}


@mcp.tool(annotations=READ_ONLY)
def get_io_assignments(project: str, include_core_interface: bool = False) -> dict:
    """Package pin assignments of user signals (pin, bank, voltage, I/O standard, pull) from the
    Interface Designer pinout. include_core_interface adds the core<->periphery signal list."""
    proj = prj.resolve_project(project)
    result = {"pins": reports.parse_pinout(proj.output(".pinout.csv"))}
    if include_core_interface:
        result["core_interface"] = reports.parse_interface_csv(proj.output(".interface.csv"))
    return result


@mcp.tool(annotations=READ_ONLY)
def list_reports(project: str) -> list[dict]:
    """List the report and log files in the project's outflow folder."""
    proj = prj.resolve_project(project)
    if not proj.outflow.is_dir():
        return []
    keep = re.compile(r"\.(rpt|log|out|csv|xml|sdc|ini|v)$", re.I)
    return [
        {"file": f.name, "size_kb": round(f.stat().st_size / 1024, 1), "modified": reports.mtime(f)}
        for f in sorted(proj.outflow.iterdir())
        if f.is_file() and keep.search(f.name)
    ]


@mcp.tool(annotations=READ_ONLY)
def read_report(
    project: str,
    report: str,
    section: str | None = None,
    pattern: str | None = None,
    context_lines: int = 2,
    offset: int = 0,
    max_lines: int = 200,
) -> dict:
    """Read any report or log, e.g. 'timing.rpt', 'pt.rpt', 'map.rpt', 'route.out', 'EFX.warn.log'
    (the project-name prefix is optional).

    section: title or number of a report section (see the 'sections' list returned when omitted).
    pattern: regex; returns only matching lines with context_lines around them.
    offset/max_lines: page through long files.
    """
    proj = prj.resolve_project(project)
    path = _find_report(proj, report)
    text = reports.read_text(path)
    result = {"file": str(path), "sections": reports.list_sections(text)}
    if section:
        body = reports.get_section(text, section)
        if body is None:
            raise ValueError(f"Section '{section}' not found. Sections: {result['sections']}")
        text = body
    lines = text.splitlines()
    if pattern:
        rx = re.compile(pattern, re.I)
        hits = [i for i, line in enumerate(lines) if rx.search(line)]
        keep = sorted({j for i in hits for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1))})
        lines = [f"{j + 1}: {lines[j]}" for j in keep]
        result["matches"] = len(hits)
    result["total_lines"] = len(lines)
    result["offset"] = offset
    result["content"] = "\n".join(lines[offset: offset + max_lines])
    if offset + max_lines < len(lines):
        result["next_offset"] = offset + max_lines
    return result


@mcp.tool(annotations=READ_ONLY)
def run_sta_tcl(project: str, script: str, timeout_seconds: int = 900) -> dict:
    """Run Tcl in Efinity's static timing analyzer on the routed design (efx_run sta_tclsh).

    Supports SDC-style commands: report_timing (-from/-to/-through/-from_clock/-to_clock/-npaths/
    -setup/-hold/-detail), report_timing_summary, report_clocks, report_cdc, check_timing,
    get_cells/get_nets/get_pins/get_ports/get_clocks, all_registers, create_clock,
    set_false_path, and more. Run 'help' or '<cmd> -help' for usage. Constraints set here only
    apply to this session; they are not saved to the project.
    """
    proj = prj.resolve_project(project)
    if not proj.output(".troutingtraces").is_file():
        raise RuntimeError("No routed design found. Run the flow (at least through pnr) first.")
    tmp = Path(tempfile.gettempdir()) / f"efinity_mcp_sta_{int(time.time() * 1000)}.tcl"
    tmp.write_text(script + "\n", encoding="utf-8")
    cmd = env.efx_run_command(
        [str(proj.xml), "--flow", "sta_tclsh", "--tcl_script", str(tmp), "--output_dir", str(proj.outflow)]
    )
    job = jobs.start("sta", f"sta_tcl {proj.name}", cmd, proj.dir, env.efinity_env(), timeout=timeout_seconds)
    jobs.wait(job, timeout_seconds + 30)
    tmp.unlink(missing_ok=True)

    lines = list(job.lines)
    start = next((i + 1 for i, line in enumerate(lines) if "Evaluating user specified Tcl script" in line), 0)
    end = next((i for i, line in enumerate(lines) if "The entire flow of EFX_STA took" in line), len(lines))
    output = "\n".join(lines[start:end]).strip()
    result = {"state": job.state, "elapsed_s": job.elapsed(), "output": output[-60000:]}
    if job.state != "succeeded" or not output:
        result["console_tail"] = lines[-40:]
    return result


# --------------------------------------------------------------------------- interface designer

_peri_locks: dict[str, threading.Lock] = {}
_peri_locks_guard = threading.Lock()


def _peri_lock(proj: prj.Project) -> threading.Lock:
    with _peri_locks_guard:
        return _peri_locks.setdefault(_key(proj), threading.Lock())


@mcp.tool(annotations=READ_ONLY)
def get_interface_design(project: str, block_types: list[str] | None = None, include_check: bool = False) -> dict:
    """Overview of the project's Interface Designer (.peri.xml) design: every GPIO (mode, I/O
    standard, resource, package pin, bank), every other block (PLL, OSC, LVDS, MIPI, DDR, JTAG,
    PMA_DIRECT, ...) with its resource and key settings, and the I/O bank voltages.

    block_types limits the listing (e.g. ["GPIO"] or ["PLL"]). include_check also runs the
    Interface Designer design check. Reads the saved file, so it reflects the GUI only after the
    GUI saves.
    """
    proj = prj.resolve_project(project)
    return interface.run_helper(proj, "summary", block_types=block_types, include_check=include_check)


@mcp.tool(annotations=READ_ONLY)
def get_interface_block(project: str, name: str, block_type: str | None = None) -> dict:
    """All properties of one Interface Designer instance (GPIO, GPIO bus, PLL, LVDS_TX, ...) with the
    allowed values for each: choice lists, 'min:max' ranges, or free-form. Use it before
    edit_interface to get exact property names. For PLLs it adds the calculated VCO/output
    frequencies and the reference clock source."""
    proj = prj.resolve_project(project)
    return interface.run_helper(proj, "block", name=name, block_type=block_type)


@mcp.tool(annotations=READ_ONLY)
def list_device_resources(
    project: str,
    block_type: str = "GPIO",
    free_only: bool = True,
    bank: str | None = None,
    feature: str | None = None,
    limit: int = 200,
) -> dict:
    """Physical resources on the project's device that Interface Designer blocks can be placed on.

    GPIO: each resource with its package pin, I/O bank, features (e.g. HSIO, DDIO), alternate
    function (e.g. GCLK, PLL_CLKIN) and current user. Filter by bank ('4B'), or by feature or
    alternate function text ('PLL_CLKIN', 'HSIO'). Other block types (PLL, LVDS_TX, OSC, ...):
    the resource names and whether each is used. free_only=False includes used resources.
    """
    proj = prj.resolve_project(project)
    # Works before the project has a .peri.xml: the helper then uses an empty in-memory design.
    return interface.run_helper(
        proj, "resources", create_if_missing=True, block_type=block_type, free_only=free_only, bank=bank,
        feature=feature, limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
def check_interface(project: str) -> dict:
    """Run the Interface Designer design check (DRC) on the saved design: unplaced pins, I/O
    standard vs bank voltage mismatches, PLL frequency ranges, clock routing rules, and so on."""
    proj = prj.resolve_project(project)
    return interface.run_helper(proj, "check")


@mcp.tool(annotations=READ_ONLY)
def calc_pll(project: str, name: str, targets: dict[str, float] | None = None, max_results: int = 5) -> dict:
    """PLL calculator. With no targets, reports the PLL's current VCO/PLL/output frequencies.
    With targets such as {"CLKOUT0_FREQ": 200, "CLKOUT1_FREQ": 50, "CLKOUT1_PHASE": 90}, returns
    counter/divider solutions (M, N, O, per-output dividers) without changing anything. Apply one
    with edit_interface op auto_calc_pll. Only enabled outputs (CLKOUTn_EN=1) can be solved."""
    proj = prj.resolve_project(project)
    return interface.run_helper(proj, "calc_pll", name=name, targets=targets, max_results=max_results)


@mcp.tool(annotations=LOCAL_WRITE)
def edit_interface(
    project: str,
    operations: list[dict],
    dry_run: bool = False,
    allow_new_errors: bool = False,
) -> dict:
    """Change the Interface Designer design (.peri.xml). Operations run in order; if any fails,
    nothing is saved. After the operations the design check runs, and the file is saved only if
    no new design-check errors appeared (allow_new_errors=True saves anyway). dry_run=True applies
    and checks without saving. A .peri.xml.bak backup is written before saving. If the project has
    no .peri.xml yet, one is created for the project's device.

    Values are validated against the allowed values (see get_interface_block); I/O standards may
    be written as '3.3 V LVCMOS' or '3.3_V_LVCMOS'.

    Operations (each is an object with "op"):
      {"op": "create_gpio", "name": "led", "mode": "output", "pin": "U4", "properties": {"IO_STANDARD": "1.8 V LVCMOS"}}
          mode: input | output | inout | open_drain_output | clock_input | regional_clock_input |
          pll_clock_input | pll_ext_feedback | mipi_clock_input | pcie_perstn | clockout |
          global_control | vref | unused. A bus: add "msb": 7, "lsb": 0 (input/output/inout);
          buses can't take "pin", so place members with assign_pin on "led[0]" etc.
          "resource" (e.g. "GPIOB_P_31") can be given instead of "pin".
      {"op": "create_block", "name": "pll0", "type": "PLL", "resource": "PLL_TL0", "properties": {...}}
          type: any of block_types_supported from get_interface_design (PLL, OSC, LVDS_TX, LVDS_RX,
          MIPI_DPHY_RX, DDR, JTAG, PMA_DIRECT, ...). Optional "params" are passed to the API's
          create_block (tx_mode / rx_conn_type for LVDS, mode / conn_type for MIPI lanes).
      {"op": "set_properties", "name": "led", "properties": {"PULL_OPTION": "WEAK_PULLUP", "DRIVE_STRENGTH": "8"}}
          On a bus name, sets every member. Add "type" if a name is ambiguous.
      {"op": "assign_pin", "name": "led", "pin": "U4"}               (GPIO package ball)
      {"op": "assign_resource", "name": "pll0", "resource": "PLL_TR1"}
          Both refuse a resource already used by another instance unless "override": true.
      {"op": "delete", "name": "old_sig"}
      {"op": "set_bank_voltage", "bank": "4B", "voltage": "1.8"}
      {"op": "auto_calc_pll", "name": "pll0", "targets": {"CLKOUT0_FREQ": 200, "CLKOUT1_FREQ": 100}}
          Solves and applies M/N/O and dividers. Enable the outputs first (CLKOUTn_EN=1, CLKOUTn_PIN).
          PLL output frequencies are read-only properties; this is how they are set.
      {"op": "gen_pll_ref_clock", "name": "pll0", "refclk_name": "pll_refclk", "pll_res": "PLL_TL0"}
          Creates the reference-clock GPIO on the pin that feeds that PLL.
      {"op": "set_unused_gpio_state", "state": "INPUT_WITH_WEAK_PULLUP"}
      {"op": "import_isf", "file": "C:/path/settings.isf"}      (Interface Scripting File)

    After saving, run_flow(compile) rebuilds with the new interface. If the project is open in the
    Efinity GUI, close it or reload it there, or the GUI may save its old copy over these changes.
    """
    proj = prj.resolve_project(project)
    busy = jobs.running_for(_key(proj))
    if busy and not dry_run:
        raise RuntimeError(f"Job {busy.id} ({busy.description}) is running on this project. Wait for it before editing the interface.")
    if not operations:
        raise ValueError("operations is empty")
    with _peri_lock(proj):
        result = interface.run_helper(
            proj, "edit", create_if_missing=True, operations=operations, dry_run=dry_run, allow_new_errors=allow_new_errors
        )
    if result.get("saved"):
        backup = prj.register_peri_file(proj, prj.peri_design_file(proj))
        if backup:
            result["project_file_updated"] = "Added the new .peri.xml to the project"
        if interface.gui_running():
            result["gui_warning"] = (
                "The Efinity GUI is running. If this project is open there, reload it (or close it without "
                "saving) so it doesn't overwrite these changes."
            )
        result["next_step"] = "run_flow(flow='compile') to rebuild with the new interface settings."
    return result


@mcp.tool(annotations=LOCAL_WRITE)
def export_interface_isf(
    project: str,
    isf_file: str | None = None,
    block_types: list[str] | None = None,
    instances: list[str] | None = None,
    export_all_pins: bool = False,
) -> dict:
    """Export Interface Designer settings to an Interface Scripting File (.isf, Python commands
    that recreate the blocks). Useful as a readable backup, for diffs, or to copy settings to
    another project (import with edit_interface op import_isf). Defaults to <project>_export.isf
    in the project folder; block_types / instances limit what is exported."""
    proj = prj.resolve_project(project)
    target = Path(isf_file).expanduser() if isf_file else proj.dir / f"{proj.name}_export.isf"
    if not target.is_absolute():
        target = proj.dir / target
    return interface.run_helper(
        proj, "export_isf", isf_file=str(target.resolve()), block_types=block_types, instances=instances,
        export_all_pins=export_all_pins,
    )


# --------------------------------------------------------------------------- programming

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def list_programmer_cables(timeout_seconds: int = 60) -> dict:
    """Detect connected Efinix programming cables / dev boards (FTDI) and the FPGAs on their JTAG chain."""
    cmd, run_env = env.ftdi_program_command(["--scan_usb"])
    try:
        r = subprocess.run(
            cmd, env=run_env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"found": False, "error": f"Cable scan timed out after {timeout_seconds}s"}
    text = (r.stdout or "") + (r.stderr or "")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return {"found": True, "cables": json.loads(line)}
            except json.JSONDecodeError:
                break
    return {"found": False, "returncode": r.returncode, "output": text.strip()[-4000:]}


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))
def program_device(
    project: str | None = None,
    file: str | None = None,
    mode: Literal["jtag", "jtag_chain", "active", "passive", "jtag_bridge", "jtag_bridge_x8"] = "jtag",
    board_profile: str | None = None,
    url: str | None = None,
    chain_device_number: int | None = None,
    jtag_clock_hz: int | None = None,
    verify_method: Literal["none", "onchipx1", "onchipx2", "onchipx4", "hostx1"] | None = None,
    wait_seconds: int = 600,
) -> dict:
    """Program an Efinix FPGA through the Efinity command-line programmer.

    mode:
      jtag / jtag_chain  - load a .bit into the FPGA's SRAM over JTAG (lost at power-off)
      active / passive   - write a .hex to SPI configuration flash (or SPI passive load)
      jtag_bridge(_x8)   - write a .hex to configuration flash through the FPGA's JTAG bridge
    Flash modes overwrite what the board boots from, so confirm with the user first.
    Give either `project` (uses its outflow .bit/.hex) or an explicit `file`.
    url / board_profile select a cable when several are attached (see list_programmer_cables).
    chain_device_number picks the device for jtag_chain.
    """
    ext = ".bit" if mode in ("jtag", "jtag_chain") else ".hex"
    if file:
        image = Path(file).expanduser().resolve()
    elif project:
        image = prj.resolve_project(project).output(ext)
    else:
        raise ValueError("Give either project or file")
    if not image.is_file():
        raise FileNotFoundError(f"{image} not found. Build the bitstream first (run_flow compile).")
    if image.suffix.lower() != ext:
        raise ValueError(f"Mode '{mode}' needs a {ext} file, got {image.name}")

    args = [str(image), "--mode", mode]
    if url:
        args += ["--url", url]
    if board_profile:
        args += ["--board_profile", board_profile]
    if chain_device_number is not None:
        args += ["--num", str(chain_device_number)]
    if jtag_clock_hz:
        args += ["--jtag_clock_freq", str(jtag_clock_hz)]
    if verify_method:
        args += ["--verify_method", verify_method]

    cmd, run_env = env.ftdi_program_command(args)

    def check(job) -> bool:
        text = "\n".join(job.lines).lower()
        return not re.search(r"\berror\b|aborting|failed|exception", text)

    job = jobs.start(
        kind="program",
        description=f"program {image.name} ({mode})",
        cmd=cmd,
        cwd=image.parent,
        env_vars=run_env,
        project_key="__programmer__",
        timeout=3600,
        success_check=check,
    )
    jobs.wait(job, wait_seconds)
    result = job.summary(tail=40)
    result["image"] = str(image)
    result["image_built"] = reports.mtime(image)
    return result


def check_install() -> int:
    """`efinity-mcp --check`: show what the server would use, without starting it."""
    try:
        info = get_efinity_info()
    except Exception as exc:
        print(f"Efinity not usable: {exc}")
        return 1
    for key in ("efinity_home", "version", "python", "default_project_dir", "job_logs_dir"):
        print(f"{key:20} {info[key]}")
    try:
        r = subprocess.run(
            [info["python"], "-c", "import sys; print(sys.version.split()[0])"], env=env.efinity_env(),
            capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW,
        )
        print(f"{'efinity python':20} {r.stdout.strip() or r.stderr.strip()[-300:]}")
        if r.returncode != 0:
            return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{'efinity python':20} failed to start: {exc}")
        return 1
    print(f"{'recent projects':20} {len(info['recent_projects'])}")
    print("OK")
    return 0


def main():
    import argparse

    from . import __version__

    parser = argparse.ArgumentParser(prog="efinity-mcp", description="MCP server for the Efinix Efinity FPGA toolchain (stdio).")
    parser.add_argument("--version", action="version", version=f"efinity-mcp {__version__}")
    parser.add_argument("--check", action="store_true", help="check the Efinity installation and exit")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check_install())
    mcp.run("stdio")


if __name__ == "__main__":
    main()
