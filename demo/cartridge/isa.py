"""TAPBOY-8 instruction set (shared by the assembler and the emulator; the netlist builder has its own
copy of the decode in gates).

Machine state (all zero at power-on, as tapnet requires):
    PC   8-bit program counter          ACC  8-bit accumulator
    B    8-bit index register           HALTED  1 bit (sticky)
    RAM  256 bytes (addresses 0x00..0xFF), all zero at power-on
Program memory: a ROM record with a = 8, w = 16 (256 instructions of 16 bits). Unused words are HALT.

One beat = one instruction (single cycle). Pins of the cartridge per beat:
    inputs : BTN[0..7]  the button byte of this beat (only read by IN)
    outputs: OUT[0..7] = ACC at the start of the beat (a "display register", shown on every beat)
             VALID     = 1 iff this beat executes OUT (the byte is a printed character / event)
             HALTED    = 1 iff the machine was already halted or executes HALT in this beat

Instruction word (16 bits):
    bits 15..12  op
    bit  11      M: operand is RAM[addr] (1) or the immediate (0)
    bit  10      X: addr = (imm + B) mod 256 (1) or addr = imm (0)
    bits  9..8   unused (0)
    bits  7..0   imm

    opnd = RAM[addr] if M else imm

op  mnemonic  effect                                   assembler forms
 0  NOP       -                                        NOP
 1  LD        ACC = opnd                               LD #imm | LD [a] | LD [a+B]
 2  ADD       ACC = ACC + opnd        (mod 256)        ADD ...
 3  SUB       ACC = ACC - opnd        (mod 256)        SUB ...
 4  AND       ACC = ACC & opnd                         AND ...
 5  OR        ACC = ACC | opnd                         OR ...
 6  XOR       ACC = ACC ^ opnd                         XOR ...
 7  LDB       B = opnd                                 LDB ...
 8  ST        RAM[addr] = ACC                          ST [a] | ST [a+B]
 9  JMP       PC = imm                                 JMP label
 A  JZ        if ACC == 0: PC = imm                    JZ label
 B  JNZ       if ACC != 0: PC = imm                    JNZ label
 C  IN        ACC = BTN                                IN
 D  OUT       VALID = 1 (OUT pins always show ACC)     OUT
 E  SWAP      ACC, B = B, ACC                          SWAP
 F  HALT      HALTED = 1; nothing changes any more     HALT

Otherwise PC = PC + 1 (mod 256). A halted machine keeps PC, ACC, B and RAM unchanged forever.
"""

OPS = {
    "NOP": 0x0, "LD": 0x1, "ADD": 0x2, "SUB": 0x3, "AND": 0x4, "OR": 0x5, "XOR": 0x6, "LDB": 0x7,
    "ST": 0x8, "JMP": 0x9, "JZ": 0xA, "JNZ": 0xB, "IN": 0xC, "OUT": 0xD, "SWAP": 0xE, "HALT": 0xF,
}
NAMES = {v: k for k, v in OPS.items()}

OPERAND_OPS = {"LD", "ADD", "SUB", "AND", "OR", "XOR", "LDB"}   # #imm, [a] or [a+B]
ADDRESS_OPS = {"ST"}                                            # [a] or [a+B]
JUMP_OPS = {"JMP", "JZ", "JNZ"}                                 # label / number
BARE_OPS = {"NOP", "IN", "OUT", "SWAP", "HALT"}

BIT_M = 1 << 11
BIT_X = 1 << 10
HALT_WORD = OPS["HALT"] << 12
ROM_WORDS = 256


def encode(op: str, imm: int = 0, m: bool = False, x: bool = False) -> int:
    return (OPS[op] << 12) | (BIT_M if m else 0) | (BIT_X if x else 0) | (imm & 0xFF)


def fields(word: int):
    """(op, M, X, imm) of a 16-bit instruction word."""
    return (word >> 12) & 0xF, (word >> 11) & 1, (word >> 10) & 1, word & 0xFF


def disassemble(word: int) -> str:
    op, m, x, imm = fields(word)
    name = NAMES[op]
    if name in BARE_OPS:
        return name
    if name in JUMP_OPS:
        return f"{name} 0x{imm:02X}"
    mem = f"[0x{imm:02X}{'+B' if x else ''}]"
    if name in ADDRESS_OPS:
        return f"{name} {mem}"
    return f"{name} {mem if m else f'#0x{imm:02X}'}"
