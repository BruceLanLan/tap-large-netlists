# L1 data fee on Base and X Layer: a snapshot

Read-only snapshot taken on **2026-10-18 (UTC)**, about 18:45–19:00 UTC. No transaction was signed with a real key or broadcast. The only RPC methods used were `eth_call`, `eth_getBlockByNumber`, `eth_blockNumber`, `eth_chainId`, `eth_gasPrice`, `eth_maxPriorityFeePerGas` and `eth_getTransactionReceipt`. Every figure here is a **snapshot of fees that move from block to block**. It is not a price.

## 1. Method

- **Oracle.** The snapshot calls the predeploy `GasPriceOracle` at `0x420000000000000000000000000000000000000F` and the `L1Block` predeploy at `0x4200000000000000000000000000000000000015` with `cast call --block N`, pinning one block per chain:
  - Base (chain 8453): block **52,780,049** (timestamp 2026-10-18 18:50:45 UTC).
  - X Layer (chain 196): block **73,580,619** (timestamp 2026-10-18 18:54:15 UTC).
- **RPCs.**
  - Base: `https://mainnet.base.org` as primary, with `https://base-rpc.publicnode.com` to cross-check.
  - X Layer: `https://rpc.xlayer.tech` as primary, with `https://xlayerrpc.okx.com` to cross-check. Both are run by OKX. Two third-party endpoints were also checked, `https://xlayer.drpc.org` and `https://196.rpc.thirdweb.com`, at the latest block (see the next point).
- **Cross-check results.**
  - Base: at the pinned block, every value that both RPCs answered was identical: `l1BaseFee`, `blobBaseFee`, the scalars, and every `getL1Fee` and `getL1GasUsed` result. Publicnode refused the last few calls with "archive requests require a token" once the pinned block was more than about 200 blocks old. Six cases were therefore re-run on both RPCs at block 52,780,237, and the two RPCs again agreed exactly (see §6).
  - X Layer: the two OKX RPCs agreed on every value they returned. The okx.com endpoint dropped a few connections. The two third-party RPCs also return `baseFeeScalar = blobBaseFeeScalar = 0`.
- **Transactions measured.**
  - **Type.** Each one is an *unsigned* EIP-1559 (type 2) transaction, RLP-encoded by hand: `0x02 ‖ rlp([chainId, nonce 1234, maxPriorityFee, maxFee, gasLimit, to, 0, data, []])`.
    - Base: priority fee 0.001 gwei, max fee 0.012 gwei.
    - X Layer: priority fee 1 wei, max fee 0.04 gwei.
    - The `to` address is a fixed dummy address. The gas limit is close to the measured gas.
  - **Why unsigned.** Since Fjord, `getL1Fee(bytes)` takes the unsigned transaction and adds 68 bytes for the signature itself. The oracle is version 1.6.0, and `isFjord()`, `isEcotone()` and `isIsthmus()` all return true.
  - **Fee formula.** The oracle computes `fee = estimatedSize × (16·baseFeeScalar·l1BaseFee + blobBaseFeeScalar·blobBaseFee) / 10^6`. Here `estimatedSize = max(100, (−42,585,600 + 836,500 · (FastLZ(tx) + 68)) / 10^6)`.
  - **Estimated size.** `getL1GasUsed(bytes)/16` gives `estimatedSize`: the Fjord linear-regression estimate, not the raw FastLZ length.
- **Calldata.** The calldata is real ABI encoding:
  - `putChunk(bytes32, uint32, bytes, bytes32[])` with one 16,384-byte chunk and a **9-hash proof**: 456 chunks give a tree of depth 9.
    - This is **16,868 bytes** of calldata. The spec's 16,772 bytes corresponds to a 6-hash proof, so the difference is 96 bytes.
  - `verifySegment(...)` with the witness lengths below. The calldata is 548 bytes plus the padded witness, which matches the spec's 612 bytes for a 48-byte witness and 75,844 bytes for a 75,280-byte witness.
