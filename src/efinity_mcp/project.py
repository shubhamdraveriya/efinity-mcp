"""Efinity project (.xml) discovery, reading and editing."""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import env

NS = "http://www.efinixinc.com/enf_proj"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("efx", NS)
ET.register_namespace("xsi", XSI)


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


@dataclass
class Project:
    xml: Path

    @property
    def name(self) -> str:
        root = ET.parse(self.xml).getroot()
        return root.get("name") or self.xml.stem

    @property
    def dir(self) -> Path:
        return self.xml.parent

    @property
    def outflow(self) -> Path:
        return self.dir / "outflow"

    def output(self, suffix: str) -> Path:
        """Path of a flow output such as '.timing.rpt' or '.bit'."""
        return self.outflow / f"{self.name}{suffix}"


def _is_project_xml(path: Path) -> bool:
    if path.suffix.lower() != ".xml" or not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(600)
    except OSError:
        return False
    return b"efx:project" in head and b"enf_proj" in head


def recent_projects() -> list[Path]:
    """Recent projects from the Efinity GUI (~/.efinity/app.ini), newest first."""
    ini = env.user_dir() / "app.ini"
    if not ini.is_file():
        return []
    found = []
    for line in ini.read_text(errors="replace").splitlines():
        m = re.match(r"^project(\d+)=(.+)$", line.strip())
        if m:
            path = Path(m.group(2).strip().strip('"').replace("\\\\", "\\"))
            found.append((int(m.group(1)), path))
    return [p for _, p in sorted(found)]


def default_project_root() -> Path:
    ini = env.user_dir() / "app.ini"
    if ini.is_file():
        m = re.search(r"^project_path=(.+)$", ini.read_text(errors="replace"), re.M)
        if m:
            return Path(m.group(1).strip().replace("\\\\", "\\"))
    return env.user_dir() / "project"


