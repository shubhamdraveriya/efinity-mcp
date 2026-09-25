"""End to end against a real Efinity install. Run with:  pytest -m efinity -v

Creates a scratch project in a temp folder, builds its Interface Designer design from scratch,
checks that bad edits are rejected without touching the file, compiles, and reads the results.
Uses a Titanium Ti375N1156 by default; set EFINITY_MCP_TEST_DEVICE=family,device,speed to change.
"""

import os
import re

import pytest

from efinity_mcp import env

pytestmark = pytest.mark.efinity


@pytest.fixture(scope="module")
def srv():
    env.efinity_home.cache_clear()
    try:
        env.efinity_home()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    from efinity_mcp import server

    return server


@pytest.fixture(scope="module")
def blinky(srv, tmp_path_factory):
    family, device, speed = os.environ.get("EFINITY_MCP_TEST_DEVICE", "Titanium,Ti375N1156,C4").split(",")
    d = tmp_path_factory.mktemp("efinity_e2e") / "blinky"
    d.mkdir()
    (d / "blinky.v").write_text(
        "module blinky(input clk, input rst_n, output [3:0] led);\n"
        "  reg [25:0] c = 0;\n"
        "  always @(posedge clk) if (!rst_n) c <= 0; else c <= c + 1'b1;\n"
        "  assign led = c[25:22];\nendmodule\n"
    )
    (d / "blinky.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    srv.create_project("blinky", str(d), family, device, speed, "blinky", ["blinky.v"], ["blinky.sdc"])
    return d


def test_efinity_found(srv):
    info = srv.get_efinity_info()
    assert info["version"] != "unknown"


def test_build_interface(srv, blinky):
    gclk = srv.list_device_resources(str(blinky), feature="GCLK", limit=100)["resources"]
    assert gclk, "no free GCLK-capable pin"
    clk = gclk[0]
    same_bank = srv.list_device_resources(str(blinky), bank=clk["bank"], limit=50)["resources"]
    pins = [r["pin"] for r in same_bank if r["pin"] != clk["pin"]][:5]
    assert len(pins) == 5
    std ="1.8 V LVCMOS" if clk["bank"][0].isdigit() else "3.3 V LVCMOS"

    ops = [
        {"op": "create_gpio", "name": "clk", "mode": "clock_input", "pin": clk["pin"], "properties": {"IO_STANDARD": std}},
        {"op": "create_gpio", "name": "rst_n", "mode": "input", "pin": pins[0],
         "properties": {"IO_STANDARD": std, "PULL_OPTION": "WEAK_PULLUP"}},
        {"op": "create_gpio", "name": "led", "mode": "output", "msb": 3, "lsb": 0, "properties": {"IO_STANDARD": std}},
    ]
    # the led bus has no pins yet: the design check fails and nothing may be saved
    out = srv.edit_interface(str(blinky), ops)
    assert out["saved"] is False and out["design_check"]["new_errors"]
    assert not (blinky / "blinky.peri.xml").exists()

    ops += [{"op": "assign_pin", "name": f"led[{i}]", "pin": pins[i + 1]} for i in range(4)]
    out = srv.edit_interface(str(blinky), ops)
    assert out["saved"] is True, out
    assert (blinky / "blinky.peri.xml").is_file()
    assert srv.get_project_info(str(blinky))["interface_files"] == ["blinky.peri.xml"]


def test_bad_edits_are_rejected(srv, blinky):
    peri = blinky / "blinky.peri.xml"
    before = peri.read_bytes()
    bad = [
        {"op": "set_properties", "name": "rst_n", "properties": {"IO_STANDARD": "BOGUS"}},
        {"op": "set_properties", "name": "rst_n", "properties": {"PULL_OPTON": "WEAK_PULLUP"}},
        {"op": "set_properties", "name": "rst_nn", "properties": {"PULL_OPTION": "NONE"}},
        {"op": "assign_pin", "name": "rst_n", "pin": "ZZ99"},
        {"op": "assign_pin", "name": "rst_n", "pin": srv.get_interface_block(str(blinky), "led[0]")["pin"]},
        {"op": "set_bank_voltage", "bank": "NOPE", "voltage": "1.8"},
    ]
    for op in bad:
        out = srv.edit_interface(str(blinky), [op])
        assert out["saved"] is False and "failed_operation" in out, op
    assert peri.read_bytes() == before


def test_reads_never_rewrite_an_older_file(srv, blinky):
    # Efinity's API upgrades (and rewrites) a design saved by an older version as soon as it loads
    # it. Read-only tools must leave the file exactly as it was.
    peri = blinky / "blinky.peri.xml"
    current = peri.read_text()
    old = re.sub(r'db_version="\d+"', 'db_version="20252999"', current, count=1)
    assert old != current
    peri.write_text(old)
    stamp = peri.stat().st_mtime
    out = srv.get_interface_design(str(blinky))
    assert peri.read_text() == old and peri.stat().st_mtime == stamp
    assert "file_format_note" in out
    peri.write_text(current)


def test_block_details_and_dry_run(srv, blinky):
    block = srv.get_interface_block(str(blinky), "rst_n")
    assert block["properties"]["PULL_OPTION"] == "WEAK_PULLUP"
    assert "WEAK_PULLUP" in block["allowed_values"]["PULL_OPTION"]
    before = (blinky / "blinky.peri.xml").read_bytes()
    out = srv.edit_interface(str(blinky), [{"op": "set_properties", "name": "led", "properties": {"DRIVE_STRENGTH": "8"}}], dry_run=True)
    assert out["saved"] is False and out["applied"][0]["changes"]
    assert (blinky / "blinky.peri.xml").read_bytes() == before
    assert srv.check_interface(str(blinky))["passed"]


def test_compile_and_results(srv, blinky):
    job = srv.run_flow(str(blinky), "compile", wait_seconds=1200)
    assert job["state"] == "succeeded", job
    assert job["interface_check"]["Missing Interface Pins"] == "0"
    assert srv.get_timing_summary(str(blinky))["timing_met"]
    signals = {p["Signal Name"].split(".")[0] for p in srv.get_io_assignments(str(blinky))["pins"]}
    assert {"clk", "rst_n", "led[0]", "led[3]"} <= signals
    status = srv.get_build_status(str(blinky))
    assert status["bitstream_stale"] is False and status["message_counts"]["error"] == 0


def test_sta_tcl(srv, blinky):
    out = srv.run_sta_tcl(str(blinky), "report_clocks\ncheck_timing")
    assert out["state"] == "succeeded" and "clk" in out["output"]
