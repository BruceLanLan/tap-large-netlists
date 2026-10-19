# Assets for the TAP draft "Large Netlists and Verifiable Execution"

| File | What it is |
|---|---|
| `tapnet2.py` | Reference implementation (MIT): the `tapnet/2` format (encoding, validity, metrics, one-beat evaluation with ROM, canonical ROM expansion), the trees and multiproofs of §0, chunking and `netlistHash2`, a model of `ITapLargeNetlistStore` that is also the chunk oracle, prefix roots and append proofs, input leaves, budgets and canonical segmentation, witnesses, `verify_segment`, the beat-end verifiers, checkpoints, tuple leaves, the witness encoding of §6.3 and the witness size bounds. |
| `bisect_sim.py` | The dispute of §7 between simulated parties, with a referee that holds only on-chain data; the claim layer `ClaimGame` applies the acceptance rules of §7.1 over several disputes. Includes the cheater classes listed in Test Cases. |
| `examples.py` | The example files (`sbox`, `mul4`, `message`, `large`), random ROM netlists and the invalid samples, all generated in code. |
| `make_vectors.py` | Generates `vectors.json` (deterministic: fixed seeds, sorted keys, no timestamps). |
| `vectors.json` | Test vectors (sections listed in the TAP's Test Cases). |
| `test_tapnet2.py` | `unittest` suite, 49 tests (below). |

## Regenerate and test

```
python3 make_vectors.py --check && python3 test_tapnet2.py
```

Python 3.9 or later, standard library only. `python3 make_vectors.py` (without `--check`) rewrites `vectors.json`.

SHA-256 of `vectors.json`:

```
902afb750facf46f389d4ca20463cf23a004c98d319c4294c4ab73205b096015
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
- the dispute rules of §7: honest against every cheater class in both roles, the claim rules of §7.1 (sock
  puppets, several challengers, bounds on `T` and `K`, both input cases, an input provider that may not claim).

No file in this folder imports anything outside it except the optional `../tap-02/reference.py`.
