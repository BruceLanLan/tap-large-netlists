# Testnet deployment of the TAP-7007 reference contracts / TAP-7007 参考合约的测试网部署

One command deploys the reference implementation of "Large Netlists and Verifiable Execution" (tapnet/2 store,
one-step verifier, external game library, informative dispute game) to a public **testnet** with the author's own
test key, and `docs/deploy/VERIFY.md` tells editors how to check every address read-only. The key never leaves the
author's shell: the script reads `PRIVATE_KEY` from the environment at run time and never prints it. The TAP-721
family (collection, companion, soulbound credential) has the same package in its own repository
(`tap-netlist-nft/DEPLOY.md`).

一条命令把《大型网表与可验证执行》的参考实现（tapnet/2 存储、单步验证器、外部游戏库、参考性争议游戏）用作者自己的
测试密钥部署到公开**测试网**；`docs/deploy/VERIFY.md` 告诉编辑如何只读核验每个地址。密钥不离开作者的 shell：
脚本运行时从环境变量 `PRIVATE_KEY` 读取，从不打印。TAP-721 家族（集合、伴生、灵魂绑定凭证）在它自己的仓库里有
同样的一套（`tap-netlist-nft/DEPLOY.md`）。

## 1. Safety note / 安全提示

- **Use a throw-away test key.** A fresh account, funded only from the faucets below; never a key that holds
  mainnet assets. The script refuses mainnet chain ids (1, 56, 196, 8453) unless `ALLOW_MAINNET=true`, an
  override this package does not use.
- Keep the key in one shell's environment (`export PRIVATE_KEY=0x…`) or in `contracts/.env` (git-ignored); never in
  a committed file, a chat, or a shared shell history.
- Transactions are sent only with `--broadcast`; without it `forge script` simulates and prints.
- **只用一次性测试密钥**：新账户，只从水龙头领测试币，绝不复用持有主网资产的密钥。脚本遇到主网链 id 会拒绝。
- 密钥只放在一个 shell 的环境变量或已被 git 忽略的 `contracts/.env` 里；不进提交文件、聊天或共享 shell 历史。
- 只有带 `--broadcast` 才发交易。

## 2. Chains / 链

| | X Layer Testnet (recommended) | BNB Smart Chain Testnet (second option) |
|---|---|---|
| chainId | **1952** (0x7a0) | **97** |
| Public RPC (`RPC_URL`) | `https://testrpc.xlayer.tech` or `https://xlayertestrpc.okx.com` | `https://bsc-testnet-rpc.publicnode.com` or `https://data-seed-prebsc-1-s1.bnbchain.org:8545` |
| Gas token | test OKB | tBNB |
| Faucet | https://www.okx.com/xlayer/faucet | https://www.bnbchain.org/en/testnet-faucet (also https://testnet.bnbchain.org/faucet-smart) |
| Explorer | https://www.oklink.com/xlayer-test (OKLink; also https://web3.okx.com/explorer/x-layer-testnet) | https://testnet.bscscan.com |
| `eth_gasPrice` seen on 2026-10-21 | 0.020000001 gwei (the 0.02 gwei floor of X Layer mainnet, `measurements/l1fee.md`) | 0.1 gwei |
| CREATE2 factory `0x4e59b44847b379578588920cA78FbF26c0B4956C` (forge deploys the linked library through it) | present | present |

Sources: the EIP-155 chain registry `ethereum-lists/chains` (`_data/chains/eip155-1952.json`, `eip155-97.json`;
https://chainlist.org/chain/1952 serves the same data), https://web3.okx.com/xlayer/docs and
https://docs.bnbchain.org; chain ids, gas prices and the factory's code were read on 2026-10-21 with `cast chain-id`,
`cast gas-price` and `cast code` (read-only). The mainnet figures of the TAP (X Layer 196, Base 8453) are in
`measurements/l1fee.md`.

X Layer Testnet first: the TAP's cost section is written for X Layer (no itemised L1 data fee, 0.02 gwei floor,
210 million block gas limit, 16,777,216-gas transaction limit), and the testnet runs the same OP-Stack
configuration, so a `putChunk` of 16,384 bytes behaves there as it would on mainnet. BNB Smart Chain Testnet is
the fallback (same `shanghai` EVM, different gas token and fee market).

