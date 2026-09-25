# PLL outputs (core clock pins of pll0)
create_clock -period 10.000 -name sys_clk [get_ports {sys_clk}]
create_clock -period 20.000 -name aux_clk [get_ports {aux_clk}]

# The only aux_clk -> sys_clk path is the Gray-coded mode bus through gray_sync's 2-FF synchronizer.
set_clock_groups -asynchronous -group {sys_clk} -group {aux_clk}
