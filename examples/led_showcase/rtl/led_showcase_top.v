// led_showcase_top - 8-mode LED light show for 6 LEDs.
//
// Target: Titanium Ti375 N1156 Development Kit.
//   25 MHz oscillator CLK_25M_2 (GPIOR_88) -> PLL_TR1 -> sys_clk 100 MHz (effects, gamma, PWM)
//                                                    -> aux_clk  50 MHz (buttons, sequencer)
//   LED1..LED6 = led[0..5] (active high). SW3 = next effect, SW4 = pause/resume auto-cycling.
//
//   The mode number crosses from aux_clk to sys_clk as Gray code through a 2-FF synchronizer.
//   Each 5 ms frame the effect engine computes 6 brightness values, a shared gamma ROM
//   (block RAM) converts them one channel at a time, and a 6-channel PWM drives the LEDs.
module led_showcase_top #(
    parameter LED_ACTIVE_LOW = 0,
    parameter MODE_HOLD_CYC  = 300_000_000,  // aux_clk cycles per mode (6 s at 50 MHz)
    parameter DEBOUNCE_CYC   = 500_000,      // aux_clk cycles a button must be stable (10 ms)
    parameter FRAME_DIV      = 500_000       // sys_clk cycles per frame (200 Hz at 100 MHz)
) (
    input  wire       sys_clk,
    input  wire       aux_clk,
    input  wire       pll_locked,
    input  wire       btn_next_n,    // SW3, low when pressed
    input  wire       btn_pause_n,   // SW4, low when pressed
    output wire [5:0] led
);
    wire sys_rst, aux_rst;
    reset_sync u_rst_sys (.clk(sys_clk), .arst_n(pll_locked), .rst(sys_rst));
    reset_sync u_rst_aux (.clk(aux_clk), .arst_n(pll_locked), .rst(aux_rst));

    // ---- aux_clk domain: buttons pick the effect
    wire next_press, pause_press;
    button_debounce #(.STABLE_CYCLES(DEBOUNCE_CYC)) u_btn_next (
        .clk(aux_clk), .rst(aux_rst), .btn_n(btn_next_n), .pressed(next_press)
    );
    button_debounce #(.STABLE_CYCLES(DEBOUNCE_CYC)) u_btn_pause (
        .clk(aux_clk), .rst(aux_rst), .btn_n(btn_pause_n), .pressed(pause_press)
    );

    wire [2:0] mode_gray;
    wire       paused;
    mode_sequencer #(.HOLD_CYCLES(MODE_HOLD_CYC)) u_seq (
        .clk(aux_clk), .rst(aux_rst), .next(next_press), .pause(pause_press),
        .mode_gray(mode_gray), .paused(paused)
    );

    // ---- clock-domain crossing
    wire [2:0] mode;
    gray_sync #(.W(3)) u_mode_cdc (
        .clk(sys_clk), .rst(sys_rst), .gray_in(mode_gray), .bin_out(mode)
    );

    // ---- sys_clk domain
    wire frame_tick;
    tick_gen #(.DIV(FRAME_DIV)) u_frame (.clk(sys_clk), .rst(sys_rst), .tick(frame_tick));

    wire [15:0] rnd;
    lfsr16 u_lfsr (.clk(sys_clk), .rst(sys_rst), .q(rnd));

    wire [47:0] bright;
    effects u_fx (
        .clk(sys_clk), .rst(sys_rst), .frame_tick(frame_tick), .mode(mode), .rnd(rnd), .bright(bright)
    );

    // effects registers its outputs on frame_tick, so start the gamma pass one cycle later
    reg gamma_start;
    always @(posedge sys_clk) gamma_start <= frame_tick;

    wire [47:0] duty;
    gamma_stage u_gamma (
        .clk(sys_clk), .rst(sys_rst), .start(gamma_start), .bright_in(bright), .duty_out(duty)
    );

    wire [5:0] pwm;
    pwm_bank #(.N(6)) u_pwm (.clk(sys_clk), .rst(sys_rst), .duty(duty), .pwm(pwm));

    assign led = LED_ACTIVE_LOW ? ~pwm : pwm;
endmodule
