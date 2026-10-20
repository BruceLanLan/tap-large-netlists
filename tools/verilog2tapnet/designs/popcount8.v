// Number of ones in an 8-bit word (same pins as on-chain circuit #3151: 8 in, 4 out, LSB first).
module popcount8(input [7:0] x, output reg [3:0] c);
  integer i;
  always @* begin
    c = 4'd0;
    for (i = 0; i < 8; i = i + 1)
      c = c + x[i];
  end
endmodule
