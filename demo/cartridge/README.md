# Cartridge demo: TAPBOY-8 (informative)

A game cartridge built as **one `tapnet/2` file**, to show how a game result becomes a claim that anyone can contest
and a contract can decide (TAP "Large Netlists and Verifiable Execution", Reference Implementation and Appendix C).
It imports `tapnet2.py` and `bisect_sim.py` from `../../assets/tap-draft-large-netlists/` (read-only).

## What it is

The file contains:

- **a small computer**: TAPBOY-8, an 8-bit single-cycle accumulator CPU built from NAND gates and LATCHes;
- **a program**: in one ROM record (`a = 8`, `w = 16`: 256 instructions of 16 bits);
- **256 bytes of RAM**: built from LATCHes (the format has no writable memory record).

Each beat executes one instruction. The inputs are 8 button bits; the outputs are the 8-bit OUT byte, VALID (the
beat prints) and HALTED.

| Cartridge | Program | What it does |
|---|---|---|
| `build/hello.tapnet2` | `programs/hello.asm` | prints `Hello, TAP-721!` and halts |
| `build/dodge.tapnet2` | `programs/dodge.asm` | a dodge game: 8 lanes, a rock falls each round and a coin sits in the opposite lane; the player moves left or right one lane per round. A hit ends the game; a coin gives +3, surviving a round +1; after 16 rounds the score is printed and the CPU halts |

## How a high score becomes checkable

1. **The game is deterministic**: the same cartridge and the same buttons always give the same score, so "my score
   is 34" is a statement about what a circuit computes.
2. **The player makes a claim**: with the cartridge stored on chain chunk by chunk (identified by `netlistHash2`),
   the player publishes `(netlistHash2, T = 651, K = 32, inputRoot, checkpointRoot)` and the 22 checkpoint leaves.
   `inputRoot` commits the buttons of every beat; each checkpoint leaf commits the state and all outputs so far.
3. **The score is in the claim**: the OUT pins show the accumulator on every beat, and after HALT it holds the score.
   The last checkpoint leaf commits the outputs of the last beat, so the score is opened with two preimages and three
   SHA-256 calls (`dispute_demo.open_final_score`; Appendix C of the TAP).
4. **Anyone who disagrees challenges**: the challenger recomputes the run, names the first checkpoint that differs,
   and bisection narrows the dispute to one beat and then to one segment of at most 6,000 gate-equivalents, which a
   contract re-evaluates (`verify_segment`) or one step of the outputs or new state (`verify_beat_end`).
5. A cheater who changes 34 into 98, in RAM or in a gate, loses in **one** verifier call.

## Measurements

From `python3 report.py` and `python3 dispute_demo.py`, default budget `(gates, extWords, chunks) = (6000, 256, 16)`:

| Item | Value |
|---|---|
| File size | **104,076 bytes** (2.12 times the 49,152-byte limit of the netlist-bound NFT draft), **7 chunks** |
| Records | 13,604 NAND, 2,073 LATCH, 1 ROM (4,096 bits), 15,703 signals; nIn 8, nOut 10 |
| Bytes by kind | NAND 95,228, LATCH 8,292, ROM 540, header 16 |
| Gates by block | RAM write 6,756; RAM read (8 × 256-to-1) 6,128; ALU 417; PC/jumps/halt 108; address adder 93; decoder 57; operand select 25; output buffers 20 |
| Canonical segmentation | **3 segments**: 5,977 / 6,000 / 3,701 records, gate cost 6000 / 6000 / 3701, extWords 9 / 15 / 10, chunks 4 / 3 / 3 |
| Honest witness size | 372 to 968 bytes per segment, 3 chunks read per step |
| `netlistHash2` (dodge) | `0xd71cb9aca4af7fda9907b6ebf1295b9d892d49942f9715415f103336465ca970` |

Disputes (dodge, the "good player" buttons, T = 651, honest score 34):

| Scenario | Result | Rounds | Verifier calls |
|---|---|---|---|
| A honest claim vs honest checker | no dispute | 0 | 0 |
| B claim of 98 (bit 6 of the SCORE byte in RAM flipped) vs honest challenger | cheater loses | 12 (checkpoint 1 + beats 5 + tuple commit 1 + state list 4 + verify 1) | 1 `verify_beat_end[state]`, witness 1,666 B |
| C claim of 98 (bit 6 of an ALU adder output flipped) vs honest challenger | cheater loses | 10 (checkpoint 1 + beats 4 + tuple commit 1 + segment list 3 + verify 1) | 1 `verify_segment(seg_1 → seg_2)`, witness 968 B |
| D honest claim vs cheating challenger | honest party wins | 12 | 1 |

