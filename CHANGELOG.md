# Changelog

All notable changes to this repository. The TAP text is a draft; its number is assigned by the TAPs editors.

## Unreleased — 2026-10-20

### Standard (`TAPs/`)

- RAM read port `0x11`, RAM write port `0x12` (backward references only, writes for the next beat, the last write
  in file order wins) and ROM read port `0x13`; header byte 7 becomes the flags byte (bit 0 MEM iff the file has
  RAM ports); memory banks in one fixed memory tree of height 24 (leaf domain `0x0B`); for files with MEM the state
  root of beats `t ≥ 1` is `SHA-256(0x0C ‖ latchRoot ‖ memRoot)`; segment tuples gain `M`, hashed into the leaf for
  files with MEM; budget: `w + a` gates for a read port, `1 + a + w` for a write port, four `extWords` per RAM port;
  `verify_segment(netlistHash2, latchRoot, memRoot, a, b, witness, budget)` with read and write proofs in an
  optional witness extension; level 3 opens `stateRoot_t` and check 5 recomputes the combined root. Files without
  RAM ports or ROM read ports are unchanged in every byte, hash, tuple, leaf and witness.
- Open question 6 (RAM record or multi-port ROM) is replaced by a question on initial memory contents.
- Fixed (review): §4.5 names the committed fields of a tuple (five for a segment tuple of a file without MEM,
  whose `M` no leaf binds) and §7.4 compares tuples by them only: the last tuples at the list pick, and the tuples
  at `h` and in the bisection as leaves. Comparing the last segment tuples as values, `M` included, let a cheating
  claimant forge the `M` of its last tuple in a file without MEM, have the segment list picked although all its
  leaves agree, and make the honest challenger, as the "longer" of two equal-length lists, reveal a tuple
  `h + 1` it does not have. Equal tuples at `h` now imply different lengths. The §6.3 bound for files with RAM ports
  is 84,208 bytes, as without them (the 84,212 counted the memory part's two count bytes also when it is absent).
  Security Considerations state the reliance on validity rule 11 (the MEM flag).

### Reference implementation

- Python reference: RAM ports, ROM read ports, memory tree and verifier; `test_ram.py` (18 tests); new example
  files `ram16`, `rammix`, `romports`; `bisect_sim.py` compares tuples by their committed fields and has a new
  cheater class `m_forgery` (MForger), `test_tapnet2.py` 50 tests; RAM vectors (`vectors.json` SHA-256
  `b6ba365e912f149e1ab8e292588fac7f934cdb1fa1ba6d8d1b8ac3dabfd9f2d6`).
- `demo/cartridge/`: the RAM is now bank 0 of the RAM ports: 5,781 bytes, 1 chunk, 1 segment (the LATCH-RAM build
  is kept as `build_cartridge(rom, ram="latch")` and `build/legacy/`); 18 tests, including the M-forgery regression on
  the LATCH-RAM build.
- `contracts/`: the verifier implements RAM read and write ports, ROM read ports, the memory proofs and their
  encoding, and `verifySegment(netlistHash2, latchRoot, memRoot, a, b, witness, budget)` with six-field tuples; the
  game reads the MEM flag at the claim, checks the claimant's opening of `stateRoot_t` at level 3 (new reason
  `BadOpening`), hashes `M` into segment leaves and applies check 5 with the combined root. Its tree computations
  moved to the external library `TapLargeGameLib`, which brings the game below the EIP-170 code size limit (it was
  237 bytes over before). Fixed: the verifier's fast path could add a NAND or LATCH after a first record that alone
  exceeded `extWords` (a ROM with many address words under a small budget; with RAM ports also a write port under
  the default budget); the Python reference ends the segment there, and now so does the verifier. Tests: every
  vector (177 + 60) replayed, a 3,500-case differential corpus with RAM files, 118 end-to-end scenarios (RAM
  cheaters on `ram16` and `rammix`, a claimant without an opening, the cartridge's score disputes, and the
  M-forgery regression on `message`, `large` and the LATCH-RAM cartridge in both roles), gas cases for RAM ports,
  ROM read ports and the cartridge; 89 Foundry tests. The game compares the last tuples by their committed fields
  (a segment tuple as its leaf at index 0) and reverts `UnreachableEqualEnds` on equal tuples at `h` with equal
  lengths; `game_gen.py`'s mirror does the same. The default budget and the weight of four words per
  memory access are unchanged: the costliest segment with RAM ports measured 5.9 million gas against 9.3 million
  for the costliest call. Appendix B gives the measurements instead of the estimates.