首选 X Layer 测试网：TAP 的成本章节按 X Layer 写（无单列 L1 数据费、0.02 gwei 底价、2.1 亿区块 gas 上限、
16,777,216 交易 gas 上限），测试网与主网配置相同，16,384 字节的 `putChunk` 行为一致。BSC 测试网是备选。

## 3. Prerequisites / 前提

- Foundry **v1.8.3** (`foundryup -v v1.8.3`); the CI pins the same.
- forge-std v1.17.0 at `contracts/lib/forge-std` (not committed; `contracts/lib/README.md` gives three ways).
- Python 3.9+ for the dry run's gas summary only.
- A funded test account. The default run (store, verifier, library, game, the 336-byte `message` example) uses
  about 10.3 million gas; with `UPLOAD_LARGE=true` (the 89,509-byte `large` example, 6 chunks) about 30.1 million
  (§6). At 0.02 gwei that is 0.0002–0.0006 OKB; at 0.1 gwei 0.001–0.003 tBNB. One faucet claim is enough.
  `cast wallet address --private-key "$PRIVATE_KEY"` prints the deployer address (and nothing else).

## 4. What gets deployed / 部署什么

`contracts/script/DeployLargeNetlists.s.sol:DeployLargeNetlists`, in one broadcast:

| Contract | Constructor arguments (defaults; env names in the script header) | Why |
|---|---|---|
| `TapLargeGameLib` (external library) | — (forge deploys it through CREATE2 and links the game) | keeps the game under the 24,576-byte code limit (TAP §Reference Implementation) |
| `TapLargeNetlistStoreOwned` | `(deployer)` — the owner may `addKnown` hashes | the chunk oracle of §3; an application would answer `knownNetlist` from its own commit |
| `TapLargeVerifier` | `(store, 6000, 256, 16)` — the RECOMMENDED budget of §5.1 (`BUDGET_GATES`, `BUDGET_EXT_WORDS`, `BUDGET_CHUNKS`) | the one-step verifiers of §6 |
| `TapLargeDisputeGame` | `Params{store, verifier, bond 0.01 ether (BOND), challengeWindow 1 day, moveTime 1 hour, perBeatTime 60 s, maxK 64, maxT 64 × 2047 = 131,008, inputProvider 0x0 (case (a))}` | the informative game of §7, with the test suite's time parameters and a small bond for a testnet |
| upload of `message` (`assets/tap-draft-large-netlists/vectors.json` `files.message`, 336 bytes, 1 chunk) | `addKnown`, `beginLarge(h, 336, chunkRoot)`, `putChunk(h, 0, chunk, [])` | so that `largeStored`, `largeInfo`, `largeChunk` and `largeChunkPointer` have something to show |
| upload of `large` (89,509 bytes, 6 chunks, proofs from the vectors) — only with `UPLOAD_LARGE=true` | 6 × `putChunk` of about 3.6 million gas each | the chunk-tree case; what the fee snapshot measured |

Values an editor compares are written to `contracts/deployments/<chainId>.json` (section `largeNetlists`):
addresses, the budget, the game parameters, the two `netlistHash2` values, the `ITapLargeNetlistStore` id
`0x8230af7f` and the verifier selectors. `foundry.toml` grants read-write access to `./deployments` for this.

## 5. Commands / 命令

Names only; never write values into this file.

```bash
export PRIVATE_KEY=…            # the test key    测试密钥
export RPC_URL=…                # §2
# optional: UPLOAD_LARGE=true BOND=… CHALLENGE_WINDOW=… MOVE_TIME=… PER_BEAT_TIME=… MAX_K=… INPUT_PROVIDER=…

contracts/script/deploy-testnet.sh          # one command / 一条命令
```

`FAST_PARAMS=true contracts/script/deploy-testnet.sh` deploys a second set with a 10-minute challenge window
(other parameters unchanged) into its own section `largeNetlistsFast` of `deployments/<chainId>.json`, for the
on-chain inference demo of `demo/inference/onchain/README.md`, whose honest claim can then be accepted in the same
sitting; the default deployment's record is not touched.