- **Inputs.**
  - Chunk payloads:
    - 16,384 random bytes, which do not compress;
    - chunks 0–4 of the `large` example in `assets/tap-draft-large-netlists/vectors.json`, which mixes NAND records and about 33 KB of ROM data;
    - chunk 1 of the NAND-only synthetic file `gas_cases.bin` file 0, 280,016 bytes;
    - an all-zero chunk, as a lower bound.
  - Witnesses:
    - Real witnesses of 1,008 bytes (the latch default-budget case) and 75,280 bytes (the wide-scatter case), taken from `contracts/test/data/gas_cases.bin`.
    - **Synthetic** witnesses of 1,212 bytes (the real 1,152-byte witness of the 7,000-LATCH case of the former budget, plus 60 random bytes) and 84,208 bytes (the real 75,280-byte witness plus 8,928 random bytes, standing in for hashes).
  - A dispute round message is modelled as 200 bytes of calldata (a selector and 196 random bytes).
- **Prices.** Taken at about 18:46 UTC:
  - ETH ≈ **$2,363**: CoinGecko 2,361.98, OKX ETH-USDT 2,365.38, Binance 2,365.33.
  - OKB ≈ **$111.9**: CoinGecko 111.91, OKX OKB-USDT 111.92.

- **To repeat.** Build each transaction as above:
  - gas limits: 3,700,000 for `putChunk`; 4,400,000, 5,200,000, 5,200,000, 10,600,000 and 11,000,000 for the `verifySegment` cases from the 48-byte witness up; 200,000 for the round message;
  - `to` = `0x3f9a1c5e7b2d84a6c0e1f2938475a6b7c8d9e0f1`;
  - random bytes from Python `random.Random(20261018)`.

  Then run, for example:

  ```
  cast call 0x420000000000000000000000000000000000000F "getL1Fee(bytes)(uint256)" 0x02… --block <N> --gas-limit 500000000 --rpc-url https://mainnet.base.org
  cast call 0x420000000000000000000000000000000000000F "getL1GasUsed(bytes)(uint256)" 0x02… --block <N> --gas-limit 500000000 --rpc-url https://mainnet.base.org
  ```

  Public RPCs serve such calls only for recent blocks (publicnode: about the last 200), so all calls of one snapshot have to be made within minutes.

## 2. What each chain charges for data

| | Base | X Layer |
|---|---|---|
| OP Stack upgrades active | Fjord, Isthmus, Jovian (`L1Block` 1.7.0, `daFootprintGasScalar` exists) | Fjord, Isthmus, Jovian (`L1Block` 1.8.0+custom-gas-token; gas token OKB) |
| `GasPriceOracle` methods | `getL1Fee`, `getL1GasUsed`, `getL1FeeUpperBound`, `l1BaseFee`, `blobBaseFee`, `baseFeeScalar`, `blobBaseFeeScalar`, `decimals` (6) all answer; `overhead()` and `scalar()` revert as deprecated | the same |
| `baseFeeScalar` / `blobBaseFeeScalar` | 2,269 / 1,055,762 | **0 / 0** |
| `operatorFeeScalar` / `operatorFeeConstant` | 0 / 0 | 0 / 0 |
| `l1BaseFee` / `blobBaseFee` at the pinned block (Ethereum values relayed by `L1Block`) | 66,373,336 wei (0.066 gwei) / 3,949,098 wei (0.0039 gwei) | 79,591,410 / 4,672,752 (scalars 0, so no effect) |
| L2 base fee (block header) | 5,000,000 wei = 0.005 gwei, **equal to the Jovian minimum base fee** in `extraData` (`0x…004c4b40`) | 20,000,000 wei = 0.02 gwei OKB, **equal to the minimum base fee** in `extraData` (`0x…01312d00`) |
| `eth_gasPrice` / `eth_maxPriorityFeePerGas` | 0.006 gwei / 0.001 gwei | 0.020000001 gwei / 1 wei |
| EIP-1559 parameters in `extraData` | denominator 100, elasticity 5 | denominator 50, **elasticity 1** |
| `daFootprintGasScalar` (Jovian) | 148 | 400 |
| Block gas limit | 400,000,000 | 210,000,000 |

