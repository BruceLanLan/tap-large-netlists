// Traffic light controller: main road green until a car waits on the side road, then
// yellow (2 beats), side green (4 beats), side yellow (2 beats), back to main green.
module traffic(input clk, input car, output [2:0] main_rgy, output [2:0] side_rgy, output [1:0] st);
  localparam MG = 2'd0, MY = 2'd1, SG = 2'd2, SY = 2'd3;
  reg [1:0] state = MG;
  reg [2:0] timer = 3'd0;
  always @(posedge clk) begin
    case (state)
      MG: if (car) begin state <= MY; timer <= 3'd1; end
      MY: if (timer == 0) begin state <= SG; timer <= 3'd3; end else timer <= timer - 3'd1;
      SG: if (timer == 0) begin state <= SY; timer <= 3'd1; end else timer <= timer - 3'd1;
      SY: if (timer == 0) begin state <= MG; timer <= 3'd0; end else timer <= timer - 3'd1;
    endcase
  end
  // {red, yellow, green}
  assign main_rgy = state == MG ? 3'b001 : state == MY ? 3'b010 : 3'b100;
  assign side_rgy = state == SG ? 3'b001 : state == SY ? 3'b010 : 3'b100;
  assign st = state;
endmodule
