module negedge_ff(input clk, input d, output reg q = 1'b0);
  always @(negedge clk) q <= d;
endmodule
