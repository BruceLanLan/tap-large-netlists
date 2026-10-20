// 4-bit counter with synchronous reset and enable.
module counter4(input clk, input rst, input en, output reg [3:0] q = 4'd0);
  always @(posedge clk)
    if (rst)     q <= 4'd0;
    else if (en) q <= q + 4'd1;
endmodule