### Tools

- New `tools/verilog2tapnet/`: a Verilog → `tapnet/1` / `tapnet/2` converter built on Yosys, self-contained in
  this repository (it uses `tapnet2.py` for validity, evaluation and hashes, and `assets/tap-02/reference.py` as the
  independent evaluator). Read-only memories become ROM records and further read ports of the same ROM ROM read
  ports `0x13`; memories with synchronous write ports on the clock and asynchronous read ports, starting at zero,
  become RAM banks (`0x11` / `0x12`, MEM flag; byte enables split banks; write ports in Yosys priority order);
  other memories stay flip-flops, with the reason in the report. The output tail moves NANDs, LATCHes and whole
  read ports to the end of the file when nothing earlier reads them, uses a second LATCH (or a second ROM read port)
  instead of two inverters, and documents the remaining costs. `validate.py` compares every design with Icarus
  Verilog beat by beat, including the whole RAM contents; 24 conversions of 20 designs (5 with RAM ports, 2 with ROM read ports) and
  9 rejected ones; 21 unit tests. RAM ports against LATCH-built memories: register file 16 × 8 94 against 9,446
  bytes, stack machine 4,031 against 16,321, accumulator CPU with a 16 × 8 data memory 3,374 against 9,866.
- CI: a new job `verilog2tapnet` (ubuntu-24.04) installs `yosys` and `iverilog` from the Ubuntu packages and runs
  the converter's tests; the existing jobs are unchanged.
- README (Chinese section): test counts brought in line with the English section.

## v0.1 (draft) — 2026-10-19

First snapshot of **Large Netlists and Verifiable Execution** (`TAP-draft-large-netlists`) and its reference
implementation. Not deployed. Reviewed internally twice; not externally audited. Not yet submitted to
[TapeOutProtocol/TAPs](https://github.com/TapeOutProtocol/TAPs).

### Standard (`TAPs/`)

- `tapnet/2` (ROM record `0x10`), `netlistHash2` over 16,384-byte chunks, the optional store
  `ITapLargeNetlistStore` (ERC-165 `0x8230af7f`), commitments, canonical segmentation under the budget
  `(6000, 256, 16)`, the one-step verifiers `verify_segment` and `verify_beat_end` with the witness encoding, and the
  dispute rules (several challengers, acceptance, input provider, bounds on `T` and `K`, time-based deadlines).
- A standalone Standards TAP that requires only TAP-02 and ERC-165. Use with netlist-bound NFTs
  (`TAP-draft-netlist-bound-nft`) is described in an informative section (§8), with text proposed for that draft's
  discussion.
- Earlier, unpublished versions of this text were written as an extension of the netlist-bound NFT draft; the
  normative rules (domain bytes, constants, budgets, witness encoding, game rules) are unchanged by the move to a
  standalone TAP.

### Reference implementation

- `assets/tap-draft-large-netlists/`: Python reference, 49 tests, `vectors.json`
  (SHA-256 `902afb750facf46f389d4ca20463cf23a004c98d319c4294c4ab73205b096015`).
- `contracts/`: store, verifiers, hash library and an informative dispute game; 68 Foundry tests including 76
  end-to-end dispute scenarios and the gas measurements of Appendix B.
- `demo/cartridge/`: TAPBOY-8 cartridge and score disputes, 15 tests.
- `measurements/`: L1 data fee snapshot (2026-10-18) and the attack scripts of the internal reviews.

Contact: X [@BruceBlue](https://x.com/BruceBlue), GitHub [@BruceLanLan](https://github.com/BruceLanLan).
