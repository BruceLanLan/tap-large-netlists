// A written memory with nonzero initial contents: tapnet/2 memory starts all zero, so with --ram the tool
// keeps this memory as flip-flops (stored inverted where the initial bit is 1) and says why.
module ram_init(input clk, input we, input [2:0] a, input [3:0] d, output [3:0] q);
  reg [3:0] m [0:7];
  integer k;
  initial for (k = 0; k < 8; k = k + 1) m[k] = k + 4'd3;
  always @(posedge clk) if (we) m[a] <= d;
  assign q = m[a];
endmodule
