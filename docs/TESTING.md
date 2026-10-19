# Testing

Exact commands, run from the repository root, and the results of the last local run (2026-10-19, macOS,
Python 3.9.6 and 3.11.15, Foundry 1.8.3, solc 0.8.28).

## Requirements

- Python 3.9 or later, standard library only.
- Foundry 1.8.3 (`forge`), and forge-std v1.17.0 at `contracts/lib/forge-std` ([`contracts/lib/README.md`](../contracts/lib/README.md)).

## 1. Python reference implementation and vectors

```
cd assets/tap-draft-large-netlists
python3 make_vectors.py --check
python3 test_tapnet2.py
```

| Check | Result |
|---|---|
| `make_vectors.py --check` | `vectors.json matches sha256: 902afb750facf46f389d4ca20463cf23a004c98d319c4294c4ab73205b096015` |
| `test_tapnet2.py` | 49 tests, OK (about 10 s), Python 3.9.6 and 3.11.15 |

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
| `test/Vectors.t.sol` | 9 | pass (replays every `verifySegment`, `verifyBeatEnd`, `store` and `checkpoints` vector; checks the SHA-256 of `vectors.json`) |
| `test/Differential.t.sol` | 3 | pass (1,968-case corpus, two fuzz tests at 256 runs) |
| `test/Unit.t.sol` | 7 | pass |
| `test/GasStore.t.sol` | 1 | pass |
| `test/GasVerify.t.sol` | 1 | pass |
| `test/game/E2E.t.sol` | 31 | pass (76 end-to-end scenarios) |
| `test/game/MultiChallenger.t.sol` | 6 | pass |
| `test/game/GameLimits.t.sol` | 10 | pass |
| **Total** | **68** | **68 passed, 0 failed** (about 15 s) |

The gas tests rewrite `contracts/test/data/game_gas.csv`, and `gas_cases.bin.csv`; after
the run they are byte-identical to the committed files (`git diff --exit-code`).

## 3. Test data generators

The binary test data is generated with the Python reference and is reproducible byte for byte:

```
cd contracts/test/data
python3 gen_diff.py     # diff_cases.bin   (about 3 s)
python3 game_gen.py     # game_moves.bin   (about 3 s)
python3 gen_gas.py      # gas_cases.bin    (about 18 s)
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
| `test_cartridge.py` | 15 tests, OK (about 20 s), Python 3.9.6 and 3.11.15 |
| `report.py` | rewrites `build/hello.tapnet2` and `build/dodge.tapnet2`, byte-identical to the committed files; 3 canonical segments, honest witnesses 372 to 968 bytes |
| `dispute_demo.py` | every cheater loses; each dispute that reaches the verifier is decided by exactly one call (10 or 12 rounds) |

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
