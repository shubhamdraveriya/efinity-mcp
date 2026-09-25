# led_showcase: 8-effect LED demo for the Ti375 N1156 Development Kit

A design built end to end by an AI assistant through efinity-mcp, then run on the board.

- **Clocks:** the 25 MHz oscillator `CLK_25M_2` (GPIOR_88, ball M23) drives `PLL_TR1`, which
  makes `sys_clk` (100 MHz) and `aux_clk` (50 MHz).
- **Effects:** scanner, rolling wave, sparkle, binary counter, fill/drain, heartbeat, dual
  comets and theatre chase. They advance every 6 s.
- **Buttons:** SW3 jumps to the next effect; SW4 pauses or resumes auto-advance. Both are
  debounced in the 50 MHz domain.
- **Clock crossing:** the effect number crosses to the 100 MHz domain as Gray code through a
  2-FF synchronizer.
- **Brightness:** each 5 ms frame, one shared block-RAM gamma table (`gamma.hex`, gamma 2.2)
  converts the 6 brightness values, and a 6-channel 8-bit PWM drives LED1..LED6.

Result on Titanium Ti375N1156 C4 with Efinity 2026.1:

| | |
|---|---|
| Logic | 1,851 XLRs, 1 block RAM, 1 PLL |
| sys_clk | 100 MHz required, 237.8 MHz achievable |
| aux_clk | 50 MHz required, 412.2 MHz achievable |
| Clock crossings | 3 endpoints (the Gray-coded mode bus), aux_clk to sys_clk |

## Pins (from the development kit user guide)

| Signal | Board | Resource | Ball | Notes |
|---|---|---|---|---|
| `clk_25m` | CLK_25M_2 | GPIOR_88 | M23 | 3.3 V bank; PLL_TR1 `EXT_CLK1` |
| `led[0..5]` | LED1..LED6 | GPIOB_N_41, P_42, N_42, P_33, P_34, P_35 | N6, M4, M3, T4, V2, U2 | active high, 1.8 V |
| `btn_next_n` | SW3 | GPIOB_P_31 | U4 | active low, weak pull-up |
| `btn_pause_n` | SW4 | GPIOB_P_32 | R5 | active low, weak pull-up |

## Rebuild it

Copy this folder somewhere writable, then ask your assistant:

> Create an Efinity project `led_showcase` in `<this folder>` for Titanium Ti375N1156 C4, top module
> `led_showcase_top`, with the files in `rtl/` and `led_showcase.sdc`. Apply the operations in
> `interface.json` with `edit_interface` (dry run first), then compile and show me the timing summary
> and pinout.

Or run the same steps with the tools directly:

1. `create_project` with `design_files: ["rtl/led_showcase_top.v", "rtl/led_showcase_lib.v", "rtl/effects.v"]`
   and `sdc_files: ["led_showcase.sdc"]`
2. `edit_interface` with the `operations` list from `interface.json`
3. `run_flow` with `flow: "compile"`
4. `get_timing_summary`, `get_io_assignments`, `run_sta_tcl` with `report_cdc`
5. `program_device` (JTAG, volatile) with the board connected

For another board, change the pins in `interface.json` and, if the LEDs are active low, set
`LED_ACTIVE_LOW = 1` in `rtl/led_showcase_top.v`.
