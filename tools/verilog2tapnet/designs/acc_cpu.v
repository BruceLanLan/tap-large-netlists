// Tiny accumulator CPU, one instruction per beat (= per clock).
//   state: pc[3:0], acc[7:0], 4 x 8-bit registers r0..r3 (flip-flop RAM), halted
//   program: 16 x 12-bit words in a read-only memory (a ROM record with --format 2 --rom)
//   word = op[11:8] imm[7:0]
//   0 NOP  1 LDI acc=imm  2 ADDI  3 SUBI  4 ANDI  5 XORI  6 IN acc=din  7 ADDR acc+=r[imm[1:0]]
//   8 JMP  9 JNZ  A JZ  B OUT (valid=1)  C ST r[imm]=acc  D LD acc=r[imm]  E SHL acc<<=1  F HALT
// Pins: din[7:0] in; acc[7:0], pc[3:0], valid, halted out (values before the clock edge).
module acc_cpu(input clk, input [7:0] din, output [7:0] acc_o, output [3:0] pc_o,
               output valid, output halted_o);
  reg [3:0] pc = 4'd0;
  reg [7:0] acc = 8'd0;
  reg halted = 1'b0;
  reg [7:0] r [0:3];
  reg [11:0] prog [0:15];
  initial begin
    r[0] = 0; r[1] = 0; r[2] = 0; r[3] = 0;
    prog[0]  = 12'h600;   // IN          acc = din (beat 0 only)
    prog[1]  = 12'hC03;   // ST r3       r3 = din
    prog[2]  = 12'h101;   // LDI 1
    prog[3]  = 12'hC00;   // ST r0       a = 1
    prog[4]  = 12'hD00;   // LD r0       loop: acc = a
    prog[5]  = 12'h701;   // ADDR r1     acc = a + b
    prog[6]  = 12'h703;   // ADDR r3     + din0
    prog[7]  = 12'hB00;   // OUT
    prog[8]  = 12'hC02;   // ST r2       t = acc
    prog[9]  = 12'hD00;   // LD r0
    prog[10] = 12'hC01;   // ST r1       b = a
    prog[11] = 12'hD02;   // LD r2
    prog[12] = 12'hC00;   // ST r0       a = t
    prog[13] = 12'h50F;   // XORI 0x0F
    prog[14] = 12'h904;   // JNZ loop
    prog[15] = 12'hF00;   // HALT
  end
  wire [11:0] w = prog[pc];
  wire [3:0] op = w[11:8];
  wire [7:0] imm = w[7:0];
  wire [7:0] rv = r[imm[1:0]];
  always @(posedge clk) begin
    if (!halted) begin
      pc <= pc + 4'd1;
      case (op)
        4'h1: acc <= imm;
        4'h2: acc <= acc + imm;
        4'h3: acc <= acc - imm;
        4'h4: acc <= acc & imm;
        4'h5: acc <= acc ^ imm;
        4'h6: acc <= din;
        4'h7: acc <= acc + rv;
        4'h8: pc <= imm[3:0];
        4'h9: if (acc != 0) pc <= imm[3:0];
        4'hA: if (acc == 0) pc <= imm[3:0];
        4'hC: r[imm[1:0]] <= acc;
        4'hD: acc <= rv;
        4'hE: acc <= {acc[6:0], 1'b0};
        4'hF: begin halted <= 1'b1; pc <= pc; end
        default: ;
      endcase
    end
  end
  assign acc_o = acc;
  assign pc_o = pc;
  assign valid = !halted && op == 4'hB;
  assign halted_o = halted;
endmodule