**X Layer.** X Layer is an OP Stack chain: it has the predeploys, Fjord/Isthmus/Jovian, and a custom gas token. Its `GasPriceOracle` exists and answers, but both L1 fee scalars are 0, so `getL1Fee` returns **0 for every transaction**. This is a consequence of the configuration, not something the measurement found in a payload.

A real receipt confirms that users are actually charged 0. Transaction `0x3749738b…6fec8a` in block 73,580,842 has `l1Fee = 0x0`, `l1BaseFeeScalar = 0` and `l1BlobBaseFeeScalar = 0`, while `l1GasUsed` (0xca8) and `l1GasPrice` are reported.

X Layer charges no itemised fee for data. A user pays `gasUsed × (0.02 gwei + priority)` in OKB, and data costs are presumably covered by that minimum base fee. OKX's Jovian announcement (2026-05-27) says only that the minimum base fee is set to 0.02 gwei and can be configured through SystemConfig.

This snapshot did **not** establish where X Layer publishes its batches (Ethereum blobs, calldata or another DA). `L1Block` relays Ethereum block numbers and fees, but that alone does not show where batches land. The scalars can be set by the operator through `SystemConfig`, so the "0" is the chain's current policy, not a protocol property.

**Base.** Base charges an itemised L1 fee. A real receipt matches the oracle's scale: transaction `0xe2a3dc79…34828a` in block 52,780,265 has `l1Fee` = 1,379,619,489 wei, with the same scalars as above.

**Jovian DA footprint.** This does not change the price, but it matters for throughput. Since Jovian, every transaction's estimated compressed size × `daFootprintGasScalar` counts against the block gas limit, recorded as the header's `blobGasUsed`. The base fee then follows `max(gasUsed, daFootprint)`.

For an incompressible `putChunk` (about 14,494 estimated bytes) the footprint is:

- about **2.15 million gas on Base**, which is less than its about 3.66 million execution gas;
- about **5.80 million gas on X Layer**, which is more than its execution gas.

The DA cap per block is the gas limit divided by the scalar:

- Base: about 2.70 MB, or about 186 such transactions; the execution gas limit allows fewer, about 109.
- X Layer: about 525 KB, or **about 36 incompressible chunk transactions per block**.

Because X Layer's elasticity is 1, the gas target equals the limit, so a full block does not raise its base fee above the minimum. Base's target is 80 million gas.

## 3. Measured L1 data fee per transaction

Base, block 52,780,049, `getL1Fee(unsigned tx)`. The USD column uses ETH at $2,363. The X Layer `getL1Fee` result is 0 for every row.

