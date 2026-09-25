"""Interface Designer (.peri.xml) access through pt_helper.py running in Efinity's Python."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from . import env
from . import project as prj
from .jobs import CREATE_NO_WINDOW

HELPER = Path(__file__).with_name("pt_helper.py")


def run_helper(proj: prj.Project, action: str, timeout: int = 300, create_if_missing: bool = False, **payload) -> dict:
    peri = prj.peri_design_file(proj)
    if not peri.is_file() and not create_if_missing:
        raise FileNotFoundError(
            f"{peri.name} not found. This project has no Interface Designer file yet; "
            "edit_interface creates one on its first change."
        )
    info = prj.read_project_info(proj)
    request = {
        "action": action,
        "peri": str(peri),
        "project_name": proj.name,
        "device": info["device"],
        "create_if_missing": create_if_missing,
        **payload,
    }
    tmp = Path(tempfile.gettempdir()) / f"efinity_mcp_pt_{uuid.uuid4().hex[:10]}"
    req_file, resp_file = tmp.with_suffix(".req.json"), tmp.with_suffix(".resp.json")
    req_file.write_text(json.dumps(request), encoding="utf-8")
    cmd = [str(env.efinity_python()), str(HELPER), str(req_file), str(resp_file)]
    started = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=str(proj.dir), env=env.efinity_env([env.efinity_home() / "pt" / "bin"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
        if not resp_file.is_file():
            tail = ((r.stdout or "") + (r.stderr or "")).strip()[-3000:]
            raise RuntimeError(f"Interface Designer helper exited with code {r.returncode} and no result:\n{tail}")
        resp = json.loads(resp_file.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Interface Designer helper timed out after {timeout}s") from None
    finally:
        req_file.unlink(missing_ok=True)
        resp_file.unlink(missing_ok=True)

    if not resp.get("ok"):
        msg = resp.get("error") or "Interface Designer helper failed"
        if resp.get("tool_messages"):
            msg += "\nEfinity messages:\n" + "\n".join(resp["tool_messages"][:15])
        raise RuntimeError(msg)
    result = resp["result"]
    if isinstance(result, dict):
        if resp.get("tool_messages"):
            result.setdefault("efinity_messages", resp["tool_messages"])
        result.setdefault("elapsed_s", round(time.time() - started, 1))
    return result


def gui_running() -> bool:
    cmd = ["tasklist", "/FI", "IMAGENAME eq efinity.exe", "/NH"] if env.IS_WINDOWS else ["pgrep", "-x", "efinity"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if env.IS_WINDOWS:
        return "efinity.exe" in (r.stdout or "").lower()
    return r.returncode == 0
