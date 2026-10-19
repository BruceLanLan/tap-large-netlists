# Changelog

All notable changes to this repository. The TAP text is a draft; its number is assigned by the TAPs editors.

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
  (`TAP-draft-netlist-bound-nft`) is an optional section (§8), with text proposed for that draft's discussion.
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