`FAST_PARAMS=true` 另部署一套挑战窗口为 10 分钟的合约（其余参数不变），写入 `deployments/<chainId>.json` 的独立小节
`largeNetlistsFast`，供 `demo/inference/onchain/README.md` 的链上推理演示使用；默认部署的记录不受影响。

which runs

```bash
cd contracts
forge script script/DeployLargeNetlists.s.sol:DeployLargeNetlists --rpc-url "$RPC_URL" --broadcast --slow
```

`--slow` waits for each receipt before sending the next transaction (the `putChunk` calls are big; see
`docs/deploy/anvil-dry-run.md` for why this matters even locally). Leave out `--broadcast` to simulate. Rerun an
interrupted broadcast with `--resume`. Running the finished script again deploys a second set of contracts (except
the library, which forge re-uses at its CREATE2 address).

Explorer source verification is optional (`docs/deploy/VERIFY.md` §4; needs the explorer's API key).

## 6. Expected gas (anvil dry run) / 预计 gas（anvil 预演）

`contracts/script/anvil-dry-run.sh`, Foundry 1.8.3, receipts of a local anvil node (`docs/deploy/anvil-dry-run.md`
has every transaction):

| | Transactions | Gas used | at 0.02 gwei (X Layer Testnet) | at 0.1 gwei (BSC Testnet) |
|---|---|---|---|---|
| Default run (library, store, verifier, game, `message` upload) | 7 | 10,321,685 | 0.00021 OKB | 0.0010 tBNB |
| `UPLOAD_LARGE=true` adds (`addKnown`, `beginLarge`, 6 × `putChunk`) | 8 | 19,775,413 | 0.00040 OKB | 0.0020 tBNB |
| **Both** | **15** | **30,097,098** | **0.00060 OKB** | **0.0030 tBNB** |

The largest single transactions: `TapLargeDisputeGame` creation 5,244,700 gas, a full `putChunk` about
3.6 million (the TAP's 3.66 million figure). `forge script` estimates about 1.3 × the receipts; fund for the
estimate.

## 7. What to send back / 发回什么

1. `contracts/deployments/<chainId>.json` (commit on a branch or attach).
2. `contracts/broadcast/DeployLargeNetlists.s.sol/<chainId>/run-latest.json` (git-ignored; attach): transaction hashes.
3. Chain, RPC used, date, and whether `UPLOAD_LARGE` was set. No key, no faucet account.

发回 `deployments/<chainId>.json`、`broadcast/…/run-latest.json`、所用链与 RPC、日期、是否设置了 `UPLOAD_LARGE`。
不要发任何密钥。

## 8. After the deployment: the inference dispute on chain / 部署之后：链上推理争议

`demo/inference/onchain/README.md`: one command (`contracts/script/run-inference-dispute.sh`, two test keys) uploads
the inference demo's netlist, publishes a claim with a class lie, plays the whole dispute against the deployed game
and ends in the verifier call that rejects it (17 transactions, about 2.9 million gas); the honest claim is
accepted after the window. The transaction hashes come back in `demo/inference/onchain/evidence-<chainId>.json`.

部署完成后，`demo/inference/onchain/README.md` 的一条命令用两把测试密钥在已部署合约上重放推理演示的争议（上传网表、发布带
类别谎言的申领、整场二分、以拒绝判决的验证器调用收尾，17 笔交易约 290 万 gas），诚实申领在窗口过后被接受；交易哈希写入
`demo/inference/onchain/evidence-<chainId>.json`。

## 9. Dry run without a chain / 不上链的预演

```bash
cd contracts && ./script/anvil-dry-run.sh
```

Starts a local `anvil`, runs the script with anvil's public default account 0 (a published constant, not a secret)
and `UPLOAD_LARGE=true`, prints the gas per transaction and `deployments/31337.json`, and stops anvil. The CI job
`forge` runs the same, followed by `contracts/script/anvil-inference-e2e.sh` (the on-chain inference runner against
anvil: deploy, the class lie rejected, the honest claim accepted after the warped window).
