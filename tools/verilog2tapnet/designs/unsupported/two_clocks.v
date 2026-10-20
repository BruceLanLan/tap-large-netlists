module two_clocks(input clk_a, input clk_b, input d, output reg qa = 1'b0, output reg qb = 1'b0);
  always @(posedge clk_a) qa <= d;
  always @(posedge clk_b) qb <= d;
endmodule
