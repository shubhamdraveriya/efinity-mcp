"""Locate the Efinity installation and build the environment its tools expect.

Mirrors what bin/setup.bat (Windows) and bin/setup.sh (Linux) do, so the server can call
Efinity's bundled Python directly instead of going through a shell.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", path.name)) or (0,)


def _is_efinity_home(path: Path) -> bool:
    return (path / "scripts" / "efx_run.py").is_file()


@lru_cache(maxsize=1)
def efinity_home() -> Path:
    """Return the Efinity install root.

    Order: EFINITY_HOME env var, then the newest version folder under the usual install roots
    (C:/Efinity/<ver> on Windows; ~/efinity/<ver>, /opt/efinity/<ver> on Linux).
    """
    env_home = os.environ.get("EFINITY_HOME")
    if env_home and _is_efinity_home(Path(env_home)):
        return Path(env_home).resolve()

    roots = [Path("C:/Efinity")] if IS_WINDOWS else []
    roots += [Path.home() / "efinity", Path.home() / "Efinity", Path("/opt/efinity"), Path("/opt/Efinity")]
    candidates = []
    for root in roots:
        if _is_efinity_home(root):
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(p for p in root.iterdir() if p.is_dir() and _is_efinity_home(p))
    if not candidates:
        raise RuntimeError(
            "Efinity installation not found. Set the EFINITY_HOME environment variable "
            "to the install folder (the one containing bin/ and scripts/)."
        )
    return max(candidates, key=_version_key).resolve()


def efinity_version() -> str:
    version_file = efinity_home() / "scripts" / "sw_version.txt"
    return version_file.read_text().strip() if version_file.is_file() else "unknown"


def _windows_python_dir() -> Path:
    """Efinity's bundled Python folder on Windows (python311 in 2024-2026 releases)."""
    home = efinity_home()
    dirs = sorted(
        (d for d in home.glob("python3*") if (d / "bin" / "python.exe").is_file()),
        key=_version_key,
        reverse=True,
    )
    if not dirs:
        raise RuntimeError(f"Efinity's bundled Python not found under {home} (expected python3xx/bin/python.exe)")
    return dirs[0]


def efinity_python() -> Path:
    if IS_WINDOWS:
        return _windows_python_dir() / "bin" / "python.exe"
    home = efinity_home()
    for cand in (home / "bin" / "python3", home / "bin" / "python"):
        if cand.is_file():
            return cand
    raise RuntimeError(f"Efinity's bundled Python not found under {home / 'bin'}")


def user_dir() -> Path:
    """Efinity's per-user settings folder (~/.efinity)."""
    return Path.home() / ".efinity"


def efinity_env(extra_pythonpath: list[Path] | None = None) -> dict[str, str]:
    """Environment equivalent to sourcing bin/setup.bat / bin/setup.sh, for Efinity subprocesses."""
    home = efinity_home()
    env = dict(os.environ)
    # Our own interpreter/venv settings must not leak into Efinity's Python.
    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        env.pop(var, None)

    home_fwd = home.as_posix()
    tool_dirs = [
        home / "bin",
        home / "pgm" / "bin",
        home / "debugger" / "bin",
        home / "debugger" / "svf_player" / "bin",
        home / "debugger" / "serdes_debug_tool" / "bin",
        home / "scripts",
        home / "ipm" / "bin" / "ip_packager",
    ]
    pythonpath: list[Path] = []
    if IS_WINDOWS:
        pydir = _windows_python_dir()
        tool_dirs.insert(0, pydir / "bin")
        env["PYTHONHOME"] = str(pydir)
        env["QT_PLUGIN_PATH"] = pydir.as_posix()
        env["QT_QPA_PLATFORM_PLUGIN_PATH"] = f"{pydir.as_posix()}/platforms"
        if "LOCALAPPDATA" in env:
            env["EFINITY_USER_DIR_INI"] = Path(env["LOCALAPPDATA"], "efinity", "user_dir.ini").as_posix()
        if env.get("PROCESSOR_ARCHITECTURE") == "ARM64":
            env["TCMALLOC_DISABLE_REPLACEMENT"] = "1"
    else:
        env["PYTHONHOME"] = home_fwd
        env["QT_PLUGIN_PATH"] = f"{home_fwd}/lib/plugins"
        env["EFINITY_USER_DIR_INI"] = str(Path.home() / ".local" / "share" / "efinity" / "user_dir.ini")
        env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        pythonpath.append(home / "lib")
    env["PATH"] = os.pathsep.join([str(p) for p in tool_dirs] + [env.get("PATH", "")])
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"

    env["EFINITY_HOME"] = home_fwd
    env["EFXPT_HOME"] = f"{home_fwd}/pt"
    env["EFXPGM_HOME"] = f"{home_fwd}/pgm"
    env["EFXDBG_HOME"] = f"{home_fwd}/debugger"
    env["EFXIPM_HOME"] = f"{home_fwd}/ipm"
    env["EFXIPPKG_HOME"] = f"{home_fwd}/ipm/bin/ip_packager"
    env["EFXSVF_HOME"] = f"{home_fwd}/debugger/svf_player"
    env["EFXSERDESDBG_HOME"] = f"{home_fwd}/debugger/serdes_debug_tool"
    env["QT_LOGGING_CONF"] = str(home / "bin" / "lc.ini")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"

    pythonpath = list(extra_pythonpath or []) + pythonpath
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(str(p) for p in pythonpath)
    return env


def efx_run_command(args: list[str]) -> list[str]:
    """Command line for scripts/efx_run.py (what bin/efx_run.bat / efx_run.sh runs)."""
    return [str(efinity_python()), str(efinity_home() / "scripts" / "efx_run.py"), *args]


def ftdi_program_command(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Command line + env for the command-line programmer (what pgm/bin/ftdi_pgm.bat runs)."""
    home = efinity_home()
    env = efinity_env(extra_pythonpath=[home / "debugger" / "bin", home / "pgm" / "bin"])
    cmd = [str(efinity_python()), str(home / "pgm" / "bin" / "efx_pgm" / "ftdi_program.py"), *args]
    return cmd, env


def logs_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / "efinity_mcp" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
