# Cartridge demo: TAPBOY-8 (informative)

A game cartridge built as **one `tapnet/2` file**, to show how a game result becomes a claim that anyone can contest
and a contract can decide (TAP "Large Netlists and Verifiable Execution", Reference Implementation and Appendix C).
It imports `tapnet2.py` and `bisect_sim.py` from `../../assets/tap-draft-large-netlists/` (read-only).

## What it is

The file contains:

- **a small computer**: TAPBOY-8, an 8-bit single-cycle accumulator CPU built from NAND gates and LATCHes;
- **a program**: in one ROM record (`a = 8`, `w = 16`: 256 instructions of 16 bits);
- **256 bytes of RAM**: bank 0 of the RAM ports (one RAM read port `0x11` and one RAM write port `0x12`, §1 of
  the TAP), with `a = 8`, `w = 8`.

The earlier build, with the RAM made of 2,048 LATCHes, a 256-to-1 read multiplexer and a write decoder, is kept
for comparison: `build_cartridge(rom, ram="latch")` builds it, and `build/legacy/*.tapnet2` holds its files.

Each beat executes one instruction. The inputs are 8 button bits; the outputs are the 8-bit OUT byte, VALID (the
beat prints) and HALTED.

| Cartridge | Program | What it does |
|---|---|---|
| `build/hello.tapnet2` | `programs/hello.asm` | prints `Hello, tapnet/2!` and halts |
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
5. A cheater who changes 34 into 98, by a RAM write, a RAM read or a gate, loses in **one** verifier call. With one
   segment per beat and the memory root in the segment tuples, every such dispute ends in `verify_segment`.

## Measurements

From `python3 report.py` and `python3 dispute_demo.py`, default budget `(gates, extWords, chunks) = (6000, 256, 16)`:

| Item | RAM ports (`build/`) | LATCH RAM (`build/legacy/`, for comparison) |
|---|---|---|
| File size | **5,781 bytes**, **1 chunk** | 104,076 bytes (2.12 times the 49,152-byte limit of the netlist-bound NFT draft), 7 chunks |
| Records | 720 NAND, 25 LATCH, 1 ROM (4,096 bits), 1 RAM read port, 1 RAM write port (1 bank, 2,048 bits); 779 signals | 13,604 NAND, 2,073 LATCH, 1 ROM (4,096 bits); 15,703 signals |
| Bytes by kind | NAND 5,040, LATCH 100, ROM 540, RAM ports 85, header 16 | NAND 95,228, LATCH 8,292, ROM 540, header 16 |
| Gates by block | ALU 417; PC/jumps/halt 108; address adder 93; decoder 57; operand select 25; output buffers 20 | the same plus RAM write 6,756 and RAM read (8 × 256-to-1) 6,128 |
| Canonical segmentation | **1 segment**: 748 records, gate cost 802, extWords 10, chunks 2 | 3 segments: gate cost 6000 / 6000 / 3701, extWords 9 / 15 / 10, chunks 4 / 3 / 3 |
| State-list steps per beat | 1 | 9 |
| Honest segment witness (every 25th beat of the dodge run) | 48 to 224 bytes (at most 1 RAM read proof and 1 RAM write proof) | 48 to 968 bytes |
| `netlistHash2` (dodge) | `0x868733be3e0e79d2fadc3ada50fe9ad20a45b1b2bbd377686694ab05272243cf` | `0xd71cb9aca4af7fda9907b6ebf1295b9d892d49942f9715415f103336465ca970` |

The two builds give identical pins on every beat, and the 256 bytes of bank 0 equal the 2,048 RAM LATCHes of the
earlier build (`test_cartridge.py`, `LegacyComparisonTests`).

Disputes (dodge, the "good player" buttons, T = 651, K = 32, honest score 34, RAM-port build):

