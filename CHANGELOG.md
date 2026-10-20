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

### Reference implementation

- Python reference: RAM ports, ROM read ports, memory tree and verifier; `test_ram.py` (18 tests); new example
  files `ram16`, `rammix`, `romports`; RAM vectors (`vectors.json` SHA-256
  `3008acc10a1d0694763a1c9f7a59990d172ba70472f09aa27021edf89b93a753`).
- `demo/cartridge/`: the RAM is now bank 0 of the RAM ports: 5,781 bytes, 1 chunk, 1 segment (the LATCH-RAM build
  is kept as `build_cartridge(rom, ram="latch")` and `build/legacy/`); 17 tests.
- `contracts/`: not yet updated for RAM ports (pending).

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
