# efinity-mcp

An [MCP](https://modelcontextprotocol.io) server for the **Efinix Efinity** FPGA toolchain
(Trion, Titanium, Topaz). It lets an AI assistant such as Claude create projects, set up the
Interface Designer (I/O, PLLs, LVDS, MIPI, DDR, SerDes), compile, read timing and utilization,
run static-timing Tcl queries, and program boards, all through your local Efinity install.

> **Unofficial.** This is a community project, not made or endorsed by Efinix, Inc.
> It contains no Efinix software; it drives the Efinity installation you already have.

## What it looks like

Asked for *"a multi-effect LED demo for my Ti375 N1156 dev kit"*, an assistant using this
server did the whole job:

1. `create_project`, then `run_flow(synthesis)`. The first run failed on a reserved word in the
   RTL; `get_messages` returned the file and line, the RTL was fixed, and synthesis passed.
2. `list_device_resources` located the board's 25 MHz oscillator pin (`PLL_CLKIN`, ball M23).
3. `edit_interface` created the clock input, a PLL with two outputs and a lock signal
   (`auto_calc_pll` solved M/N/O for exactly 100 and 50 MHz), a 6-LED bus and 2 pushbuttons
   with pull-ups. It ran as a dry run first, then passed the design check and was saved.
4. `run_flow(compile)` finished in 47 s. `get_timing_summary` showed timing met, and
   `run_sta_tcl` with `report_cdc` confirmed the only clock crossing was the Gray-coded bus.
5. `get_io_assignments` confirmed every pin against the board's user guide. On the board,
   the design ran as intended.

That design is in [`examples/led_showcase`](examples/led_showcase).

## Tools

| Area | Tool | What it does |
|---|---|---|
| Setup | `get_efinity_info` | Install path, version, user folder, recent projects |
| Projects | `list_projects` | GUI recent projects + projects under `~/.efinity/project` |
| | `get_project_info` | Device, top module, HDL/SDC/ISF/interface files, tool options |
| | `create_project` | New project `.xml` (device, top module, HDL and SDC files) |
| | `update_project` | Add/remove HDL or SDC files, change the top module or device (writes a `.bak` first) |
| Build | `run_flow` | `compile` (RTL to bitstream), `synthesis`, `interface`, `pnr`, `bitstream` |
| | `get_job_status` / `list_jobs` / `cancel_job` | Long runs continue in the background; poll or stop them |
| Results | `get_build_status` | Output timestamps, stale-bitstream check, resources, final timing, message counts |
| | `get_timing_summary` | Fmax per clock, setup/hold slack per clock pair, timing met? |
| | `get_timing_paths` | Worst setup/hold paths (start/end points, slack, logic levels, optional full detail) |
| | `get_utilization` | Core + periphery usage, synthesis estimates, per-module hierarchy |
| | `get_messages` | Errors and warnings from the latest run of each stage, filterable |
| | `get_io_assignments` | Pin, bank, voltage, I/O standard and pull of each user signal |
| | `list_reports` / `read_report` | Any report or log, by section, regex, or page |
| Timing | `run_sta_tcl` | Tcl in Efinity's STA: `report_timing -from/-to/-through`, `report_cdc`, `check_timing`, ... |
| Interface Designer | `get_interface_design` | Every GPIO and block (PLL, OSC, LVDS, MIPI, DDR, SerDes...) with key settings, bank voltages |
| | `get_interface_block` | All properties of one instance, with the allowed values for each |
| | `list_device_resources` | Free (or all) pins by bank, feature or alternate function; PLL/LVDS/... resources |
| | `edit_interface` | Create/delete GPIOs, buses and blocks, set properties, assign pins, bank voltages, PLL auto-calc, ISF import |
| | `calc_pll` | PLL calculator: current frequencies, or divider solutions for target frequencies |
| | `check_interface` | Interface Designer design check (DRC) |
| | `export_interface_isf` | Export settings as an `.isf` script (backup, diff, copy to another project) |
| Hardware | `list_programmer_cables` | Detect FTDI cables / dev boards and their JTAG chain |
| | `program_device` | JTAG (SRAM, `.bit`) or SPI flash (`active`, `passive`, `jtag_bridge`, `.hex`) |

Every `project` argument accepts a project `.xml` path, a project folder, or just a project name
known to the Efinity GUI. See [docs/TOOLS.md](docs/TOOLS.md) for details, including every
`edit_interface` operation.

## Requirements

- **Efinity** installed (tested with 2026.1.132). The free Efinity license is enough.
- **Python 3.10+** for the server. It runs separately from Efinity's bundled Python and starts
  that as a subprocess.
- **Windows 10/11** (tested). **Linux**: the environment setup follows Efinity's `setup.sh`,
  but Linux has not been tested yet; reports are welcome.

## Install

```bash
pip install git+https://github.com/shubhamdraveriya/efinity-mcp
efinity-mcp --check        # prints the Efinity install it found and tests its Python
```

Or download the `.whl` from the [Releases](https://github.com/shubhamdraveriya/efinity-mcp/releases)
page and run `pip install efinity_mcp-0.1.0-py3-none-any.whl`.

The server finds Efinity through the `EFINITY_HOME` environment variable, or else the newest
version under `C:\Efinity\` (Windows), `~/efinity/` or `/opt/efinity/` (Linux).

From source:

```bash
git clone https://github.com/shubhamdraveriya/efinity-mcp
cd efinity-mcp
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip install -e ".[dev]"
```

## Connect it to an assistant

**Claude Code** (all projects):

```bash
claude mcp add efinity --scope user -- efinity-mcp
```

**Claude Desktop** and other clients that use an `mcpServers` JSON file (for Claude Desktop:
`%APPDATA%\Claude\claude_desktop_config.json` or `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "efinity": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["-m", "efinity_mcp"],
      "env": { "EFINITY_HOME": "C:\\Efinity\\2026.1" }
    }
  }
}
```

Use the Python that has `efinity-mcp` installed. `env` is optional; without it the newest
Efinity install is used. Restart the client after editing its config.

### Things to ask

- *"List my Efinity projects and show the timing summary of blinky."*
- *"Compile my_design. If timing fails, show the worst 5 setup paths and suggest fixes."*
- *"Add a 1.8 V output `status_led` on a free pin in bank 4B and rebuild."*
- *"Set the PLL's second output to 156.25 MHz and check the design."*
- *"Run report_cdc on the routed design and explain every crossing."*
- *"Program the board over JTAG."*

## How it works

- **Builds** call `scripts/efx_run.py <project>.xml --flow <flow>` with Efinity's bundled Python
  and the same environment `bin/setup.bat` / `setup.sh` create. Outputs go to the project's
  normal `outflow/` folder, so the Efinity GUI sees the same results.
- **Background jobs:** each run is a job with its console log in `%LOCALAPPDATA%\efinity_mcp\logs`
  (Linux: `~/.local/state/efinity_mcp/logs`). Progress comes from efx_run's per-stage
  `PASS`/`FAIL` lines. Only one job per project runs at a time.
- **Results** come from `<project>.route.rpt.xml`, `.pt.rpt.xml`, `.timing.rpt`, `.res.csv`,
  `.hier_util.rpt`, `.pinout.csv` and the per-stage `*.out` logs.
- **`run_sta_tcl`** uses the `sta_tclsh` flow. It reloads the routed design (about 6 s) and
  doesn't change the build.
- **Interface Designer tools** run a small worker (`pt_helper.py`) under Efinity's Python, using
  Efinity's Interface Designer Python API (`pt/bin/api_service`). That API accepts unknown
  properties, invalid values and nonexistent pins without complaint, so every edit is checked
  against the API's own option lists and read back afterwards.
- **Programming** uses the command-line programmer (`pgm/bin/efx_pgm/ftdi_program.py`).

## Safety

- **Edits are all-or-nothing.** If any operation in an `edit_interface` batch fails, nothing is
  saved. After the batch, the Interface Designer design check runs, and the file is saved only
  if the batch added no new errors. `dry_run=true` previews a change, and a `.peri.xml.bak`
  backup is written before every save. `update_project` also writes a `.xml.bak` first.
- **Hardware:** JTAG programming loads volatile SRAM. The flash modes (`active`, `passive`,
  `jtag_bridge`) overwrite what the board boots from; the server's instructions tell the
  assistant to confirm with you first.
- **GUI open at the same time:** builds are fine. If you edit a project's `.xml` or `.peri.xml`
  while it is open in the Efinity GUI, reload it there, or the GUI may save its old copy over
  the change. `edit_interface` warns when the GUI is running.

## Things to know

- **No SDC means a 1 ns default.** Without an SDC, Efinity constrains every clock to 1000 MHz, so
  large negative slack on an unconstrained design is expected, not a real failure.
  `get_timing_summary` points this out.
- **PLL output frequencies are read-only.** In the API they follow from M/N/O and the dividers.
  Use `calc_pll` or the `auto_calc_pll` operation.
- **Debugger auto-instantiation:** if a Debug Wizard profile is enabled, the command-line
  compile needs the generated core `work_dbg/debug_top.v`, which the GUI creates the first time
  you use the Debug Wizard. The server checks for it and tells you when it's missing.
- **Efinity versions:** the Interface Designer tools use Efinity's Python API, which can change
  between releases. Tested with 2026.1.

## Not covered yet

- Simulation flows (`rtlsim`, `mapsim`, `pnrsim`)
- Debugger (logic analyzer / virtual I/O) capture
- IP Manager (generating and configuring IP cores)
- Interface Designer global clock-mux / regional-buffer setup, PCIe root-port outbound tables,
  partial designs. These can still be done by importing an `.isf` script.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest                      # unit tests; no Efinity needed
pytest -m efinity -v        # end-to-end tests against your Efinity install (creates and compiles a scratch project)
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). "Efinix", "Efinity", "Titanium", "Trion"
and "Topaz" are trademarks of Efinix, Inc.
