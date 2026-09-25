import pytest

from efinity_mcp import project as prj


def _new_project(tmp_path):
    d = tmp_path / "blinky"
    d.mkdir()
    (d / "blinky.v").write_text("module blinky(input clk, output led); assign led = clk; endmodule\n")
    (d / "extra.v").write_text("module extra; endmodule\n")
    (d / "blinky.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    return d, prj.create_project("blinky", str(d), "Titanium", "Ti60F225", "C4", "blinky", ["blinky.v"], ["blinky.sdc"])


def test_create_and_read(fake_efinity, tmp_path):
    d, proj = _new_project(tmp_path)
    info = prj.read_project_info(proj)
    assert info["name"] == "blinky"
    assert (info["family"], info["device"], info["timing_model"]) == ("Titanium", "Ti60F225", "C4")
    assert info["top_module"] == "blinky"
    assert [f["file"] for f in info["design_files"]] == ["blinky.v"]
    assert info["sdc_files"] == ["blinky.sdc"]
    assert info["sw_version"] == "2026.1.999"
    assert not (d / "blinky.xml.bak").exists()


def test_create_refuses_to_overwrite(fake_efinity, tmp_path):
    d, _ = _new_project(tmp_path)
    with pytest.raises(FileExistsError):
        prj.create_project("blinky", str(d), "Titanium", "Ti60F225", "C4", "blinky", ["blinky.v"])


def test_create_rejects_missing_file(fake_efinity, tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        prj.create_project("p", str(d), "Titanium", "Ti60F225", "C4", "top", ["nope.v"])
    assert not (d / "p.xml").exists()


def test_update_project(fake_efinity, tmp_path):
    d, proj = _new_project(tmp_path)
    out = prj.update_project(proj, add_files=["extra.v"], top_module="extra", remove_sdc=["blinky.sdc"])
    assert out["changed"] and (d / "blinky.xml.bak").is_file()
    info = prj.read_project_info(proj)
    assert [f["file"] for f in info["design_files"]] == ["blinky.v", "extra.v"]
    assert info["top_module"] == "extra"
    assert info["sdc_files"] == []
    with pytest.raises(ValueError):
        prj.update_project(proj, remove_files=["not_there.v"])


def test_resolve_by_folder_and_file(fake_efinity, tmp_path):
    d, proj = _new_project(tmp_path)
    assert prj.resolve_project(str(d)).xml == proj.xml
    assert prj.resolve_project(str(proj.xml)).xml == proj.xml


def test_peri_file_registration(fake_efinity, tmp_path):
    d, proj = _new_project(tmp_path)
    peri = prj.peri_design_file(proj)
    assert peri == (d / "blinky.peri.xml").resolve()
    assert prj.read_project_info(proj)["interface_files"] == []
    assert prj.register_peri_file(proj, peri) is not None
    assert prj.read_project_info(proj)["interface_files"] == ["blinky.peri.xml"]
    assert prj.register_peri_file(proj, peri) is None  # already registered: no rewrite


def test_real_project_file(showcase):
    proj = prj.resolve_project(str(showcase))
    info = prj.read_project_info(proj)
    assert info["device"] == "Ti375N1156"
    assert info["top_module"] == "led_showcase_top"
    assert info["interface_files"] == ["led_showcase.peri.xml"]
    assert prj.debug_profile_issue(proj) is None
