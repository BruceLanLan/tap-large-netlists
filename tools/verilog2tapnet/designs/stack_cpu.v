// Small stack machine, one instruction per beat (= per clock).
//   program: 32 x 8 ROM; an instruction is prog[pc], its immediate prog[pc + 1] (fetched in the same beat:
//            with --rom the second read of the ROM is a ROM read port 0x13)
//   stack  : 16 x 8 RAM, sp = number of entries (top = stk[sp-1], second = stk[sp-2]); with --ram one bank
//            with two read ports and two write ports (SWAP writes two entries)
//   op (low nibble): 0 NOP  1 PUSH imm  2 ADD  3 SUB  4 DUP  5 DROP  6 IN (push din)  7 OUT (valid=1)
//                    8 JZ imm (pop; jump if it was 0)  9 JMP imm  A XOR  B OVER  C SWAP  D AND
// Pins: din[7:0] in; tos[7:0], sp[3:0], pc[4:0], valid out (values before the clock edge).
module stack_cpu(input clk, input [7:0] din, output [7:0] tos, output [3:0] sp_o, output [4:0] pc_o,
                 output valid);
  reg [4:0] pc = 5'd0;
  reg [3:0] sp = 4'd0;
  reg [7:0] stk [0:15];
  reg [7:0] prog [0:31];
  integer k;
  initial begin
    for (k = 0; k < 16; k = k + 1) stk[k] = 8'd0;
    for (k = 0; k < 32; k = k + 1) prog[k] = 8'h00;
    // start: n = din & 7, acc = 0                     stack: [n, acc]
    prog[0]  = 8'h06;                     // IN
    prog[1]  = 8'h01; prog[2]  = 8'h07;   // PUSH 7
    prog[3]  = 8'h0D;                     // AND
    prog[4]  = 8'h01; prog[5]  = 8'h00;   // PUSH 0
    // loop: while n != 0: acc = 2 * (acc ^ din) ; n = n - 1
    prog[6]  = 8'h0B;                     // OVER         [n, acc, n]
    prog[7]  = 8'h08; prog[8]  = 8'd23;   // JZ end       [n, acc]
    prog[9]  = 8'h06;                     // IN           [n, acc, d]
    prog[10] = 8'h0A;                     // XOR          [n, acc^d]
    prog[11] = 8'h04;                     // DUP
    prog[12] = 8'h02;                     // ADD          [n, acc']
    prog[13] = 8'h07;                     // OUT
    prog[14] = 8'h0C;                     // SWAP         [acc', n]
    prog[15] = 8'h01; prog[16] = 8'h01;   // PUSH 1
    prog[17] = 8'h03;                     // SUB          [acc', n-1]
    prog[18] = 8'h0C;                     // SWAP         [n-1, acc']
    prog[19] = 8'h09; prog[20] = 8'd6;    // JMP loop
    // end: output acc, drop it and start again; n stays on the stack, so sp grows by one per pass and
    // wraps around (the stack is a ring of 16 entries), which writes every entry over a run
    prog[23] = 8'h07;                     // OUT
    prog[24] = 8'h05;                     // DROP
    prog[25] = 8'h09; prog[26] = 8'd0;    // JMP 0
  end
  wire [7:0] w0 = prog[pc];
  wire [7:0] imm = prog[pc + 5'd1];
  wire [3:0] op = w0[3:0];
  wire [7:0] top = stk[sp - 4'd1];
  wire [7:0] sec = stk[sp - 4'd2];
  always @(posedge clk) begin
    pc <= pc + 5'd1;
    case (op)
      4'h1: begin stk[sp] <= imm; sp <= sp + 4'd1; pc <= pc + 5'd2; end
      4'h2: begin stk[sp - 4'd2] <= sec + top; sp <= sp - 4'd1; end
      4'h3: begin stk[sp - 4'd2] <= sec - top; sp <= sp - 4'd1; end
      4'h4: begin stk[sp] <= top; sp <= sp + 4'd1; end
      4'h5: sp <= sp - 4'd1;
      4'h6: begin stk[sp] <= din; sp <= sp + 4'd1; end
      4'h8: begin sp <= sp - 4'd1; pc <= (top == 8'd0) ? imm[4:0] : pc + 5'd2; end
      4'h9: pc <= imm[4:0];
      4'hA: begin stk[sp - 4'd2] <= sec ^ top; sp <= sp - 4'd1; end
      4'hB: begin stk[sp] <= sec; sp <= sp + 4'd1; end
      4'hC: begin stk[sp - 4'd1] <= sec; stk[sp - 4'd2] <= top; end
      4'hD: begin stk[sp - 4'd2] <= sec & top; sp <= sp - 4'd1; end
      default: ;
    endcase
  end
  assign tos = top;
  assign sp_o = sp;
  assign pc_o = pc;
  assign valid = op == 4'h7;
endmodule
