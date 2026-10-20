# Assets for the TAP draft "Large Netlists and Verifiable Execution"

| File | What it is |
|---|---|
| `tapnet2.py` | Reference implementation (MIT): the `tapnet/2` format (encoding, validity, metrics, one-beat evaluation with ROM and memory, canonical ROM expansion; RAM read and write ports, ROM read ports, the flags byte), the memory tree and the state root of files with MEM, the trees and multiproofs of §0, chunking and `netlistHash2`, a model of `ITapLargeNetlistStore` that is also the chunk oracle, prefix roots and append proofs, input leaves, budgets and canonical segmentation, witnesses, `verify_segment`, the beat-end verifiers, checkpoints, tuple leaves, the witness encoding of §6.3 and the witness size bounds. |
| `bisect_sim.py` | The dispute of §7 between simulated parties, with a referee that holds only on-chain data; the claim layer `ClaimGame` applies the acceptance rules of §7.1 over several disputes. Includes the cheater classes listed in Test Cases. |
| `examples.py` | The example files (`sbox`, `mul4`, `message`, `large`; with ports `ram16`, `rammix`, `romports`, and the LATCH-built twin of `ram16`), random ROM netlists and the invalid samples, all generated in code. |
| `make_vectors.py` | Generates `vectors.json` (deterministic: fixed seeds, sorted keys, no timestamps). |
| `vectors.json` | Test vectors (sections listed in the TAP's Test Cases). |
| `test_tapnet2.py` | `unittest` suite, 50 tests (below). |
| `test_ram.py` | `unittest` suite for RAM ports and ROM read ports, 18 tests (below). |

## Regenerate and test

```
python3 make_vectors.py --check && python3 test_tapnet2.py && python3 test_ram.py
```

Python 3.9 or later, standard library only. `python3 make_vectors.py` (without `--check`) rewrites `vectors.json`.

SHA-256 of `vectors.json`:

```
b6ba365e912f149e1ab8e292588fac7f934cdb1fa1ba6d8d1b8ac3dabfd9f2d6
```

## What `test_tapnet2.py` checks

- `vectors.json` is regenerated and compared byte for byte, and every vector is verified again from its hex alone
  (files, invalid files, one-beat results, segmentations, every `verifySegment` and `verifyBeatEnd` case, checkpoints,
  the store and the disputes);
- validity rules, the examples (AES S-box, 4 × 4 product table, the `message` circuit that prints
  `Hello from tapnet/2 ROM!`), prefix roots, append proofs and multiproofs;
- equivalence of `tapnet/2` files with their canonical ROM expansions on random inputs, also against the
  independent TAP-02 evaluator `../tap-02/reference.py` of the TAPs repository (another copy can be named with the
  environment variable `TAP02_REFERENCE`); if that file is not found, the comparison runs against `tapnet2.py`
  only and one test is skipped;
- the dispute rules of §7: honest against every cheater class in both roles (including a party that forges the
  uncommitted `M` of its last segment tuple; tuples are compared by their committed fields), the claim rules of §7.1 (sock
  puppets, several challengers, bounds on `T` and `K`, both input cases, an input provider that may not claim).

## What `test_ram.py` checks

- validity of files with ports (flags byte, bank dimensions, backward references of write ports, ROM read port
  targets), metrics, and that files with RAM ports have no ROM expansion;
- a 16 × 4-bit memory built from LATCHes and the same memory built from RAM ports give identical outputs and
  memory contents on every beat; `rammix` matches a direct model of its behaviour (two banks, two writes to one
  bank in a beat, the later winning, a ROM read port addressed by memory contents); write order and reads that see
  the memory at the start of the beat; ROM read ports against duplicated ROMs; the memory tree and `ZERO_MEM`;
- `verify_segment` with memory proofs under three budgets (every honest step passes, through the encoding), the
  beat end (`stateRoot_{t+1} = SHA-256(0x0C ‖ state list root ‖ seg_m.M)`), mutations of read and write proofs,
  of the claimed `M`, of the `memRoot` argument and the combined root passed as `latchRoot`, `ZERO_MEM` reads, the
  encoding rules of the memory extension, the budget weight of RAM ports and the witness bound;
- the RAM step and dispute vectors from their hex alone, and disputes with RAM cheaters (read, forged write,
  write data, LATCH) in both roles, plus a claimant that does not open the agreed state root.

No file in this folder imports anything outside it except the optional `../tap-02/reference.py`.
