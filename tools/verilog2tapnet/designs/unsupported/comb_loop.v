module comb_loop(input a, output y);
  wire x;
  assign x = ~(a & y);
  assign y = ~(x & a);
endmodule
