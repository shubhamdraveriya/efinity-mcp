"""Parsers for the reports Efinity writes into a project's outflow/ folder."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .project import Project

TOOL_REPORT_NS = "{http://www.efinixinc.com/tool_report}"

# Section markers used across Efinity text reports:
#   ---------- 3. Path Details for Max Critical Paths (begin) ----------
#   ### ### EFX_FF CE enables (begin) ### ### ###
_SECTION_BEGIN = re.compile(r"^\s*(?:-{5,}|(?:###\s*)+)\s*(?:\d+\.\s*)?(.+?)\s*\(begin\)\s*(?:-+|(?:###\s*)+)\s*$")
_SECTION_END = re.compile(r"^\s*(?:-{5,}|(?:###\s*)+)\s*(?:\d+\.\s*)?(.+?)\s*\(end\)\s*(?:-+|(?:###\s*)+)\s*$")


def read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not found in {path.parent}. Has the flow that produces it been run?")
    return path.read_text(encoding="utf-8", errors="replace")


def mtime(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.is_file() else None


# --------------------------------------------------------------------------- sections

def list_sections(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _SECTION_BEGIN.match(line))]


def get_section(text: str, name: str) -> str | None:
    """Body of the first section whose title contains `name` (case-insensitive), or whose number matches."""
    lines = text.splitlines()
    want = name.strip().lower()
    for i, line in enumerate(lines):
        m = _SECTION_BEGIN.match(line)
        if not m:
            continue
        numbered = re.match(r"^\s*-+\s*(\d+)\.", line)
        if want in m.group(1).lower() or (numbered and numbered.group(1) == want):
            body = []
            for nxt in lines[i + 1:]:
                if _SECTION_END.match(nxt):
                    break
                body.append(nxt)
            return "\n".join(body).strip("\n")
    return None


# --------------------------------------------------------------------------- tool_report XML

def parse_tool_report(path: Path) -> dict:
    """Parse *.rpt.xml (route.rpt.xml, pt.rpt.xml, ...) into {group: {item: value}}."""
    root = ET.parse(path).getroot()
    out = {}
    for group in root.iter(f"{TOOL_REPORT_NS}group"):
        items = {}
        for d in group.findall(f"{TOOL_REPORT_NS}group_data"):
            value = d.get("value")
            if d.get("severity") in ("error", "warning"):
                value = f"{value} ({d.get('severity')})"
            items[d.get("name")] = value
        out[group.get("name")] = items
    return out


# --------------------------------------------------------------------------- utilization

def parse_res_csv(path: Path) -> list[dict]:
    """Synthesis resource estimates (<proj>.res.csv)."""
    lines = [line for line in read_text(path).splitlines() if "\t" in line and not line.startswith("sep=")]
    if not lines:
        return []
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter="\t"))
    header = [h.strip() for h in rows[0]]
    return [
        {h: c.strip() for h, c in zip(header, row, strict=False) if h}
        for row in rows[1:]
        if any(c.strip() for c in row)
    ]


def parse_hier_util(path: Path, max_depth: int | None = None) -> list[dict]:
    """Post-place hierarchical utilization (<proj>.hier_util.rpt). Values are 'total(self)'."""
    rows, header = [], None
    for line in read_text(path).splitlines():
        if not line.startswith("|"):
            continue
        cells = [c for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.strip() for c in cells]
            continue
        name_cell = cells[0]
        depth = len(name_cell) - len(name_cell.lstrip(" ")) - 1
        if max_depth is not None and depth > max_depth:
            continue
        row = {"instance": name_cell.strip().lstrip("+"), "depth": depth}
        row.update({h: c.strip() for h, c in zip(header[1:], cells[1:], strict=False)})
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- timing

_CLOCK_TARGET = re.compile(r"^(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(\{.*?\}(?:\s*\{.*?\})?)\s+(\{.*\})\s*$")
_CLOCK_MAX = re.compile(r"^(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+\((\S+)\)\s*$")
_CLOCK_REL = re.compile(r"^(\S+)\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+\((\S+)\)\s*$")


def _header_value(text: str, key: str) -> str | None:
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def parse_timing_summary(text: str) -> dict:
    summary = {
        "timing_model": _header_value(text, "Timing Model"),
        "sdc_file": _header_value(text, "SDC Filename"),
        "constrained_clocks": [],
        "max_frequency": [],
        "setup_relationships": [],
        "hold_relationships": [],
    }
    freq = get_section(text, "Clock Frequency Summary") or ""
    mode = None
    for line in freq.splitlines():
        if "User target constrained clocks" in line:
            mode = "target"
        elif "Maximum possible analyzed clocks frequency" in line:
            mode = "max"
        elif mode == "target" and (m := _CLOCK_TARGET.match(line.strip())):
            summary["constrained_clocks"].append(
                {"clock": m[1], "period_ns": float(m[2]), "frequency_mhz": float(m[3]), "waveform": m[4], "targets": m[5]}
            )
        elif mode == "max" and (m := _CLOCK_MAX.match(line.strip())):
            summary["max_frequency"].append(
                {"clock": m[1], "period_ns": float(m[2]), "fmax_mhz": float(m[3]), "edge": m[4]}
            )
        elif line.startswith("Geomean max period"):
            summary["geomean_max_period_ns"] = float(line.split(":")[1])

    rel = get_section(text, "Clock Relationship Summary") or ""
    target = None
    for line in rel.splitlines():
        if line.startswith("Setup"):
            target = "setup_relationships"
        elif line.startswith("Hold"):
            target = "hold_relationships"
        elif target and (m := _CLOCK_REL.match(line.strip())):
            summary[target].append(
                {"launch": m[1], "capture": m[2], "constraint_ns": float(m[3]), "slack_ns": float(m[4]), "edge": m[5]}
            )

    for kind in ("setup", "hold"):
        rels = summary[f"{kind}_relationships"]
        if rels:
            worst = min(rels, key=lambda r: r["slack_ns"])
            summary[f"worst_{kind}_slack_ns"] = worst["slack_ns"]
            summary[f"worst_{kind}_clocks"] = f"{worst['launch']} -> {worst['capture']}"
    failing = [r for k in ("setup_relationships", "hold_relationships") for r in summary[k] if r["slack_ns"] < 0]
    summary["timing_met"] = not failing
    if summary["sdc_file"] in (None, "Not Specified"):
        summary["note"] = (
            "No SDC file: Efinity applies a default 1 ns (1000 MHz) constraint to every clock, so negative "
            "slack here usually means 'unconstrained', not a real failure. Add an SDC with create_clock."
        )
    return summary


_PATH_FIELDS = {
    "Path Begin": "begin",
    "Path End": "end",
    "Launch Clock": "launch_clock",
    "Capture Clock": "capture_clock",
    "Slack": "slack_ns",
    "Delay": "data_delay_ns",
    "Logic Level": "logic_levels",
    "Non-global nets on path": "non_global_nets",
    "Global nets on path": "global_nets",
    "End-of-path arrival time": "arrival_ns",
    "End-of-path required time": "required_ns",
    "Launch Clock Path Delay": "launch_clock_delay_ns",
    "Capture Clock Path Delay": "capture_clock_delay_ns",
    "Clock Uncertainty": "clock_uncertainty_ns",
    "Constraint": "constraint_ns",
}


def parse_timing_paths(text: str, analysis: str = "setup", include_detail: bool = False) -> list[dict]:
    section_name = "Max Critical Paths" if analysis == "setup" else "Min Critical Paths"
    body = get_section(text, section_name)
    if body is None:
        return []
    paths, current, clocks, detail = [], None, None, []

    def flush():
        if current is not None:
            if include_detail:
                current["detail"] = "\n".join(detail).strip()
            paths.append(current)

    for line in body.splitlines():
        if m := re.match(r"^Path Detail Report \((.+)\)", line):
            clocks = m.group(1)
            continue
        if m := re.match(r"^\+{4} Path (\d+) \+", line):
            flush()
            current, detail = {"clock_pair": clocks, "index": int(m.group(1))}, []
            continue
        if current is None:
            continue
        detail.append(line)
        m = re.match(r"^[+\- ]*\s*([A-Za-z][A-Za-z\- ]+?)\s*:\s*(.+?)\s*$", line)
        if m and m.group(1) in _PATH_FIELDS and _PATH_FIELDS[m.group(1)] not in current:
            key, value = _PATH_FIELDS[m.group(1)], m.group(2)
            if key.endswith("_ns") or key in ("logic_levels", "non_global_nets", "global_nets"):
                num = re.match(r"-?[\d.]+", value)
                value = float(num.group()) if num else value
            current[key] = value
    flush()
    return sorted(paths, key=lambda p: p.get("slack_ns", 0.0))


# --------------------------------------------------------------------------- messages

_OUT_MSG = re.compile(r"^(CRITICAL WARNING|CRITICAL|ERROR|WARNING)\s*:\s*(.+)$")
_EFX_MSG = re.compile(r"^\[(EFX-\d+)\s+([A-Z\-]+)\]\s*(.+)$")
_SEVERITY_RANK = {"error": 3, "critical": 2, "warning": 1, "info": 0}


def _norm_severity(raw: str) -> str:
    raw = raw.upper()
    if "ERROR" in raw:
        return "error"
    if "CRITICAL" in raw:
        return "critical"
    if "WARN" in raw:
        return "warning"
    return "info"


def _latest_run(text: str) -> str:
    marker = "// Efinity Synthesis Started"
    idx = text.rfind(marker)
    return text[idx:] if idx >= 0 else text


def collect_messages(proj: Project, min_severity: str = "warning", extra_logs: list[Path] | None = None) -> list[dict]:
    """Messages from the latest run: per-stage *.out logs plus the latest synthesis run in <proj>.err/.warn.log."""
    threshold = _SEVERITY_RANK[min_severity]
    messages, seen = [], set()

    def add(stage, severity, text, source):
        key = (severity, text)
        if _SEVERITY_RANK[severity] >= threshold and key not in seen:
            seen.add(key)
            messages.append({"severity": severity, "stage": stage, "message": text, "source": source})

    name = proj.name
    # <proj>.map.out already holds the latest synthesis messages; the cumulative
    # .err/.warn logs are only a fallback when it is missing.
    synth_logs = [] if proj.output(".map.out").is_file() else [proj.output(".err.log"), proj.output(".warn.log")]
    for log in synth_logs:
        if log.is_file():
            for line in _latest_run(read_text(log)).splitlines():
                if m := _EFX_MSG.match(line.strip()):
                    add("synthesis", _norm_severity(m[2]), f"[{m[1]}] {m[3]}", log.name)

    if proj.outflow.is_dir():
        for out in sorted(proj.outflow.glob(f"{name}*.out")):
            stage = out.name[len(name):].strip(".").removesuffix(".out") or "flow"
            if stage == "sta":
                # Written only by interactive sta_tclsh sessions (run_sta_tcl), not by the build.
                continue
            for line in read_text(out).splitlines():
                if m := _OUT_MSG.match(line.strip()):
                    add(stage, _norm_severity(m[1]), m[2], out.name)

    for log in extra_logs or []:
        if log.is_file():
            for line in read_text(log).splitlines():
                line = line.strip()
                if m := _OUT_MSG.match(line):
                    add("console", _norm_severity(m[1]), m[2], log.name)
                elif re.search(r"\bFAIL\b|Traceback|Exception|No such file", line):
                    add("console", "error", line, log.name)

    messages.sort(key=lambda m: -_SEVERITY_RANK[m["severity"]])
    return messages


# --------------------------------------------------------------------------- IO / pinout

def parse_pinout(path: Path) -> list[dict]:
    """Assigned user signals from <proj>.pinout.csv."""
    lines = read_text(path).splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("Pin Number,")), None)
    if start is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [
        {k: (v or "").strip() for k, v in row.items() if k}
        for row in reader
        if (row.get("Signal Name") or "").strip()
    ]


def parse_interface_csv(path: Path) -> list[dict]:
    """Core <-> periphery interface signals (<proj>.interface.csv)."""
    rows = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        rows.append(
            {
                "type": parts[0],
                "x": parts[1],
                "y": parts[2],
                "z": parts[3],
                "clock": parts[4],
                "signal": parts[5] if len(parts) > 5 else "",
            }
        )
    return rows


def parse_proj_state(path: Path) -> dict:
    state = {}
    if path.is_file():
        for line in read_text(path).splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                state[k.strip()] = v.strip()
    return state
