// Two write ports in one process: port 1 (lower half only) has priority over port 0 when both write the
// same word. Yosys splits the enables by column, so the tool makes two RAM banks (bits 1:0 and 3:2); bank
// "bits 1:0" gets both write ports in priority order (the last write in file order wins), bank "bits 3:2"
// only port 0.
module ram_bytes(input clk, input e1, input e2, input [1:0] a1, input [1:0] a2, input [3:0] d1,
                 input [3:0] d2, input [1:0] ra, output [3:0] q);
  reg [3:0] m [0:3];
  integer k;
  initial for (k = 0; k < 4; k = k + 1) m[k] = 4'd0;
  always @(posedge clk) begin
    if (e1) m[a1] <= d1;
    if (e2) m[a2][1:0] <= d2[1:0];
  end
  assign q = m[ra];
endmodule
