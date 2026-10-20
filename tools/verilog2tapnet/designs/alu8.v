// 8-bit ALU: op 0 add, 1 sub, 2 and, 3 or, 4 xor, 5 shl, 6 shr, 7 pass b. Flags zero and carry.
module alu8(input [7:0] a, input [7:0] b, input [2:0] op, output reg [7:0] y, output zero, output reg carry);
  always @* begin
    carry = 1'b0;
    case (op)
      3'd0: {carry, y} = a + b;
      3'd1: {carry, y} = {1'b0, a} - {1'b0, b};
      3'd2: y = a & b;
      3'd3: y = a | b;
      3'd4: y = a ^ b;
      3'd5: {carry, y} = {a, 1'b0};
      3'd6: begin y = {1'b0, a[7:1]}; carry = a[0]; end
      default: y = b;
    endcase
  end
  assign zero = (y == 8'd0);
endmodule
