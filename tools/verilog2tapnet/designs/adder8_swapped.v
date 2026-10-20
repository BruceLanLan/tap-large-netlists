// adder8 with ports declared in another order and other internal style; --ports a,b,cin,s,cout
// restores the pin order of adder8.v.
module adder8_swapped(cout, s, cin, b, a);
  input  [7:0] b;
  input  [7:0] a;
  input        cin;
  output [7:0] s;
  output       cout;
  wire [8:0] sum = {1'b0, b} + {1'b0, a} + {8'b0, cin};
  assign s = sum[7:0];
  assign cout = sum[8];
endmodule
