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
| `make_vectors.py --check` | `vectors.json matches sha256: b6ba365e912f149e1ab8e292588fac7f934cdb1fa1ba6d8d1b8ac3dabfd9f2d6` |
| `test_tapnet2.py` | 50 tests, OK (about 12 s), Python 3.9.6 and 3.11.15 |
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
| `test/game/E2E.t.sol` | 56 | pass (125 end-to-end scenarios on seven files, three of them with RAM ports: `ram16`, `rammix` and the cartridge; the other four are `message`, `large`, the cartridge's LATCH-RAM build, on which a party that forges the uncommitted `M` of its last segment tuple loses in both roles, and the inference demo's classifier `inference` (seven scenarios, `test_inference_gasPerMove` writes `test/data/inference_gas.csv`)) |
| `test/game/MultiChallenger.t.sol` | 6 | pass |
| `test/game/GameLimits.t.sol` | 10 | pass (including the costliest final calls, with and without RAM ports, through the game) |
| **Total** | **96** | **96 passed, 0 failed** (about 70 s) |

Contract sizes (`forge build --sizes`; EIP-170 limit 24,576 bytes): `TapLargeDisputeGame` 24,114, `TapLargeGameLib`
3,223, `TapLargeVerifier` 13,837, `TapLargeNetlistStoreOwned` 4,468 bytes of runtime code. `GameHarness`, a
test-only subclass of the game, is above the limit (26,337 bytes), so `forge build --sizes` reports an error for it;
Foundry tests do not enforce the limit.

The gas tests rewrite `contracts/test/data/game_gas.csv`, `gas_cases.bin.csv` and `inference_gas.csv`; after
the run they are byte-identical to the committed files (`git diff --exit-code`).

## 3. Test data generators

The binary test data is generated with the Python reference and is reproducible byte for byte:

```
cd contracts/test/data
python3 gen_diff.py     # diff_cases.bin   (about 6 s)
python3 game_gen.py     # game_moves.bin   (about 30 s; includes the inference game of demo/inference)
python3 gen_gas.py      # gas_cases.bin    (about 70 s)
```

All three outputs were regenerated and are identical to the committed files.

`game_gen.py` plays every scenario against a Python mirror of the game; the mirror decides exactly as the
contract does (the same reveal checks, the same list pick by the committed fields of the last tuples, tuples at
`h` and in the bisection compared as leaves, the same midpoints and final call), and `E2E.t.sol` asserts that the
contract reaches the mirror's outcome in every scenario, including the index asked at every reveal. The
M-forgery scenarios (`*/j_m_forgery_*`, `legacy/m_forgery_*`) are the regression for the review finding that the
game compared the uncommitted `M` of files without MEM: against the contract before the fix, all five
`test_mForgery_*` tests fail (the game asks for another tuple than the mirror, and picks the segment list).

## 4. Cartridge demo

```
cd demo/cartridge
python3 test_cartridge.py
python3 report.py
python3 dispute_demo.py
```

| Check | Result |
|---|---|
| `test_cartridge.py` | 18 tests, OK (about 12 s), Python 3.9.6 and 3.11.15 |
| `report.py` | rewrites `build/*.tapnet2` (RAM ports: 5,781 bytes, 1 chunk, 1 canonical segment, honest witnesses 48 to 224 bytes) and `build/legacy/*.tapnet2` (LATCH RAM: 104,076 bytes, 7 chunks, 3 segments, 48 to 968 bytes), byte-identical to the committed files |
| `dispute_demo.py` | six scenarios; every cheater loses; each dispute that reaches the verifier is decided by exactly one call of `verify_segment` (8 or 9 rounds) |

## 4b. Inference demo

```
cd demo/inference
python3 test_inference.py
python3 mlp_dispute.py
python3 mlp_report.py
```

