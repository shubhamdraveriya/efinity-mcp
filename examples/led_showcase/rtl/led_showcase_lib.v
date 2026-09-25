// Small building blocks for led_showcase_top.

// Active-high reset, asserted asynchronously, released synchronously to clk.
module reset_sync (
    input  wire clk,
    input  wire arst_n,
    output wire rst
);
    (* async_reg = "true" *) reg [2:0] sync;
    always @(posedge clk or negedge arst_n)
        if (!arst_n) sync <= 3'b111;
        else         sync <= {sync[1:0], 1'b0};
    assign rst = sync[2];
endmodule


// One-cycle strobe every DIV cycles.
module tick_gen #(
    parameter DIV = 1000
) (
    input  wire clk,
    input  wire rst,
    output reg  tick
);
    localparam W = $clog2(DIV);
    reg [W-1:0] cnt;
    always @(posedge clk)
        if (rst) begin
            cnt  <= 0;
            tick <= 1'b0;
        end else if (cnt == DIV - 1) begin
            cnt  <= 0;
            tick <= 1'b1;
        end else begin
            cnt  <= cnt + 1'b1;
            tick <= 1'b0;
        end
endmodule


// 16-bit maximal-length Fibonacci LFSR (x^16 + x^14 + x^13 + x^11 + 1).
module lfsr16 (
    input  wire        clk,
    input  wire        rst,
    output reg  [15:0] q
);
    always @(posedge clk)
        if (rst) q <= 16'hACE1;
        else     q <= {q[14:0], q[15] ^ q[13] ^ q[12] ^ q[10]};
endmodule


// Active-low pushbutton: 2-FF synchronizer, then the level must hold for STABLE_CYCLES before
// it is accepted. `pressed` pulses for one cycle on each accepted press.
module button_debounce #(
    parameter STABLE_CYCLES = 500_000
) (
    input  wire clk,
    input  wire rst,
    input  wire btn_n,
    output reg  pressed
);
    localparam W = $clog2(STABLE_CYCLES);
    (* async_reg = "true" *) reg [1:0] sync;
    reg         state;     // debounced level, 1 = pressed
    reg [W-1:0] cnt;
    always @(posedge clk)
        if (rst) begin
            sync    <= 2'b11;
            state   <= 1'b0;
            cnt     <= 0;
            pressed <= 1'b0;
        end else begin
            sync    <= {sync[0], btn_n};
            pressed <= 1'b0;
            if (~sync[1] == state) begin
                cnt <= 0;
            end else if (cnt == STABLE_CYCLES - 1) begin
                cnt     <= 0;
                state   <= ~sync[1];
                pressed <= ~sync[1];
            end else begin
                cnt <= cnt + 1'b1;
            end
        end
endmodule


// Counts through 8 modes, holding each for HOLD_CYCLES. `next` jumps to the next mode at
// once; `pause` toggles automatic advancing. The output is registered Gray code, so exactly
// one bit changes per step and it can cross clock domains safely.
module mode_sequencer #(
    parameter HOLD_CYCLES = 300_000_000
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       next,
    input  wire       pause,
    output reg  [2:0] mode_gray,
    output reg        paused
);
    localparam W = $clog2(HOLD_CYCLES);
    reg [W-1:0] hold;
    reg [2:0]   mode_bin;
    always @(posedge clk)
        if (rst) begin
            hold      <= 0;
            mode_bin  <= 3'd0;
            mode_gray <= 3'd0;
            paused    <= 1'b0;
        end else begin
            if (pause)
                paused <= ~paused;
            if (next || (!paused && hold == HOLD_CYCLES - 1)) begin
                hold     <= 0;
                mode_bin <= mode_bin + 1'b1;
            end else if (!paused) begin
                hold <= hold + 1'b1;
            end
            mode_gray <= mode_bin ^ (mode_bin >> 1);
        end
endmodule


// 2-FF synchronizer for a Gray-coded bus, converted back to binary in the destination domain.
module gray_sync #(
    parameter W = 3
) (
    input  wire         clk,
    input  wire         rst,
    input  wire [W-1:0] gray_in,
    output reg  [W-1:0] bin_out
);
    (* async_reg = "true" *) reg [W-1:0] s1;
    (* async_reg = "true" *) reg [W-1:0] s2;
    reg [W-1:0] bin;
    integer i;
    always @* begin
        bin[W-1] = s2[W-1];
        for (i = W - 2; i >= 0; i = i - 1)
            bin[i] = bin[i + 1] ^ s2[i];
    end
    always @(posedge clk)
        if (rst) begin
            s1 <= 0;
            s2 <= 0;
            bin_out <= 0;
        end else begin
            s1 <= gray_in;
            s2 <= s1;
            bin_out <= bin;
        end
endmodule


// 256 x 8 gamma-2.2 table in block RAM, one-cycle read latency.
module gamma_rom (
    input  wire       clk,
    input  wire [7:0] addr,
    output reg  [7:0] data
);
    reg [7:0] mem [0:255];
    initial $readmemh("gamma.hex", mem);
    always @(posedge clk) data <= mem[addr];
endmodule


// On `start`, runs the 6 brightness values through one shared gamma ROM (one per cycle,
// pipelined) and updates duty_out. The whole pass takes 8 cycles.
module gamma_stage (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire [47:0] bright_in,
    output reg  [47:0] duty_out
);
    reg  [2:0] rd_idx;     // channel being addressed
    reg        rd_busy;
    reg  [2:0] wr_idx;     // channel whose ROM data arrives this cycle
    reg        wr_valid;
    wire [7:0] rom_q;

    gamma_rom u_rom (.clk(clk), .addr(bright_in[rd_idx*8 +: 8]), .data(rom_q));

    always @(posedge clk)
        if (rst) begin
            rd_idx   <= 3'd0;
            rd_busy  <= 1'b0;
            wr_idx   <= 3'd0;
            wr_valid <= 1'b0;
            duty_out <= 48'd0;
        end else begin
            wr_valid <= rd_busy;
            wr_idx   <= rd_idx;
            if (start) begin
                rd_idx  <= 3'd0;
                rd_busy <= 1'b1;
            end else if (rd_busy) begin
                if (rd_idx == 3'd5) rd_busy <= 1'b0;
                else                rd_idx  <= rd_idx + 1'b1;
            end
            if (wr_valid)
                duty_out[wr_idx*8 +: 8] <= rom_q;
        end
endmodule


// N-channel 8-bit PWM (sys_clk / 256 = 390 kHz). New duty values are taken at the start of
// each PWM period so a channel never glitches mid-period.
module pwm_bank #(
    parameter N = 6
) (
    input  wire           clk,
    input  wire           rst,
    input  wire [8*N-1:0] duty,
    output reg  [N-1:0]   pwm
);
    reg [7:0]     cnt;
    reg [8*N-1:0] duty_q;
    integer i;
    always @(posedge clk)
        if (rst) begin
            cnt    <= 8'd0;
            duty_q <= 0;
            pwm    <= 0;
        end else begin
            cnt <= cnt + 1'b1;
            if (cnt == 8'hFF)
                duty_q <= duty;
            for (i = 0; i < N; i = i + 1)
                pwm[i] <= (duty_q[8*i +: 8] > cnt);
        end
endmodule
