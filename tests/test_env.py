import os

from efinity_mcp import env


def test_home_from_env_var(fake_efinity):
    assert env.efinity_home() == fake_efinity.resolve()
    assert env.efinity_version() == "2026.1.999"


def test_bundled_python_found(fake_efinity):
    py = env.efinity_python()
    assert py.is_file()
    assert py.is_relative_to(fake_efinity.resolve())


def test_environment_matches_setup_script(fake_efinity, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    monkeypatch.setenv("PYTHONPATH", "/leaky")
    e = env.efinity_env([fake_efinity / "pt" / "bin"])
    home = fake_efinity.resolve().as_posix()
    assert e["EFINITY_HOME"] == home
    assert e["EFXPT_HOME"] == f"{home}/pt"
    assert e["EFXPGM_HOME"] == f"{home}/pgm"
    assert "VIRTUAL_ENV" not in e
    # our own PYTHONPATH must not leak in; the requested entry comes first
    assert "/leaky" not in e["PYTHONPATH"]
    assert e["PYTHONPATH"].split(os.pathsep)[0] == str(fake_efinity / "pt" / "bin")
    assert e["PATH"].split(os.pathsep)[0].startswith(str(fake_efinity.resolve()))


def test_commands_use_bundled_python(fake_efinity):
    cmd = env.efx_run_command(["p.xml", "--flow", "compile"])
    assert cmd[0] == str(env.efinity_python())
    assert cmd[1].endswith("efx_run.py")
    pgm, pgm_env = env.ftdi_program_command(["--scan_usb"])
    assert pgm[1].endswith("ftdi_program.py")
    assert "pgm" in pgm_env["PYTHONPATH"]


def test_logs_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = env.logs_dir()
    assert path.is_dir()
    assert path.is_relative_to(tmp_path)
