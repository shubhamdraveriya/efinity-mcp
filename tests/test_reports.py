"""Parsers against the real reports of a compiled Ti375N1156 design (tests/fixtures/led_showcase)."""

from efinity_mcp import project as prj
from efinity_mcp import reports


def _proj(showcase):
    return prj.Project(showcase / "led_showcase.xml")


def test_timing_summary(showcase):
    s = reports.parse_timing_summary(reports.read_text(_proj(showcase).output(".timing.rpt")))
    assert s["timing_met"] is True
    clocks = {c["clock"]: c for c in s["constrained_clocks"]}
    assert clocks["sys_clk"]["frequency_mhz"] == 100.0
    assert clocks["aux_clk"]["period_ns"] == 20.0
    fmax = {c["clock"]: c["fmax_mhz"] for c in s["max_frequency"]}
    assert fmax == {"sys_clk": 237.756, "aux_clk": 412.201}
    assert s["worst_setup_slack_ns"] == 5.794
    assert s["worst_hold_slack_ns"] == 0.095


def test_timing_paths(showcase):
    text = reports.read_text(_proj(showcase).output(".timing.rpt"))
    setup = reports.parse_timing_paths(text, "setup")
    assert setup and setup[0]["slack_ns"] == 5.794
    assert setup[0]["clock_pair"] == "sys_clk vs sys_clk"
    detailed = reports.parse_timing_paths(text, "setup", include_detail=True)
    assert "detail" in detailed[0]
    assert reports.parse_timing_paths(text, "hold")


def test_pinout(showcase):
    pins = {p["Signal Name"]: p for p in reports.parse_pinout(_proj(showcase).output(".pinout.csv"))}
    assert pins["led[0].N"]["Pin Number"] == "N6"
    assert pins["clk_25m"]["Pin Number"] == "M23"
    assert pins["btn_next_n.P"]["Pull Type"] == "weak pullup"


def test_tool_reports(showcase):
    proj = _proj(showcase)
    route = reports.parse_tool_report(proj.output(".route.rpt.xml"))
    assert route["Interface"]["Missing Interface Pins"] == "0"
    assert route["Core Resources"]["Memory Blocks"].startswith("1 /")
    periphery = reports.parse_tool_report(proj.output(".pt.rpt.xml"))["Periphery Resource"]
    assert periphery["PLL"].startswith("1 /")


def test_utilization_reports(showcase):
    proj = _proj(showcase)
    rows = reports.parse_res_csv(proj.output(".res.csv"))
    assert rows[0]["Module"].startswith("led_showcase_top")
    hier = reports.parse_hier_util(proj.output(".hier_util.rpt"), max_depth=1)
    assert hier and all(r["depth"] <= 1 for r in hier)


def test_messages_ignore_interactive_sta_log(showcase):
    proj = _proj(showcase)
    assert not reports.collect_messages(proj, "error")
    warnings = reports.collect_messages(proj, "warning")
    assert any("Synchronizer chain" in m["message"] for m in warnings)
    # An ad-hoc run_sta_tcl session writes <proj>.sta.out; its errors aren't build errors.
    proj.output(".sta.out").write_text("ERROR    : Fail to create the path command from report timing options\n")
    assert not reports.collect_messages(proj, "error")


def test_sections():
    text = "---------- 1. Clock Summary (begin) ----------\nA\n---------- Clock Summary (end) ----------\n"
    assert reports.list_sections(text)
    assert "A" in reports.get_section(text, "Clock Summary")
