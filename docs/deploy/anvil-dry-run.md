# Anvil dry run of the deployment script (TAP-7007)

Run on 2026-10-21 with Foundry 1.8.3 and `contracts/script/anvil-dry-run.sh`: a fresh local `anvil` (chain id
31337, 30,000,000 block gas limit), anvil's public default account 0 as deployer, `UPLOAD_LARGE=true`,
`forge script … --broadcast --slow`. Gas is the `gasUsed` of the receipts. No public chain was touched. The CI job
`forge` repeats this run.

Without `--slow`, forge sends every transaction at once; anvil packed the first 14 (28.4 million gas) into one
block and the fifteenth stayed pending until another transaction arrived, so `forge script` waited forever. Public
RPCs show the same class of problem as nonce gaps and rate limits, which is why `deploy-testnet.sh` passes `--slow`.

## DeployLargeNetlists.s.sol:DeployLargeNetlists — 15 transactions, 30,097,098 gas

| Gas | Transaction |
|---|---|
| 747,862 | CREATE2 `TapLargeGameLib` (library, through forge's CREATE2 factory `0x4e59b448…956C`) |
| 1,011,764 | CREATE `TapLargeNetlistStoreOwned(deployer)` |
| 3,041,191 | CREATE `TapLargeVerifier(store, 6000, 256, 16)` |
| 5,244,700 | CREATE `TapLargeDisputeGame(Params{store, verifier, 0.01 ether, 86400, 3600, 60, 64, 131008, 0x0})` |
| 44,020 | `addKnown(message)` |
| 72,320 | `beginLarge(message, 336, 0x83c47455…3572)` |
| 159,828 | `putChunk(message, 0, 336 bytes, [])` |
| 44,032 | `addKnown(large)` |
| 72,344 | `beginLarge(large, 89509, 0x64c498f2…ad78)` |
| 3,554,614 | `putChunk(large, 0, 16,384 bytes, 3 proof hashes)` |
| 3,580,771 | `putChunk(large, 1, …)` |
| 3,644,131 | `putChunk(large, 2, …)` |
| 3,622,396 | `putChunk(large, 3, …)` |
| 3,558,829 | `putChunk(large, 4, …)` |
| 1,698,296 | `putChunk(large, 5, 7,589 bytes, 2 proof hashes)` |

| | Transactions | Gas |
|---|---|---|
| Default run (no `UPLOAD_LARGE`): first 7 lines | 7 | 10,321,685 |
| `large` upload: last 8 lines | 8 | 19,775,413 |
| Both | 15 | 30,097,098 |

A full `putChunk` costs about 3.6 million gas here, matching the "about 3.66 million per `putChunk` transaction"
of the TAP's cost section and `measurements/l1fee.md`. `forge script` estimated about 1.3 × these figures before
sending.

## deployments/31337.json written by the run

```json
{
  "chain": "anvil",
  "chainId": 31337,
  "largeNetlists": {
    "ITapLargeNetlistStore": "0x8230af7f",
    "TapLargeDisputeGame": "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
    "TapLargeGameLib": "0xbc51E174D8960d416EbcA6D01DA8cde82134Ba65",
    "TapLargeNetlistStoreOwned": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
    "TapLargeVerifier": "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    "block": 0,
    "bond": 10000000000000000,
    "budgetChunks": 16,
    "budgetExtWords": 256,
    "budgetGates": 6000,
    "challengeWindow": 86400,
    "deployer": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "inputProvider": "0x0000000000000000000000000000000000000000",
    "largeNetlistHash2": "0x6abfe829d81de05e2997125e2aa31e24a8b4f3da306e2fee6621ade5e1a0758f",
    "maxK": 64,
    "maxT": 131008,
    "messageNetlistHash2": "0xf5cd90005f48bc1eac7173c4f05c1af9710a1b6bd689d9377709c522627095b0",
    "moveTime": 3600,
    "perBeatTime": 60,
    "segmentBudgetSelector": "0x09683cea",
    "timestamp": 1792591536,
    "verifyBeatEndSelector": "0x364abc73",
    "verifySegmentSelector": "0xaca8e751"
  }
}
```

The library address recorded is the one forge wrote into the broadcast record (`libraries` of `run-latest.json`)
and the one the script found in the game's runtime code; both agreed. Addresses on anvil are those of a fresh node
and mean nothing elsewhere.
