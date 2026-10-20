// The accumulator CPU of acc_cpu.v with a 16 x 8 data memory instead of 4 registers, and two ops that
// address it from the input pins, so that a run writes and reads all of it.
//   state: pc[3:0], acc[7:0], halted; data memory r[0..15] (with --format 2 --ram one RAM bank, a = 4,
//          w = 8, two read ports and one write port); program 16 x 12 in a ROM (--rom)
//   word = op[11:8] imm[7:0]
//   0 LDX acc=r[din[3:0]]  1 LDI  2 ADDI  3 SUBI  4 ANDI  5 XORI  6 IN acc=din  7 ADDR acc+=r[imm[3:0]]
//   8 JMP  9 JNZ  A JZ  B OUT (valid=1)  C ST r[imm[3:0]]=acc  D LD acc=r[imm[3:0]]  E STX r[din[3:0]]=acc
//   F HALT
// Pins: din[7:0] in; acc[7:0], pc[3:0], valid, halted out (values before the clock edge).
module acc_ram(input clk, input [7:0] din, output [7:0] acc_o, output [3:0] pc_o,
               output valid, output halted_o);
  reg [3:0] pc = 4'd0;
  reg [7:0] acc = 8'd0;
  reg halted = 1'b0;
  reg [7:0] r [0:15];
  reg [11:0] prog [0:15];
  integer k;
  initial begin
    for (k = 0; k < 16; k = k + 1) r[k] = 8'd0;     // tapnet/2 memory starts all zero
    prog[0]  = 12'h600;   // IN
    prog[1]  = 12'hC03;   // ST r3       r3 = din
    prog[2]  = 12'h101;   // LDI 1
    prog[3]  = 12'hC00;   // ST r0       a = 1
    prog[4]  = 12'hD00;   // LD r0       loop: acc = a
    prog[5]  = 12'h701;   // ADDR r1     acc = a + b
    prog[6]  = 12'h703;   // ADDR r3
    prog[7]  = 12'hB00;   // OUT
    prog[8]  = 12'hE00;   // STX         r[din] = acc
    prog[9]  = 12'hD00;   // LD r0
    prog[10] = 12'hC01;   // ST r1       b = a
    prog[11] = 12'h000;   // LDX         acc = r[din]
    prog[12] = 12'hC00;   // ST r0       a = r[din]
    prog[13] = 12'h55A;   // XORI 0x5A
    prog[14] = 12'h904;   // JNZ loop    (until r[din] = 0x5A)
    prog[15] = 12'hF00;   // HALT
  end
  wire [11:0] w = prog[pc];
  wire [3:0] op = w[11:8];
  wire [7:0] imm = w[7:0];
  wire [7:0] rv = r[imm[3:0]];
  wire [7:0] rx = r[din[3:0]];
  always @(posedge clk) begin
    if (!halted) begin
      pc <= pc + 4'd1;
      case (op)
        4'h0: acc <= rx;
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
        4'hC: r[imm[3:0]] <= acc;
        4'hD: acc <= rv;
        4'hE: r[din[3:0]] <= acc;
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
