// 1-bit full adder, behavioural.
module full_adder(input a, input b, input cin, output s, output cout);
  assign {cout, s} = a + b + cin;
endmodule
