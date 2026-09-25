# Tool reference

Generated from the server's tool definitions by `scripts/gen_tools_doc.py`. The descriptions
are exactly what the assistant sees.

## `get_efinity_info`  (read-only)

Show the Efinity install this server uses: path, version, user settings folder, recent projects.

## `list_projects`  (read-only)

List Efinity projects: the GUI's recent projects plus projects found under the default
project folder (or `search_dir`). Shows device and when the bitstream was last built.

| Parameter | Type | Default |
|---|---|---|
| `search_dir` | string | `None` |

## `get_project_info`  (read-only)

Read a project's settings: device, top module, design/constraint/interface files and
synthesis, place-and-route, and bitstream options.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |

## `create_project`

Create a new Efinity project .xml.

family: Trion | Titanium | Topaz. device: e.g. Ti375N1156, Ti60F225, T20F256.
timing_model: speed grade such as C4, C3, I4. design_files and sdc_files may be absolute or
relative to `directory`. verilog_version: verilog_2k | sv_09 | ...; vhdl_version: vhdl_2008 | vhdl_93.
The Interface Designer file (<name>.peri.xml) is created by the first edit_interface call
(create the GPIOs / PLLs the top module's ports need) before a full compile.

| Parameter | Type | Default |
|---|---|---|
| `name` | string | required |
| `directory` | string | required |
| `family` | string | required |
| `device` | string | required |
| `timing_model` | string | required |
| `top_module` | string | required |
| `design_files` | list of string | required |
| `sdc_files` | list of string | `None` |
| `verilog_version` | string | `'verilog_2k'` |
| `vhdl_version` | string | `'vhdl_2008'` |

## `update_project`

Edit a project .xml: add or remove HDL files and SDC files, change the top module or the
device. A .xml.bak backup is written first.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `add_files` | list of string | `None` |
| `remove_files` | list of string | `None` |
| `top_module` | string | `None` |
| `family` | string | `None` |
| `device` | string | `None` |
| `timing_model` | string | `None` |
| `add_sdc` | list of string | `None` |
| `remove_sdc` | list of string | `None` |

## `run_flow`

Run the Efinity flow with efx_run.

compile = full RTL-to-bitstream (synthesis, debug core, interface, place & route, bitstream).
The other flows run one stage and expect the earlier stages to be up to date.
Waits up to `wait_seconds` (0 = return immediately); if the run is not done by then, returns
a job_id to poll with get_job_status. When it finishes, the result includes resource usage,
final timing, and any errors.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `flow` | `compile` \| `synthesis` \| `interface` \| `pnr` \| `bitstream` | `'compile'` |
| `wait_seconds` | integer | `600` |
| `timeout_minutes` | integer | `180` |

## `get_job_status`  (read-only)

Status of a background job (flow run or programming). Optionally wait up to wait_seconds
for it to finish. Shows stage results and the last console lines.

| Parameter | Type | Default |
|---|---|---|
| `job_id` | string | required |
| `wait_seconds` | integer | `0` |
| `tail_lines` | integer | `40` |

## `list_jobs`  (read-only)

List the jobs started in this server session, newest first.

## `cancel_job`  (changes files or hardware)

Stop a running job and its child processes.

| Parameter | Type | Default |
|---|---|---|
| `job_id` | string | required |

## `get_build_status`  (read-only)

Summarise the latest build: which outputs exist and when they were made, whether sources
changed since the bitstream was built, resource usage, final timing, and error/warning counts.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |

## `get_timing_summary`  (read-only)

Post-route timing: constrained vs achievable clock frequencies, setup/hold slack per
clock pair, worst slack, and whether timing is met (from <project>.timing.rpt).

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |

## `get_timing_paths`  (read-only)

Worst critical paths from the timing report, sorted by slack. clock_filter keeps paths
whose 'launch vs capture' clock pair contains the text. include_detail adds the full
cell/net breakdown of each path. For custom queries (-from/-to/-through), use run_sta_tcl.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `analysis` | `setup` \| `hold` | `'setup'` |
| `max_paths` | integer | `10` |
| `clock_filter` | string | `None` |
| `include_detail` | boolean | `False` |

## `get_utilization`  (read-only)

Resource usage: core (XLRs, memory, DSP, I/O, clocks) and periphery after place & route,
plus synthesis estimates. hierarchy=True adds a per-module breakdown (values are total(self)).

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `hierarchy` | boolean | `False` |
| `max_depth` | integer | `2` |

## `get_messages`  (read-only)

Errors and warnings from the latest run of each stage (synthesis, debug core, place,
route, bitstream), most severe first. `severity` is the minimum level; `pattern` is a
case-insensitive regex filter.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `severity` | `error` \| `critical` \| `warning` \| `info` | `'warning'` |
| `pattern` | string | `None` |
| `limit` | integer | `100` |

## `get_io_assignments`  (read-only)

Package pin assignments of user signals (pin, bank, voltage, I/O standard, pull) from the
Interface Designer pinout. include_core_interface adds the core<->periphery signal list.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `include_core_interface` | boolean | `False` |

## `list_reports`  (read-only)

List the report and log files in the project's outflow folder.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |

## `read_report`  (read-only)

Read any report or log, e.g. 'timing.rpt', 'pt.rpt', 'map.rpt', 'route.out', 'EFX.warn.log'
(the project-name prefix is optional).

section: title or number of a report section (see the 'sections' list returned when omitted).
pattern: regex; returns only matching lines with context_lines around them.
offset/max_lines: page through long files.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `report` | string | required |
| `section` | string | `None` |
| `pattern` | string | `None` |
| `context_lines` | integer | `2` |
| `offset` | integer | `0` |
| `max_lines` | integer | `200` |

## `run_sta_tcl`  (read-only)

Run Tcl in Efinity's static timing analyzer on the routed design (efx_run sta_tclsh).

Supports SDC-style commands: report_timing (-from/-to/-through/-from_clock/-to_clock/-npaths/
-setup/-hold/-detail), report_timing_summary, report_clocks, report_cdc, check_timing,
get_cells/get_nets/get_pins/get_ports/get_clocks, all_registers, create_clock,
set_false_path, and more. Run 'help' or '<cmd> -help' for usage. Constraints set here only
apply to this session; they are not saved to the project.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `script` | string | required |
| `timeout_seconds` | integer | `900` |

## `get_interface_design`  (read-only)

Overview of the project's Interface Designer (.peri.xml) design: every GPIO (mode, I/O
standard, resource, package pin, bank), every other block (PLL, OSC, LVDS, MIPI, DDR, JTAG,
PMA_DIRECT, ...) with its resource and key settings, and the I/O bank voltages.

