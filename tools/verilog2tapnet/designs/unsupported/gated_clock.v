module gated_clock(input clk, input en, input d, output reg q = 1'b0);
  wire gclk = clk & en;
  always @(posedge gclk) q <= d;
endmodule
