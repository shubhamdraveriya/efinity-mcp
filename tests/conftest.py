import shutil
from pathlib import Path

import pytest

from efinity_mcp import env

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_efinity(tmp_path, monkeypatch):
    """A minimal fake Efinity install: enough for path discovery, not for running tools."""
    home = tmp_path / "efinity" / "2026.1"
    (home / "scripts").mkdir(parents=True)
    (home / "scripts" / "efx_run.py").write_text("# fake\n")
    (home / "scripts" / "sw_version.txt").write_text("2026.1.999\n")
    if env.IS_WINDOWS:
        (home / "python311" / "bin").mkdir(parents=True)
        (home / "python311" / "bin" / "python.exe").write_text("")
    else:
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "python3").write_text("")
    monkeypatch.setenv("EFINITY_HOME", str(home))
    env.efinity_home.cache_clear()
    yield home
    env.efinity_home.cache_clear()


@pytest.fixture
def showcase(tmp_path):
    """A copy of a real compiled project's .xml, SDC and reports (led_showcase, Ti375N1156)."""
    dst = tmp_path / "led_showcase"
    shutil.copytree(FIXTURES / "led_showcase", dst)
    return dst
