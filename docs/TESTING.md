# Testing

Exact commands, run from the repository root, and the results of the last local run (2026-10-20, macOS; Python
3.9.6 and 3.11.15; Foundry 1.8.3, solc 0.8.28).

## Requirements

- Python 3.9 or later, standard library only.
- Foundry 1.8.3 (`forge`), and forge-std v1.17.0 at `contracts/lib/forge-std` ([`contracts/lib/README.md`](../contracts/lib/README.md)).

## 1. Python reference implementation and vectors

```
cd assets/tap-draft-large-netlists
python3 make_vectors.py --check
python3 test_tapnet2.py
python3 test_ram.py
```

| Check | Result |
|---|---|
| `make_vectors.py --check` | `vectors.json matches sha256: 3008acc10a1d0694763a1c9f7a59990d172ba70472f09aa27021edf89b93a753` |
| `test_tapnet2.py` | 49 tests, OK (about 13 s), Python 3.9.6 and 3.11.15 |
| `test_ram.py` | 18 tests, OK (about 4 s), Python 3.9.6 and 3.11.15: RAM read and write ports, ROM read ports, the memory tree, `verify_segment` with memory proofs, the memory encoding, and disputes with RAM cheaters |

`test_tapnet2.py` loads the independent TAP-02 evaluator `../tap-02/reference.py` (or the file named by the
environment variable `TAP02_REFERENCE`). Without it, the suite still runs and one test is skipped
(`OK (skipped=1)`).

## 2. Solidity reference implementation

```
cd contracts
forge build
forge test
```

| Suite | Tests | Result |
|---|---|---|
| `test/Vectors.t.sol` | 11 | pass (replays all 177 `verifySegment` and 60 `verifyBeatEnd` vectors, RAM vectors included, with the number of chunks read on every positive step, and the `store` and `checkpoints` vectors; checks the SHA-256 of `vectors.json`, the memory constants `Z_0 … Z_24` and `ZERO_MEM`, the segment leaves with `M`, and the memory encoding rules of §6.3) |
| `test/Differential.t.sol` | 3 | pass (3,500-case corpus, of which 1,532 cases on 40 files with RAM ports or ROM read ports; two fuzz tests at 256 runs, also run at 20,000) |
| `test/Unit.t.sol` | 7 | pass |
| `test/GasStore.t.sol` | 2 | pass (the 456-chunk upload; the cartridge's upload with RAM ports and with LATCH-based RAM) |
| `test/GasVerify.t.sol` | 1 | pass (114 cases, including RAM read and write ports, ROM read ports and the cartridge) |
| `test/game/E2E.t.sol` | 44 | pass (111 end-to-end scenarios on five files, three of them with RAM ports: `ram16`, `rammix` and the cartridge) |
| `test/game/MultiChallenger.t.sol` | 6 | pass |
| `test/game/GameLimits.t.sol` | 10 | pass (including the costliest final calls, with and without RAM ports, through the game) |
| **Total** | **84** | **84 passed, 0 failed** (about 60 s) |

Contract sizes (`forge build --sizes`; EIP-170 limit 24,576 bytes): `TapLargeDisputeGame` 24,079, `TapLargeGameLib`
3,223, `TapLargeVerifier` 13,837, `TapLargeNetlistStoreOwned` 4,468 bytes of runtime code. `GameHarness`, a
test-only subclass of the game, is above the limit (26,302 bytes), so `forge build --sizes` reports an error for it;
Foundry tests do not enforce the limit.

The gas tests rewrite `contracts/test/data/game_gas.csv` and `gas_cases.bin.csv`; after
the run they are byte-identical to the committed files (`git diff --exit-code`).

## 3. Test data generators

The binary test data is generated with the Python reference and is reproducible byte for byte:

```
cd contracts/test/data
python3 gen_diff.py     # diff_cases.bin   (about 6 s)
python3 game_gen.py     # game_moves.bin   (about 10 s)
python3 gen_gas.py      # gas_cases.bin    (about 70 s)
```

All three outputs were regenerated and are identical to the committed files.

## 4. Cartridge demo

```
cd demo/cartridge
python3 test_cartridge.py
python3 report.py
python3 dispute_demo.py
```

| Check | Result |
|---|---|
| `test_cartridge.py` | 17 tests, OK (about 8 s), Python 3.9.6 and 3.11.15 |
| `report.py` | rewrites `build/*.tapnet2` (RAM ports: 5,781 bytes, 1 chunk, 1 canonical segment, honest witnesses 48 to 224 bytes) and `build/legacy/*.tapnet2` (LATCH RAM: 104,076 bytes, 7 chunks, 3 segments, 48 to 968 bytes), byte-identical to the committed files |
| `dispute_demo.py` | six scenarios; every cheater loses; each dispute that reaches the verifier is decided by exactly one call of `verify_segment` (8 or 9 rounds) |

## 5. Measurements

```
cd measurements/attacks
python3 attack1.py         # witness size of the first review's file: at most 48 bytes per segment
python3 attack2.py         # state tree size attack: both forged witnesses rejected (False, False)
python3 attack3.py         # hidden middle checkpoint: found by the challenger from the published list
python3 worst_witness.py   # near-worst-case witness: 74,896 bytes (bound 84,208, cap 98,304), about 20 s
```

All four ran with the results shown in the comments.

## Continuous integration

`.github/workflows/ci.yml` runs sections 1, 3 (`gen_diff.py`, `game_gen.py`), 4 (`test_cartridge.py`) and the
first three scripts of section 5 on Python 3.9 and 3.11, and section 2 with Foundry 1.8.3, and fails if any
generated file differs from the committed one. It has not run yet: the repository has no remote.
