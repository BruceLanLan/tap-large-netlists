// A RAM written on one clock while a register uses another: rejected (single clock only).
module ram_two_clocks(input clk, input clk2, input we, input [1:0] a, input [3:0] d, output [3:0] q,
                      output reg r = 1'b0);
  reg [3:0] m [0:3];
  integer k;
  initial for (k = 0; k < 4; k = k + 1) m[k] = 4'd0;
  always @(posedge clk) if (we) m[a] <= d;
  always @(posedge clk2) r <= we;
  assign q = m[a];
endmodule
