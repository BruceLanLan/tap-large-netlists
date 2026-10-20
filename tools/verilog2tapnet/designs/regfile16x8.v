// 16 x 8 register file, two asynchronous read ports and one synchronous write port.
// With --format 2 --ram: one RAM bank (a = 4, w = 8), two RAM read ports 0x11, one RAM write port 0x12.
// A write is visible to the read ports from the next beat on (reads see the memory before the edge).
module regfile16x8(input clk, input we, input [3:0] wa, input [7:0] wd, input [3:0] ra, input [3:0] rb,
                   output [7:0] qa, output [7:0] qb);
  reg [7:0] m [0:15];
  integer k;
  initial for (k = 0; k < 16; k = k + 1) m[k] = 8'd0;   // tapnet/2 memory starts all zero
  always @(posedge clk) if (we) m[wa] <= wd;
  assign qa = m[ra];
  assign qb = m[rb];
endmodule
