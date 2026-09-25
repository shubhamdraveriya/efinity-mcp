// Effect engine: once per frame_tick, computes a new 8-bit brightness for each of the 6 LEDs.
// LEDs keep their brightness across mode changes, so a new effect fades in from the old one.
//
//   0 SCANNER    Knight Rider bounce with a fading tail
//   1 WAVE       phase-shifted triangle breathing, a wave rolling along the row
//   2 SPARKLE    random LEDs flash and decay (LFSR)
//   3 BINARY     6-bit binary counter with soft transitions
//   4 FILL       thermometer bar filling and draining
//   5 HEARTBEAT  lub-dub pulse, brightest in the middle
//   6 COMETS     two comets bouncing in opposite directions
//   7 CHASE      theatre chase, every third LED
module effects (
    input  wire        clk,
    input  wire        rst,
    input  wire        frame_tick,
    input  wire [2:0]  mode,
    input  wire [15:0] rnd,
    output wire [47:0] bright
);
    localparam SCANNER = 3'd0, WAVE = 3'd1, SPARKLE = 3'd2, BINARY = 3'd3,
               FILL = 3'd4, HEARTBEAT = 3'd5, COMETS = 3'd6, CHASE = 3'd7;

    reg [7:0] b [0:5];
    reg [2:0] mode_q;
    reg [7:0] fcnt;     // frames since the last step of the current effect
    reg [2:0] pos;      // scanner / comet head
    reg       dir;
    reg [7:0] phase;    // wave phase
    reg [5:0] bincnt;
    reg [2:0] level;    // fill level 0..6
    reg       lvl_up;
    reg [1:0] chase;    // 0..2
    reg [7:0] beat;     // heartbeat frame counter

    // v - v/2^sh - 1, floored at 0
    function [7:0] decay(input [7:0] v, input [2:0] sh);
        decay = (v == 8'd0) ? 8'd0 : v - (v >> sh) - 8'd1;
    endfunction

    // step v toward target by at most step
    function [7:0] approach(input [7:0] v, input [7:0] target, input [7:0] step);
        if (v < target)      approach = (target - v > step) ? v + step : target;
        else if (v > target) approach = (v - target > step) ? v - step : target;
        else                 approach = v;
    endfunction

    // 0..255 triangle: up over x = 0..127, down over 128..255
    function [7:0] triangle(input [7:0] x);
        triangle = x[7] ? ~{x[6:0], 1'b0} : {x[6:0], 1'b0};
    endfunction

    // bounce a head position between 0 and 5
    task step_bounce;
        begin
            if (!dir) begin
                if (pos == 3'd5) begin dir <= 1'b1; pos <= 3'd4; end
                else pos <= pos + 1'b1;
            end else begin
                if (pos == 3'd0) begin dir <= 1'b0; pos <= 3'd1; end
                else pos <= pos - 1'b1;
            end
        end
    endtask

    integer i;
    always @(posedge clk)
        if (rst) begin
            for (i = 0; i < 6; i = i + 1) b[i] <= 8'd0;
            mode_q <= 3'd0;
            fcnt   <= 8'd0;
            pos    <= 3'd0;
            dir    <= 1'b0;
            phase  <= 8'd0;
            bincnt <= 6'd0;
            level  <= 3'd0;
            lvl_up <= 1'b1;
            chase  <= 2'd0;
            beat   <= 8'd0;
        end else if (frame_tick) begin
            mode_q <= mode;
            if (mode != mode_q) begin
                fcnt <= 8'd0;
                beat <= 8'd0;
            end else begin
                fcnt <= fcnt + 1'b1;
                case (mode)
                    SCANNER: begin
                        if (fcnt == 8'd9) begin fcnt <= 8'd0; step_bounce; end
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= (i == pos) ? 8'd255 : decay(b[i], 3'd3);
                    end
                    WAVE: begin
                        phase <= phase + 8'd3;
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= triangle(phase + i * 43);
                    end
                    SPARKLE: begin
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= decay(b[i], 3'd4);
                        if (fcnt == 8'd2) begin
                            fcnt <= 8'd0;
                            if (rnd[2:0] < 3'd6) b[rnd[2:0]] <= 8'd255;
                            if (rnd[10:8] < 3'd6 && rnd[15]) b[rnd[10:8]] <= 8'd140;
                        end
                    end
                    BINARY: begin
                        if (fcnt == 8'd24) begin fcnt <= 8'd0; bincnt <= bincnt + 1'b1; end
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= approach(b[i], bincnt[i] ? 8'd255 : 8'd0, 8'd32);
                    end
                    FILL: begin
                        if (fcnt == 8'd7) begin
                            fcnt <= 8'd0;
                            if (lvl_up) begin
                                if (level == 3'd6) begin lvl_up <= 1'b0; level <= 3'd5; end
                                else level <= level + 1'b1;
                            end else begin
                                if (level == 3'd0) begin lvl_up <= 1'b1; level <= 3'd1; end
                                else level <= level - 1'b1;
                            end
                        end
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= approach(b[i], (i < level) ? 8'd255 : 8'd0, 8'd24);
                    end
                    HEARTBEAT: begin
                        beat <= (beat == 8'd165) ? 8'd0 : beat + 1'b1;   // ~72 bpm
                        if (beat == 8'd0) begin
                            b[0] <= 8'd128; b[1] <= 8'd192; b[2] <= 8'd255;
                            b[3] <= 8'd255; b[4] <= 8'd192; b[5] <= 8'd128;
                        end else if (beat == 8'd36) begin
                            b[0] <= 8'd80;  b[1] <= 8'd120; b[2] <= 8'd170;
                            b[3] <= 8'd170; b[4] <= 8'd120; b[5] <= 8'd80;
                        end else begin
                            for (i = 0; i < 6; i = i + 1)
                                b[i] <= decay(b[i], 3'd3);
                        end
                    end
                    COMETS: begin
                        if (fcnt == 8'd7) begin fcnt <= 8'd0; step_bounce; end
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= (i == pos || i == 5 - pos) ? 8'd255 : decay(b[i], 3'd2);
                    end
                    CHASE: begin
                        if (fcnt == 8'd14) begin
                            fcnt  <= 8'd0;
                            chase <= (chase == 2'd2) ? 2'd0 : chase + 1'b1;
                        end
                        for (i = 0; i < 6; i = i + 1)
                            b[i] <= (i % 3 == chase) ? approach(b[i], 8'd255, 8'd64) : decay(b[i], 3'd2);
                    end
                endcase
            end
        end

    assign bright = {b[5], b[4], b[3], b[2], b[1], b[0]};
endmodule
