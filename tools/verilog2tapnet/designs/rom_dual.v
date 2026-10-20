// One 64 x 8 table read at two addresses in the same beat (a quarter-wave apart, like sin/cos).
// With --format 2 --rom: one ROM record 0x10 and one ROM read port 0x13 that reads the same data.
module rom_dual(input clk, input step, output [7:0] s, output [7:0] c, output [5:0] phase_o);
  reg [5:0] phase = 6'd0;
  reg [7:0] t [0:63];
  integer k;
  initial for (k = 0; k < 64; k = k + 1)
    t[k] = (k < 32) ? (k * 8 + (k % 3)) : ((63 - k) * 8 + (k % 5));
  always @(posedge clk) if (step) phase <= phase + 6'd1;
  assign s = t[phase];
  assign c = t[phase + 6'd16];
  assign phase_o = phase;
endmodule
