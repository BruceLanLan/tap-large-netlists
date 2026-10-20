// XOR as a sum of products.
module xor_sop(input a, input b, output y);
  assign y = (a & ~b) | (~a & b);
endmodule