block_types limits the listing (e.g. ["GPIO"] or ["PLL"]). include_check also runs the
Interface Designer design check. Reads the saved file, so it reflects the GUI only after the
GUI saves.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `block_types` | list of string | `None` |
| `include_check` | boolean | `False` |

## `get_interface_block`  (read-only)

All properties of one Interface Designer instance (GPIO, GPIO bus, PLL, LVDS_TX, ...) with the
allowed values for each: choice lists, 'min:max' ranges, or free-form. Use it before
edit_interface to get exact property names. For PLLs it adds the calculated VCO/output
frequencies and the reference clock source.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `name` | string | required |
| `block_type` | string | `None` |

## `list_device_resources`  (read-only)

Physical resources on the project's device that Interface Designer blocks can be placed on.

GPIO: each resource with its package pin, I/O bank, features (e.g. HSIO, DDIO), alternate
function (e.g. GCLK, PLL_CLKIN) and current user. Filter by bank ('4B'), or by feature or
alternate function text ('PLL_CLKIN', 'HSIO'). Other block types (PLL, LVDS_TX, OSC, ...):
the resource names and whether each is used. free_only=False includes used resources.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `block_type` | string | `'GPIO'` |
| `free_only` | boolean | `True` |
| `bank` | string | `None` |
| `feature` | string | `None` |
| `limit` | integer | `200` |

## `check_interface`  (read-only)

Run the Interface Designer design check (DRC) on the saved design: unplaced pins, I/O
standard vs bank voltage mismatches, PLL frequency ranges, clock routing rules, and so on.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |

## `calc_pll`  (read-only)

PLL calculator. With no targets, reports the PLL's current VCO/PLL/output frequencies.
With targets such as {"CLKOUT0_FREQ": 200, "CLKOUT1_FREQ": 50, "CLKOUT1_PHASE": 90}, returns
counter/divider solutions (M, N, O, per-output dividers) without changing anything. Apply one
with edit_interface op auto_calc_pll. Only enabled outputs (CLKOUTn_EN=1) can be solved.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `name` | string | required |
| `targets` | object | `None` |
| `max_results` | integer | `5` |

## `edit_interface`

Change the Interface Designer design (.peri.xml). Operations run in order; if any fails,
nothing is saved. After the operations the design check runs, and the file is saved only if
no new design-check errors appeared (allow_new_errors=True saves anyway). dry_run=True applies
and checks without saving. A .peri.xml.bak backup is written before saving. If the project has
no .peri.xml yet, one is created for the project's device.

Values are validated against the allowed values (see get_interface_block); I/O standards may
be written as '3.3 V LVCMOS' or '3.3_V_LVCMOS'.