The program takes 540 bytes; almost the whole file is the 256 bytes of LATCH-based RAM (about 12,900 NAND and 2,048
LATCH). ROM is cheap, RAM is expensive (Rationale and Open question 6 of the TAP).

## Instruction set (TAPBOY-8)

Single-cycle 8-bit accumulator machine: `PC`, `ACC`, `B` (index register), `HALTED`, 256 bytes of RAM, all zero at
reset. Instructions are 16 bits: `[15:12] op | [11] M (operand from memory) | [10] X (address = imm + B) | [7:0] imm`.

| op | Mnemonic | Effect | | op | Mnemonic | Effect |
|---|---|---|---|---|---|---|
| 0 | NOP | — | | 8 | ST | RAM[addr] = ACC |
| 1 | LD | ACC = opnd | | 9 | JMP | PC = imm |
| 2 | ADD | ACC += opnd | | A | JZ | jump if ACC == 0 |
| 3 | SUB | ACC −= opnd | | B | JNZ | jump if ACC != 0 |
| 4 | AND | ACC &= opnd | | C | IN | ACC = buttons |
| 5 | OR | ACC \|= opnd | | D | OUT | VALID = 1 (the OUT pins always show ACC) |
| 6 | XOR | ACC ^= opnd | | E | SWAP | swap ACC and B |
| 7 | LDB | B = opnd | | F | HALT | halt; the state no longer changes |

`opnd = M ? RAM[addr] : imm`, `addr = X ? (imm + B) mod 256 : imm`. Assembly syntax: `LD #5`, `LD [0x10]`,
`LD [0xF0+B]`, `ST [x]`, `JZ label`, `.equ`, `.print "..."`. See `isa.py`.

## Run

```
python3 test_cartridge.py               # all tests (about 20 s)
python3 report.py                       # writes build/*.tapnet2, prints sizes, gates, segments, witness sizes
python3 dispute_demo.py                 # the four dispute scenarios (about 8 s)
python3 assembler.py programs/dodge.asm # assembly listing
```

Python 3.9+, standard library only.

## Files

| File | Content |
|---|---|
| `netlist_builder.py` | a small circuit DSL: NAND and NOT/AND/OR/XOR/MUX built from it, LATCH registers, ROM, adders, comparators, decoders, multiplexer trees; constant folding and sharing of equal gates; output buffers; writes and checks `tapnet/2` bytes through `tapnet2.py` |
| `cpu.py` | TAPBOY-8 built with the DSL (program in ROM, RAM in LATCHes); returns the file and the LATCH ordinal map |
| `isa.py`, `assembler.py`, `emulator.py` | instruction set, two-pass assembler, instruction-level emulator independent of the circuit |
| `programs/hello.asm`, `programs/dodge.asm` | the two programs |
| `player.py` | button sequences: a "good player" that watches the screen, a player that presses nothing, random presses |
| `test_cartridge.py` | exhaustive gate tests, the assembler, the circuit against the emulator **beat by beat** (outputs and the whole state, including all 256 bytes of RAM) for both programs, three random button sequences and three random programs; the dispute demo; the score opening |
| `dispute_demo.py` | the high-score disputes, played through the referee of `bisect_sim.py` |
| `report.py` | measures and writes `build/hello.tapnet2` and `build/dodge.tapnet2` |

## Observations from building it

- The score is opened from the last checkpoint leaf (Appendix C of the TAP), so the circuit keeps the score on its
  output pins after it halts. A result printed on a beat that is not just before a checkpoint would need the output
  roots of every later beat up to the next checkpoint.
- Inputs are committed for every beat, also for beats on which the CPU does not read its buttons; the demo fixes them
  to zero ("canonical inputs" in §7.1 of the TAP).
- The all-zero initial state means RAM cannot be preloaded; the dodge program writes its one-hot table at run time.
  A ROM serves one address per beat, so fetching an instruction and looking up a table in the same beat would need a
  second ROM record.
- The outputs must be the last `nOut` signals and NAND references point backwards only, so the builder appends output
  buffers and declares LATCHes and the ROM's address lines first.
- Decode once: `tapnet2.step` on bytes re-decodes the file on every call; passing a decoded `Netlist` is about ten
  times faster (3 ms against 31 ms per beat here).