def scan_projects(root: Path, max_depth: int = 2) -> list[Path]:
    results = []
    if not root.is_dir():
        return results

    def walk(folder: Path, depth: int):
        try:
            entries = list(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_file() and _is_project_xml(entry):
                results.append(entry)
            elif entry.is_dir() and depth < max_depth and not entry.name.startswith(
                ("outflow", "work_", ".", "ip", "ooc")
            ):
                walk(entry, depth + 1)

    walk(root, 0)
    return sorted(results)


def resolve_project(ref: str) -> Project:
    """Accept a project .xml path, a project folder, or a project name."""
    if not ref:
        raise ValueError("project is required (path to .xml, project folder, or project name)")
    path = Path(ref).expanduser()
    if path.is_file() and _is_project_xml(path):
        return Project(path.resolve())
    if path.is_dir():
        preferred = path / f"{path.name}.xml"
        if _is_project_xml(preferred):
            return Project(preferred.resolve())
        xmls = [p for p in path.glob("*.xml") if _is_project_xml(p)]
        if len(xmls) == 1:
            return Project(xmls[0].resolve())
        if len(xmls) > 1:
            raise ValueError(f"Several project files in {path}: {[p.name for p in xmls]}. Pass the .xml path.")
        raise ValueError(f"No Efinity project .xml found in {path}")

    name = path.stem if path.suffix.lower() == ".xml" else ref
    candidates = recent_projects() + scan_projects(default_project_root())
    for cand in candidates:
        if cand.stem.lower() == name.lower() and _is_project_xml(cand):
            return Project(cand.resolve())
    raise ValueError(f"Project '{ref}' not found. Use list_projects, or pass the full path to the project .xml.")


def _params(section: ET.Element | None) -> dict[str, str]:
    if section is None:
        return {}
    return {p.get("name"): p.get("value") for p in section.findall(_q("param"))}


def read_project_info(proj: Project) -> dict:
    root = ET.parse(proj.xml).getroot()
    dev = root.find(_q("device_info"))
    design = root.find(_q("design_info"))

    def attr(parent, tag, key="name"):
        el = parent.find(_q(tag)) if parent is not None else None
        return el.get(key) if el is not None else None

    files = []
    if design is not None:
        for f in design.findall(_q("design_file")):
            if f.get("name"):
                files.append({"file": f.get("name"), "version": f.get("version"), "library": f.get("library")})

    def names(section, tag):
        sec = root.find(_q(section))
        if sec is None:
            return []
        return [e.get("name") for e in sec.findall(_q(tag)) if e.get("name")]

    return {
        "name": proj.name,
        "project_file": str(proj.xml),
        "project_dir": str(proj.dir),
        "sw_version": root.get("sw_version"),
        "family": attr(dev, "family"),
        "device": attr(dev, "device"),
        "timing_model": attr(dev, "timing_model"),
        "top_module": attr(design, "top_module"),
        "verilog_version": design.get("def_veri_version") if design is not None else None,
        "vhdl_version": design.get("def_vhdl_version") if design is not None else None,
        "design_files": files,
        "interface_files": names("interface_info", "peri_file"),
        "sdc_files": names("constraint_info", "sdc_file"),
        "isf_files": names("constraint_info", "isf_file"),
        "simulation_files": names("sim_info", "sim_file"),
        "ip_files": [e.get("name") for e in root.iter(_q("ip")) if e.get("name")],
        "synthesis_options": _params(root.find(_q("synthesis"))),
        "pnr_options": _params(root.find(_q("place_and_route"))),
        "bitstream_options": _params(root.find(_q("bitstream_generation"))),
        "debugger": _params(root.find(_q("debugger"))),
    }


def debug_profile_issue(proj: Project) -> str | None:
    """If the debugger auto-instantiation flow is on but its generated core is missing, say so."""
    root = ET.parse(proj.xml).getroot()
    dbg = _params(root.find(_q("debugger")))
    if dbg.get("auto_instantiation") != "on" or not dbg.get("profile"):
        return None
    work = proj.dir / dbg.get("work_dir", "work_dbg")
    if not (proj.dir / dbg["profile"]).is_file():
        return f"Debug profile {dbg['profile']} is enabled but missing."
    if not (work / "debug_top.v").is_file():
        return (
            f"Debugger auto-instantiation is on but {work / 'debug_top.v'} is missing. "
            "Open the project in the Efinity GUI Debug Wizard and generate the debug core once, "
            "or disable auto-instantiation."
        )
    return None


def peri_design_file(proj: Project) -> Path:
    """The project's Interface Designer file (peri_file, else default_peri_file, else <name>.peri.xml)."""
    iface = ET.parse(proj.xml).getroot().find(_q("interface_info"))
    for tag in ("peri_file", "default_peri_file"):
        el = iface.find(_q(tag)) if iface is not None else None
        if el is not None and el.get("name"):
            return (proj.dir / el.get("name")).resolve()
    return proj.dir / f"{proj.name}.peri.xml"


def register_peri_file(proj: Project, peri: Path) -> str | None:
    """Make sure the project lists `peri` as its interface file. Returns the backup path if it wrote."""
    tree = ET.parse(proj.xml)
    root = tree.getroot()
    iface = root.find(_q("interface_info"))
    if iface is None:
        iface = ET.SubElement(root, _q("interface_info"))
    name = peri.name if peri.parent.resolve() == proj.dir.resolve() else peri.as_posix()
    if any(e.get("name") == name for e in iface.findall(_q("peri_file"))):
        return None
    if iface.find(_q("default_peri_file")) is None:
        ET.SubElement(iface, _q("default_peri_file"), {"name": name})
    ET.SubElement(iface, _q("peri_file"), {"name": name})
    return _write(proj, tree)


def _write(proj: Project, tree: ET.ElementTree) -> str:
    backup = proj.xml.with_suffix(".xml.bak")
    shutil.copy2(proj.xml, backup)
    ET.indent(tree, space="    ")
    tree.write(proj.xml, encoding="UTF-8", xml_declaration=True)
    return str(backup)


def update_project(
    proj: Project,
    add_files: list[str] | None = None,
    remove_files: list[str] | None = None,
    top_module: str | None = None,
    family: str | None = None,
    device: str | None = None,
    timing_model: str | None = None,
    add_sdc: list[str] | None = None,
    remove_sdc: list[str] | None = None,
) -> dict:
    tree = ET.parse(proj.xml)
    root = tree.getroot()
    changes = []

    design = root.find(_q("design_info"))
    if design is None:
        raise ValueError("Project file has no design_info section")

    def rel(p: str) -> str:
        path = Path(p)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(proj.dir).as_posix()
            except ValueError:
                return path.as_posix()
        return path.as_posix()

    existing = {f.get("name"): f for f in design.findall(_q("design_file"))}
    for f in add_files or []:
        name = rel(f)
        if not (proj.dir / name).is_file() and not Path(name).is_file():
            raise FileNotFoundError(f"Design file not found: {f}")
        if name in existing:
            continue
        # drop the template's empty placeholder entry, if any
        if "" in existing:
            design.remove(existing.pop(""))
        el = ET.Element(_q("design_file"), {"name": name, "version": "default", "library": "default"})
        # keep design_file entries grouped after top_module / other design_files
        idx = max((i for i, c in enumerate(design) if c.tag in (_q("design_file"), _q("top_module"))), default=-1)
        design.insert(idx + 1, el)
        existing[name] = el
        changes.append(f"added design file {name}")

    for f in remove_files or []:
        name = rel(f)
        if name not in existing:
            raise ValueError(f"{name} is not in the project. Current files: {[k for k in existing if k]}")
        design.remove(existing.pop(name))
        changes.append(f"removed design file {name}")

    if top_module:
        el = design.find(_q("top_module"))
        if el is None:
            el = ET.SubElement(design, _q("top_module"))
        el.set("name", top_module)
        changes.append(f"top module -> {top_module}")

    dev = root.find(_q("device_info"))
    for tag, value in (("family", family), ("device", device), ("timing_model", timing_model)):
        if value:
            el = dev.find(_q(tag))
            el.set("name", value)
            changes.append(f"{tag} -> {value}")

    cons = root.find(_q("constraint_info"))
    if (add_sdc or remove_sdc) and cons is None:
        cons = ET.SubElement(root, _q("constraint_info"))
    sdcs = {e.get("name"): e for e in cons.findall(_q("sdc_file"))} if cons is not None else {}
    for f in add_sdc or []:
        name = rel(f)
        if name not in sdcs:
            cons.insert(0, ET.Element(_q("sdc_file"), {"name": name}))
            changes.append(f"added SDC {name}")
    for f in remove_sdc or []:
        name = rel(f)
        if name in sdcs:
            cons.remove(sdcs[name])
            changes.append(f"removed SDC {name}")

    if not changes:
        return {"changed": False, "message": "Nothing to change"}
    backup = _write(proj, tree)
    return {
        "changed": True,
        "changes": changes,
        "backup": backup,
        "note": "If the project is open in the Efinity GUI, reload it there so the GUI doesn't overwrite these edits.",
    }


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
) -> Project:
    proj_dir = Path(directory).expanduser().resolve()
    proj_dir.mkdir(parents=True, exist_ok=True)
    xml_path = proj_dir / f"{name}.xml"
    if xml_path.exists():
        raise FileExistsError(f"{xml_path} already exists")

    root = ET.Element(
        _q("project"),
        {
            "name": name,
            "description": "",
            "sw_version": env.efinity_version(),
            f"{{{XSI}}}schemaLocation": f"{NS} enf_proj.xsd",
        },
    )
    dev = ET.SubElement(root, _q("device_info"))
    ET.SubElement(dev, _q("family"), {"name": family})
    ET.SubElement(dev, _q("device"), {"name": device})
    ET.SubElement(dev, _q("timing_model"), {"name": timing_model})
    design = ET.SubElement(
        root, _q("design_info"), {"def_veri_version": verilog_version, "def_vhdl_version": vhdl_version, "unified_flow": "false"}
    )
    ET.SubElement(design, _q("top_module"), {"name": top_module})
    ET.SubElement(design, _q("top_vhdl_arch"), {"name": ""})
    iface = ET.SubElement(root, _q("interface_info"))
    ET.SubElement(iface, _q("default_peri_file"), {"name": f"{name}.peri.xml"})
    ET.SubElement(root, _q("constraint_info"))
    for tag in ("sim_info", "misc_info", "ooc_info", "ip_info"):
        ET.SubElement(root, _q(tag))
    syn = ET.SubElement(root, _q("synthesis"), {"tool_name": "efx_map"})
    ET.SubElement(syn, _q("param"), {"name": "work_dir", "value": "work_syn", "value_type": "e_string"})
    ET.SubElement(syn, _q("param"), {"name": "write_efx_verilog", "value": "on", "value_type": "e_bool"})
    pnr = ET.SubElement(root, _q("place_and_route"), {"tool_name": "efx_pnr"})
    ET.SubElement(pnr, _q("param"), {"name": "work_dir", "value": "work_pnr", "value_type": "e_string"})
    pgm = ET.SubElement(root, _q("bitstream_generation"), {"tool_name": "efx_pgm"})
    for pname, value, vtype in (
        ("mode", "active", "e_option"),
        ("width", "1", "e_option"),
        ("generate_bit", "on", "e_bool"),
        ("generate_hex", "on", "e_bool"),
    ):
        ET.SubElement(pgm, _q("param"), {"name": pname, "value": value, "value_type": vtype})

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)

    proj = Project(xml_path)
    try:
        update_project(proj, add_files=design_files, add_sdc=sdc_files)
    except Exception:
        xml_path.unlink(missing_ok=True)
        raise
    xml_path.with_suffix(".xml.bak").unlink(missing_ok=True)
    return proj