Operations (each is an object with "op"):
  {"op": "create_gpio", "name": "led", "mode": "output", "pin": "U4", "properties": {"IO_STANDARD": "1.8 V LVCMOS"}}
      mode: input | output | inout | open_drain_output | clock_input | regional_clock_input |
      pll_clock_input | pll_ext_feedback | mipi_clock_input | pcie_perstn | clockout |
      global_control | vref | unused. A bus: add "msb": 7, "lsb": 0 (input/output/inout);
      buses can't take "pin", so place members with assign_pin on "led[0]" etc.
      "resource" (e.g. "GPIOB_P_31") can be given instead of "pin".
  {"op": "create_block", "name": "pll0", "type": "PLL", "resource": "PLL_TL0", "properties": {...}}
      type: any of block_types_supported from get_interface_design (PLL, OSC, LVDS_TX, LVDS_RX,
      MIPI_DPHY_RX, DDR, JTAG, PMA_DIRECT, ...). Optional "params" are passed to the API's
      create_block (tx_mode / rx_conn_type for LVDS, mode / conn_type for MIPI lanes).
  {"op": "set_properties", "name": "led", "properties": {"PULL_OPTION": "WEAK_PULLUP", "DRIVE_STRENGTH": "8"}}
      On a bus name, sets every member. Add "type" if a name is ambiguous.
  {"op": "assign_pin", "name": "led", "pin": "U4"}               (GPIO package ball)
  {"op": "assign_resource", "name": "pll0", "resource": "PLL_TR1"}
      Both refuse a resource already used by another instance unless "override": true.
  {"op": "delete", "name": "old_sig"}
  {"op": "set_bank_voltage", "bank": "4B", "voltage": "1.8"}
  {"op": "auto_calc_pll", "name": "pll0", "targets": {"CLKOUT0_FREQ": 200, "CLKOUT1_FREQ": 100}}
      Solves and applies M/N/O and dividers. Enable the outputs first (CLKOUTn_EN=1, CLKOUTn_PIN).
      PLL output frequencies are read-only properties; this is how they are set.
  {"op": "gen_pll_ref_clock", "name": "pll0", "refclk_name": "pll_refclk", "pll_res": "PLL_TL0"}
      Creates the reference-clock GPIO on the pin that feeds that PLL.
  {"op": "set_unused_gpio_state", "state": "INPUT_WITH_WEAK_PULLUP"}
  {"op": "import_isf", "file": "C:/path/settings.isf"}      (Interface Scripting File)

After saving, run_flow(compile) rebuilds with the new interface. If the project is open in the
Efinity GUI, close it or reload it there, or the GUI may save its old copy over these changes.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `operations` | list of object | required |
| `dry_run` | boolean | `False` |
| `allow_new_errors` | boolean | `False` |

## `export_interface_isf`

Export Interface Designer settings to an Interface Scripting File (.isf, Python commands
that recreate the blocks). Useful as a readable backup, for diffs, or to copy settings to
another project (import with edit_interface op import_isf). Defaults to <project>_export.isf
in the project folder; block_types / instances limit what is exported.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | required |
| `isf_file` | string | `None` |
| `block_types` | list of string | `None` |
| `instances` | list of string | `None` |
| `export_all_pins` | boolean | `False` |

## `list_programmer_cables`  (read-only)

Detect connected Efinix programming cables / dev boards (FTDI) and the FPGAs on their JTAG chain.

| Parameter | Type | Default |
|---|---|---|
| `timeout_seconds` | integer | `60` |

## `program_device`  (changes files or hardware)

Program an Efinix FPGA through the Efinity command-line programmer.

mode:
  jtag / jtag_chain  - load a .bit into the FPGA's SRAM over JTAG (lost at power-off)
  active / passive   - write a .hex to SPI configuration flash (or SPI passive load)
  jtag_bridge(_x8)   - write a .hex to configuration flash through the FPGA's JTAG bridge
Flash modes overwrite what the board boots from, so confirm with the user first.
Give either `project` (uses its outflow .bit/.hex) or an explicit `file`.
url / board_profile select a cable when several are attached (see list_programmer_cables).
chain_device_number picks the device for jtag_chain.

| Parameter | Type | Default |
|---|---|---|
| `project` | string | `None` |
| `file` | string | `None` |
| `mode` | `jtag` \| `jtag_chain` \| `active` \| `passive` \| `jtag_bridge` \| `jtag_bridge_x8` | `'jtag'` |
| `board_profile` | string | `None` |
| `url` | string | `None` |
| `chain_device_number` | integer | `None` |
| `jtag_clock_hz` | integer | `None` |
| `verify_method` | `none` \| `onchipx1` \| `onchipx2` \| `onchipx4` \| `hostx1` | `None` |
| `wait_seconds` | integer | `600` |