| Check | Result |
|---|---|
| `test_inference.py` | 12 tests, OK (about 30 s), Python 3.9.6 and 3.11.15: the integer model reproduces `train.py`'s predictions on the 450 test digits (accuracy 0.9778) and its beat schedule equals the arithmetic; `rtl/mlp_digits.v` is the generator's output; the committed netlist is valid, has 2 ROM records, 421 LATCHes, 6 chunks and 3 canonical segments within the budget; the netlist equals the model on every beat of 50 test digits; the register bits read through the LATCH mapping equal the model's; the seven dispute scenarios (every cheater loses in one verifier call, each segment reached); the class opening rejects another class; honest witnesses of all three segments verify. `ConverterTests` (a fresh Yosys conversion behaves like the model on 3 digits) runs when `yosys` is on `PATH`, otherwise `skipped=1` |
| `mlp_dispute.py` | seven scenarios, every cheater loses, exactly one verifier call each (about 3 s) |
| `mlp_report.py` | compares 60 digits (60 exact matches), measures witnesses, reads `contracts/test/data/inference_gas.csv` and `game_gas.csv`, rewrites `build/numbers.json`, byte-identical to the committed file |

`train.py` (numpy, scikit-learn) and `mlp_build.py` (yosys) regenerate `model/` and `build/mlp_digits.tapnet2`; they
are not part of the test commands, and a rebuild with another Yosys version may change the file's bytes and gate
count (the committed file is the one the numbers refer to).

## 5. Measurements

```
cd measurements/attacks
python3 attack1.py         # witness size of the first review's file: at most 48 bytes per segment
python3 attack2.py         # state tree size attack: both forged witnesses rejected (False, False)
python3 attack3.py         # hidden middle checkpoint: found by the challenger from the published list
python3 worst_witness.py   # near-worst-case witness: 74,896 bytes (bound 84,208, cap 98,304), about 20 s
```

All four ran with the results shown in the comments.

## 6. Verilog converter

Needs `yosys` (minimum 0.69, `MIN_YOSYS` in `verilog2tapnet.py`) and Icarus Verilog (`iverilog`, `vvp`) on
`PATH`; local run with Yosys 0.69 and Icarus Verilog 13.0.

```
cd tools/verilog2tapnet
python3 -m unittest discover -s tests -v
python3 validate.py
```

| Check | Result |
|---|---|
| `tests/test_verilog2tapnet.py` | 21 tests, OK (about 10 s), Python 3.9.6 and 3.11.15: every design converts, is valid under `tapnet2.decode`, and matches iverilog beat by beat (two seeds); RAM contents compared on every beat; a memory bit flipped in the tapnet run is caught at the next beat; record kinds of the RAM and ROM-port designs; RAM builds smaller than LATCH builds; the output tail; nine unsupported designs rejected with a clear message. `test_same_structure_same_records` (same NAND records for renamed / operand-swapped designs) needs Yosys >= 0.69 and is skipped under an older one with a message naming both versions (Yosys 0.33's ABC maps `adder8_swapped` to equivalent but not identical records; its beat-by-beat check still runs) |
| `validate.py` | 24 conversions of 20 designs and 5 LATCH builds, each 1,000 beats (exhaustive for small combinational designs) plus the `--best` variant; about 90 s; writes `out/` (not committed: sizes depend on the Yosys version) and prints the tables of `tools/verilog2tapnet/README.md` |

The tests assert behaviour, record kinds and bounds, not exact gate counts, because those depend on the Yosys and
ABC version.

## Continuous integration

`.github/workflows/ci.yml` runs sections 1, 3 (`gen_diff.py`, `game_gen.py`), 4 (`test_cartridge.py`), 4b
(`test_inference.py`, `mlp_report.py`) and the first three scripts of section 5 on Python 3.9 and 3.11, and section
2 with Foundry 1.8.3, and fails if any generated file differs from the committed one. The job `verilog2tapnet`
installs the pinned [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) nightly
`2026-10-07` (linux-x64; Yosys 0.69+ with ABC, Icarus Verilog; only its `bin/` is put on `PATH`, so the tests
run under the job's Python 3.11), prints the tool versions, and runs the unit tests of section 6 (not
`validate.py`) and the `ConverterTests` of section 4b. The Ubuntu 24.04 package (Yosys 0.33) is not used: with
it every design still matched iverilog, but `test_same_structure_same_records` failed (section 6).