| Scenario | Result | Rounds | Verifier calls |
|---|---|---|---|
| A honest claim vs honest checker | no dispute | 0 | 0 |
| B claim of 98 (bit 6 of RAM[SCORE] forged after the write port) vs honest challenger | cheater loses | 9 (checkpoint 1 + beats 5 + tuple commit 1 + segment list 1 + verify 1) | 1 `verify_segment(seg_0 → seg_1)`, witness 224 B |
| C claim of 98 (bit 6 of an ALU adder output flipped) vs honest challenger | cheater loses | 8 | 1 `verify_segment(seg_0 → seg_1)`, witness 156 B |
| D honest claim vs the cheater of B as challenger | honest party wins | 9 | 1 |
| E claim of 98 (a bit of the RAM read port's output flipped) vs honest challenger | cheater loses | 8 | 1 `verify_segment(seg_0 → seg_1)`, witness 156 B |
| F honest claim vs the cheater of E as challenger | honest party wins | 8 | 1 |

On chain (Foundry, `contracts/` of this repository; Appendix B of the TAP): the verifier step of the one segment
takes 648,550 gas of execution in a beat with a RAM read and a RAM write (against 3.4 to 5.5 million for each of the
three segments of the LATCH-RAM build), and the upload 1,384,398 gas in two transactions (22,780,576 in eight for
the LATCH-RAM build). Scenarios A to F are replayed through the reference game (`test/game/E2E.t.sol`, `dodge/*`,
inputs published by an input provider): disputes B to F cost 2,276,817 to 2,434,957 gas for all parties together,
and the costliest move of each is the final `proveSegment`, at most 764,023 gas.

With LATCH RAM the same lies took 10 to 12 rounds, and a lie about a RAM bit was decided by `verify_beat_end` over
the 2,073-bit state list. The program takes 540 bytes in both builds; with LATCH RAM almost the whole file was the
256 bytes of RAM (about 12,900 NAND and 2,048 LATCH).

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
python3 test_cartridge.py               # all tests (about 10 s)
python3 report.py                       # writes build/*.tapnet2 and build/legacy/*.tapnet2, prints the measurements
python3 dispute_demo.py                 # the six dispute scenarios (about 2 s)
python3 assembler.py programs/dodge.asm # assembly listing
```

Python 3.9+, standard library only.

## Files

| File | Content |
|---|---|
| `netlist_builder.py` | a small circuit DSL: NAND and NOT/AND/OR/XOR/MUX built from it, LATCH registers, ROM, RAM read and write ports, adders, comparators, decoders, multiplexer trees; constant folding and sharing of equal gates; output buffers; writes and checks `tapnet/2` bytes through `tapnet2.py` (setting the MEM flag when the file has RAM ports) |
| `cpu.py` | TAPBOY-8 built with the DSL (program in ROM, RAM in bank 0 of the RAM ports, or in LATCHes with `ram="latch"`); returns the file and the LATCH ordinal map |
| `isa.py`, `assembler.py`, `emulator.py` | instruction set, two-pass assembler, instruction-level emulator independent of the circuit |
| `programs/hello.asm`, `programs/dodge.asm` | the two programs |
| `player.py` | button sequences: a "good player" that watches the screen, a player that presses nothing, random presses |
| `test_cartridge.py` | exhaustive gate tests, the assembler, the circuit against the emulator **beat by beat** (outputs and the whole state, including all 256 bytes of RAM) for both programs, three random button sequences and three random programs; the RAM-port build against the LATCH-RAM build; the dispute demo; on the LATCH-RAM build (no MEM), a cheater that forges the uncommitted `M` of its last segment tuple loses in both roles; the score opening |
| `dispute_demo.py` | the high-score disputes, played through the referee of `bisect_sim.py` |
| `report.py` | measures and writes `build/*.tapnet2` (RAM ports) and `build/legacy/*.tapnet2` (LATCH RAM) |

## Observations from building it

- The score is opened from the last checkpoint leaf (Appendix C of the TAP), so the circuit keeps the score on its
  output pins after it halts. A result printed on a beat that is not just before a checkpoint would need the output
  roots of every later beat up to the next checkpoint.
- Inputs are committed for every beat, also for beats on which the CPU does not read its buttons; the demo fixes them
  to zero ("canonical inputs" in §7.1 of the TAP).
- The all-zero initial state (and all-zero memory) means RAM cannot be preloaded; the dodge program writes its
  one-hot table at run time. A table that never changes belongs in ROM; a ROM read port (`0x13`) can read the
  program ROM at a second address in the same beat without a second copy of the data.
- A RAM write port takes only backward references, so it sits after the logic that computes its address, data and
  write enable; it takes effect for the next beat, like a LATCH.
- The outputs must be the last `nOut` signals and NAND references point backwards only, so the builder appends output
  buffers and declares LATCHes and the ROM's address lines first.
- Decode once: `tapnet2.evaluate_full` and `tapnet2.step` on bytes re-decode the file on every call; pass a decoded
  `Netlist` when running many beats.
