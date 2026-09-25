# Changelog

## 0.1.0 - first public release

- 27 tools covering projects, builds (background jobs with per-stage progress), timing,
  utilization, messages, reports, STA Tcl, Interface Designer editing, and board programming.
- Interface Designer editing through Efinity's Python API. Every value is checked against
  the API's allowed values, batches are all-or-nothing and design-checked before saving, and
  a `.peri.xml.bak` backup is written first.
- PLL auto-calculation (`calc_pll`, `auto_calc_pll`) for target output frequencies.
- `efinity-mcp --check` to verify the Efinity installation.
- Tested on Windows 11 with Efinity 2026.1.132 (Titanium Ti375N1156). Linux support follows
  Efinity's `setup.sh` but is untested.
