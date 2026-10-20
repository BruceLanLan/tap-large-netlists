// A 6-bit phase counter addressing a 64 x 8 table (a triangle-ish wave). With --format 2 --rom the
// table becomes one ROM record (a = 6, w = 8).
module rom_wave(input clk, input step, output [7:0] level, output [5:0] phase_o);
  reg [5:0] phase = 6'd0;
  reg [7:0] table_ [0:63];
  integer k;
  initial for (k = 0; k < 64; k = k + 1)
    table_[k] = (k < 32) ? (k * 8 + (k % 3)) : ((63 - k) * 8 + (k % 5));
  always @(posedge clk) if (step) phase <= phase + 6'd1;
  assign level = table_[phase];
  assign phase_o = phase;
endmodule
