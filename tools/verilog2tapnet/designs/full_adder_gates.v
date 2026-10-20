// 1-bit full adder, written with gates.
module full_adder_gates(input a, input b, input cin, output s, output cout);
  wire p = a ^ b;
  assign s = p ^ cin;
  assign cout = (a & b) | (cin & p);
endmodule
