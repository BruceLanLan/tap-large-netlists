// A RAM written on the falling edge: not a RAM port (the tool maps it to flip-flops), then rejected as
// negative-edge flip-flops.
module ram_negedge(input clk, input we, input [1:0] a, input [3:0] d, output [3:0] q);
  reg [3:0] m [0:3];
  integer k;
  initial for (k = 0; k < 4; k = k + 1) m[k] = 4'd0;
  always @(negedge clk) if (we) m[a] <= d;
  assign q = m[a];
endmodule
