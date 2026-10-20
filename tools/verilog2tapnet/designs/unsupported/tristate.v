module tristate(input oe, input d, inout pad);
  assign pad = oe ? d : 1'bz;
endmodule