| Transaction | Calldata (bytes) | Nonzero bytes | Unsigned tx (bytes) | Estimated size (`getL1GasUsed/16`) | L1 fee (wei) | ETH | USD |
|---|---|---|---|---|---|---|---|
| `putChunk`, 16,384 random bytes | 16,868 | 16,642 | 16,916 | 14,494 | 95,361,177,907 | 9.54 × 10⁻⁸ | 0.000225 |
| `putChunk`, `large` chunk 0 (NAND records) | 16,868 | 9,163 | 16,916 | 10,888 | 71,636,576,955 | 7.16 × 10⁻⁸ | 0.000169 |
| `putChunk`, `large` chunk 1 | 16,868 | 11,374 | 16,916 | 12,214 | 80,359,261,369 | 8.04 × 10⁻⁸ | 0.000190 |
| `putChunk`, `large` chunk 2 (ROM data) | 16,868 | 16,654 | 16,916 | 14,494 | 95,361,177,907 | 9.54 × 10⁻⁸ | 0.000225 |
| `putChunk`, `large` chunk 3 | 16,868 | 14,850 | 16,916 | 13,844 | 91,079,633,129 | 9.11 × 10⁻⁸ | 0.000215 |
| `putChunk`, `large` chunk 4 | 16,868 | 9,628 | 16,916 | 11,636 | 76,556,501,161 | 7.66 × 10⁻⁸ | 0.000181 |
| `putChunk`, NAND-only file, chunk 1 | 16,868 | 9,679 | 16,916 | 10,442 | 68,697,830,282 | 6.87 × 10⁻⁸ | 0.000162 |
| `putChunk`, all-zero chunk (lower bound) | 16,868 | 331 | 16,916 | 521 | 3,429,037,125 | 3.4 × 10⁻⁹ | 0.000008 |
| `verifySegment`, 48-byte witness | 612 | 264 | 660 | 343 | 2,256,840,419 | 2.3 × 10⁻⁹ | 0.000005 |
| `verifySegment`, 1,008-byte witness (real) | 1,572 | 1,109 | 1,620 | 1,144 | 7,528,973,964 | 7.5 × 10⁻⁹ | 0.000018 |
| `verifySegment`, 1,212-byte witness (synthetic) | 1,764 | 1,298 | 1,812 | 1,310 | 8,624,124,878 | 8.6 × 10⁻⁹ | 0.000020 |
| `verifySegment`, 75,280-byte witness (real) | 75,844 | 74,665 | 75,894 | 58,651 | 385,862,340,162 | 3.86 × 10⁻⁷ | 0.000912 |
| `verifySegment`, 84,208-byte witness (synthetic, §6.3 bound) | 84,772 | 83,559 | 84,822 | 66,353 | 436,536,458,828 | 4.37 × 10⁻⁷ | 0.001032 |
| dispute round message, 200 bytes of calldata | 200 | 199 | 246 | 226 | 1,491,885,760 | 1.5 × 10⁻⁹ | 0.000004 |

**What the table shows:**

- **NAND-heavy chunks compress.** FastLZ brings them to about 64–75% of the incompressible size (10,442–12,214 estimated bytes against 14,494).
- **ROM data does not compress.** The ROM data of the example behaves like random bytes. The weights of a real design may compress somewhat, but this was not measured.
- **Witnesses barely compress.** They are mostly hashes, so they compress about as badly as random data.
- **Oracle quirk.** For the two incompressible chunks, `getL1Fee` (95.36 × 10⁹ wei) was about 1.8% **above** `getL1FeeUpperBound(16,916)` (93.64 × 10⁹ wei). The upper bound assumes a FastLZ expansion of `size/255 + 16`. Estimates that use the upper bound for random payloads can therefore be slightly low.

## 4. Totals (L2 execution + L1 data)

These are snapshot prices:

- **Base:** L2 gas price 0.006 gwei (base fee 0.005 at its minimum, plus a 0.001 priority fee); ETH at $2,363.
- **X Layer:** 0.02 gwei OKB (the minimum, plus 1 wei); OKB at $111.9.

L2 gas figures are those measured in the spec's Reference Implementation (Foundry): about 3.66 million per `putChunk` transaction, and 1.67 × 10⁹ for the whole 7,466,723-byte upload (456 chunks) including calldata gas.

| Item | Base L2 execution | Base L1 data | X Layer (L2 only; L1 = 0) |
|---|---|---|---|
| One `putChunk` transaction | 2.20 × 10⁻⁵ ETH ≈ $0.052 | 6.9–9.5 × 10⁻⁸ ETH ≈ $0.00016–0.00023 | 7.32 × 10⁻⁵ OKB ≈ $0.0082 |
| Upload of the INT-C16-scale file (456 chunks, 1.67 × 10⁹ gas) | 0.0100 ETH ≈ **$23.7**; $19.7 at the minimum base fee without priority | 3.1–4.3 × 10⁻⁵ ETH ≈ **$0.07–0.10** (NAND-like to incompressible) | 0.0334 OKB ≈ **$3.74** |
| Worst dispute step: `verifySegment` measured at 10,524,815 gas tx estimate, 75,280-byte witness | 6.31 × 10⁻⁵ ETH ≈ $0.149 | 3.86 × 10⁻⁷ ETH ≈ $0.0009 | 2.10 × 10⁻⁴ OKB ≈ $0.024 |
| Worst step at the spec's bound: "below about 11 million gas", 84,208-byte witness | ≤ 6.6 × 10⁻⁵ ETH ≈ $0.156 | 4.37 × 10⁻⁷ ETH ≈ $0.0010 | ≤ 2.2 × 10⁻⁴ OKB ≈ $0.025 |
| Whole dispute, **L1 data only**: about 35 round messages of about 200 bytes plus one worst final step | — | 35 × 1.49 × 10⁹ + 4.37 × 10¹¹ wei ≈ 4.9 × 10⁻⁷ ETH ≈ $0.0012 | 0 |

