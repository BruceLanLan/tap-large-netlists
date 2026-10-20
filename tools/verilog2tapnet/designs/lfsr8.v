// 8-bit Fibonacci LFSR, taps 8,6,5,4, seed 0x01 (a nonzero initial value: stored inverted in LATCHes).
module lfsr8(input clk, output reg [7:0] s = 8'h01);
  always @(posedge clk)
    s <= {s[6:0], s[7] ^ s[5] ^ s[4] ^ s[3]};
endmodule
