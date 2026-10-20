// Same function and structure as xor2.v, written differently: other names, an intermediate wire,
// operands swapped.
module xor_renamed (p, q, z);
  input p;
  input q;
  output z;
  wire t;
  assign t = q ^ p;
  assign z = t;
endmodule