**Execution gas of a dispute round.** When this snapshot was taken no game contract existed, so the L2 execution gas of a round was **not measured** here. (The reference game was written later; Appendix B of the TAP gives its measured gas: every move other than the final call costs at most about 409,000 gas, and a whole dispute that reaches the verifier 1.4 to 5.9 million gas for all parties together.) Purely as an assumption, at 100,000–300,000 gas per move, 35 moves would cost 0.21–0.63 × 10⁻⁴ ETH (about $0.05–0.15) on Base, or about $0.008–0.023 on X Layer. Add the final step from the table above.

**Ratios.** On Base, the L1 data fee was about **0.3–0.4%** of the L2 execution cost of an upload. For a worst dispute step it was about 0.6–0.7%. The execution cost of `putChunk`, mostly the SSTORE2 code deposit, dominates.

## 5. Volatility

- **Movement within minutes.** Across the reads in about 10 minutes, the Ethereum values relayed on Base ranged over:
  - `l1BaseFee` 66–97 × 10⁶ wei;
  - `blobBaseFee` 3.9–5.6 × 10⁶ wei.

  At Base block 52,780,237, a few minutes after the pinned block, the same transactions cost 13.6% more (§6).
- **Current levels.** Both Ethereum fees are very low: `l1BaseFee` about 0.07 gwei and `blobBaseFee` about 0.004 gwei. Both L2 base fees sit at their configured minimum.
- **Split of the Base fee.** At the pinned block, `16·2,269·l1BaseFee` = 2.41 × 10¹² and `1,055,762·blobBaseFee` = 4.17 × 10¹². The blob term was **63%** of the L1 fee.
- **10× blob base fee** (with `l1BaseFee` and the scalars unchanged): the L1 fee grows by a factor of **6.7**:
  - about $0.0015 per incompressible chunk;
  - about **$0.69** for the 456-chunk upload;
  - about $0.007 for the 84,208-byte step.

  At 10× both Ethereum fees, the L1 fee grows by a factor of 10.
- **Break-even.** The L1 data fee of an incompressible chunk would equal its L2 execution cost (at 0.006 gwei) only if the blob base fee rose about **360×**, to about 1.4 gwei, or `l1BaseFee` alone rose about 630×, to about 42 gwei. Blob fees have historically left their floor by more than one order of magnitude, so 10× does not bound the range.
- **Other levers.**
  - L2 congestion would raise the L2 base fee above its minimum, and that multiplies the dominant L2 term directly.
  - The operator can change the scalars at any time.
  - On X Layer a 10× L1 fee changes nothing while the scalars are 0. Its lever is the operator's minimum base fee and scalars.

## 6. Raw data

Base cross-check at block 52,780,237: both `mainnet.base.org` and `base-rpc.publicnode.com` returned `l1BaseFee` 67,984,732 and `blobBaseFee` 4,743,165. The `getL1Fee` results, in wei, were:

| Transaction | L1 fee (wei) |
|---|---|
| random `putChunk` | 108,360,912,277 |
| `large` chunk 0 | 81,402,149,193 |
| 1,212-byte witness | 9,799,774,498 |
| 75,280-byte witness | 438,463,493,328 |
| 84,208-byte witness | 496,045,560,244 |
| 200-byte round | 1,695,261,170 |

X Layer `getL1GasUsed` (estimated L1 gas, before the zero scalars apply) differs from Base's only by the 3-byte difference in transaction encoding: for example, 231,879 for the random chunk against 231,919 on Base.
