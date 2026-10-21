# The inference dispute on a public testnet / 推理争议上测试网

[中文](#中文) · [English](#english)

> One command replays the inference demo's dispute (`demo/inference/mlp_dispute.py`, scenario B: a class lie at
> beat 90, honest challenger) and the honest claim (scenario A) against the **deployed** TAP-7007 reference
> contracts of a testnet, and collects the transaction hashes as evidence for the TAP's Deployments section and the
> article. Testnet only; test keys only; nothing here is production code.

---

## English

### What is sent

The transcript `transcript.json` (written by `gen_transcript.py`) holds every transaction of the two scenarios
exactly as `contracts/test/data/game_gen.py` generates them for the Foundry end-to-end test
(`contracts/test/game/E2E.t.sol`, scenarios `inference/A_honest_unchallenged` and
`inference/B_class_gate_as_claimant`): `gen_transcript.py --check` asserts that each move's calldata equals the
record in `game_moves.bin` byte for byte. Only the claim id and the challenger's address exist at run time; the
script patches those two words into the calldata. The game decides the input case (§7.1): with
`inputProvider = 0x0` (the default deployment, case (a)) the claim carries the 92 input bytes and the game computes
`inputRoot` itself; with a provider (case (b)) the provider's `publishInputs` goes first.

| Step | Who | Transactions | Gas (anvil receipts, Foundry 1.8.3) | Notes |
|---|---|---|---|---|
| 0. Upload of `build/mlp_digits.tapnet2` (88,854 bytes, 6 chunks), only if `largeStored` is false | store owner (`addKnown`), claimant (`beginLarge`, 6 × `putChunk`) | 8 | **19,472,414** (`addKnown` 44,032; `beginLarge` 72,344; `putChunk` 3,566,338 / 3,558,223 / 3,558,679 / 3,558,220 / 3,557,497 / 1,557,081) | once per store; the same hash `0x561f036b…300e` serves both scenarios |
| B. Class lie, honest challenger | claimant: `claim`, `commitBeats`, 2 × `revealBeat`, `commitTuples`, 3 × `revealSeg`, `proveSegment` (9 txs); challenger: `challenge`, `commitBeats`, 2 × `revealBeat`, `commitTuples`, 3 × `revealSeg` (8 txs) | **17** | **2,934,657**: `claim` 560,329 · `challenge` 143,540 · `commitBeats` 72,002 / 90,024 · `revealBeat` 68,985 / 55,027 / 51,798 / 60,078 · `commitTuples` 369,622 / 124,979 · `revealSeg` 54,332 / 65,955 / 54,459 / 58,451 / 54,471 / 58,024 · `proveSegment` 992,581 | 9 rounds; the final call `verify_segment(seg_2 → seg_3)` at beat 90 (witness 776 bytes) returns false; claim **Rejected**, `outcomeOf` = challenger, `VerifierFalse`; the challenger's credit becomes 2 bonds |
| A. Honest claim, nobody challenges | claimant: `claim`; anyone: `accept` after the window | **2** | **597,963** (`claim` 543,229; `accept` 54,734) | claim **Accepted**; the claimant's bond is credited back |

Totals: 27 transactions and 23,005,034 gas for upload + B + A; 19 transactions and 22,407,071 gas for upload + B
alone. On a case-(b) deployment (`INPUT_PROVIDER` set) the provider's `publishInputs` (35,984 gas) precedes each
claim and the claim is cheaper (about 235k, no inputs on chain): B is then 18 transactions and 2,682,565 gas, A 3
transactions and 325,971 gas (anvil, `FAST_PARAMS=true INPUT_PROVIDER=…`; the local test runs both deployments). At BSC Testnet's 0.1 gwei that is about 0.0023 tBNB for everything, plus two bonds of `game.bond()`
(0.01 tBNB each by default; the claimant's bond is lost in B and returned in A, the challenger's is returned
with the claimant's). `claim` costs more here than in `inference_gas.csv` (560k against 235k) because the case-(a)
claim carries the inputs and the game hashes the 92 input leaves; everything else matches the test's figures.
`forge script` estimates about 1.3 × the receipts; fund for the estimate.

### Time rules and waiting

Every move of the game has `moveTime` (1 hour) **at most** and no minimum, so scenario B is one broadcast:
`forge script --slow` sends the 17 transactions one after another, each after the previous receipt, in a minute or
two on BSC Testnet (3-second blocks). The only mandatory wait is `accept`: it reverts `TooEarly` until
`block.timestamp > windowEnd` = claim time + `challengeWindow`. With the deploy script's default parameters
(challengeWindow 1 day, moveTime 1 hour, perBeatTime 60 s):

| | Default deployment (`largeNetlists`) | `FAST_PARAMS=true` deployment (`largeNetlistsFast`) |
|---|---|---|
| Scenario B (upload + claim + 16 dispute moves) | one sitting, no waiting | one sitting, no waiting |
| Scenario A, step 1 (upload + claim) | one sitting | one sitting |
| Scenario A, step 2 (`accept`) | **after 24 hours** (`STEP=accept`) | **after 10 minutes** (`STEP=accept`) |

Nothing takes days. `FAST_PARAMS` (added to `DeployLargeNetlists.s.sol`) shortens **only** the challenge window to
10 minutes; `moveTime` and `perBeatTime` keep their defaults, because the game has no minimum time between moves and
a shorter `moveTime` would only make a stalled RPC fatal (a move sent after its deadline reverts `TooLate`, and the
dispute then ends by `timeout` against the party that was late). The fast variant writes its own section
`largeNetlistsFast` to `deployments/<chainId>.json`, so it never overwrites the default deployment's record; the
runner reads it with `DEPLOYMENT_SECTION=largeNetlistsFast`. The deadlines also mean: do not start scenario B when
you cannot finish it within an hour (RPC trouble: `--resume` resends the unsent transactions of the same
broadcast; a party that misses a deadline loses by `timeout`).

### Keys

Two **different** test keys: `challenge` reverts `NotYourTurn` from the claimant's own address, so one key cannot
play both roles. `CLAIMANT_KEY` is naturally the deploy key (the store owner, which `addKnown` needs for the upload;
otherwise set `OWNER_KEY`), `CHALLENGER_KEY` a second fresh account funded from the faucet with one bond plus gas.
A case-(b) deployment (`INPUT_PROVIDER` set) also needs `PROVIDER_KEY` (the provider cannot be the claimant). Keys
stay in the shell's environment; the scripts never print them.

### Commands (BSC Testnet, chainId 97)

```bash
export RPC_URL=https://bsc-testnet-rpc.publicnode.com
export CLAIMANT_KEY=…        # the deploy key (store owner); funded: about 0.03 tBNB covers everything
export CHALLENGER_KEY=…      # a different test key; funded: bond 0.01 tBNB + gas

# the deployment of DEPLOY.md must exist: contracts/deployments/97.json (section largeNetlists)
contracts/script/run-inference-dispute.sh                      # scenario B: upload if needed, claim, dispute; Rejected
SCENARIO=A contracts/script/run-inference-dispute.sh           # scenario A: claim; prints the window end
# … 24 hours later (10 minutes with DEPLOYMENT_SECTION=largeNetlistsFast after a FAST_PARAMS=true deployment):
SCENARIO=A STEP=accept contracts/script/run-inference-dispute.sh
```

The wrapper runs
`forge script script/RunInferenceDispute.s.sol:RunInferenceDispute --rpc-url "$RPC_URL" --broadcast --slow -vv`
and then `collect_evidence.py`, which merges every broadcast record of the chain, maps each transaction to its
scenario (by claim id), role and function, reads `claimOf`/`outcomeOf` back from the chain (read-only) and writes
`demo/inference/onchain/evidence-97.json`. `DRY=1` simulates without sending. Other knobs: `STEP=upload|claim|
dispute|accept`, `CLAIM_ID` (name an existing claim when the state file is lost), `START_MOVE` (resume a dispute
from a transcript index; normally `FORGE_SCRIPT_FLAGS=--resume` is the right tool), `FORGE_SCRIPT_FLAGS=--legacy`
for an RPC without EIP-1559. The state file is written only by a real broadcast (never by `DRY=1`), but a
broadcast interrupted after the claim leaves it ahead of the chain: repair with `--resume`, which sends the
transactions the record shows as unsent.

X Layer Testnet (1952) works the same with `RPC_URL=https://testrpc.xlayer.tech`.

### What to send back

1. `demo/inference/onchain/evidence-<chainId>.json` — every transaction hash with block, sender role, function and
   gas, per scenario, plus the final status read from the chain (commit it next to `deployments/<chainId>.json`).
2. `contracts/deployments/<chainId>.inference.json` — the state file (claim ids, parties, window ends).
3. `contracts/broadcast/RunInferenceDispute.s.sol/<chainId>/run-*.json` (git-ignored; attach).
4. The paragraph `collect_evidence.py` prints (below), with `<COMMIT>` filled in. No key, no faucet account.

### Evidence paragraph (template; `collect_evidence.py` prints it filled in)

```markdown
**On-chain run of the inference dispute (BNB Smart Chain Testnet, chainId 97, <DATE>).** Against the deployed game
`<GAME>` (commit `<COMMIT>`), a claimant published claim <ID> about the classifier netlist
`0x561f036b1c051727b724839a77498a5592b0f324436fd971153644c426df300e` (T = 92, K = 8, 13 checkpoint leaves;
checkpointRoot `0xcc0a5bfb…7e9e`) with a class lie (a gate feeding the class register flipped at beat 90: class 0
instead of 1) in tx `<CLAIM TX>`; an honest challenger challenged it at checkpoint index 3 in tx `<CHALLENGE TX>`;
after 9 rounds (17 transactions in all, about 2.93 million gas) the claimant's own final call `proveSegment`
(tx `<PROVE TX>`, `verify_segment(seg_2 → seg_3)`, witness 776 bytes) made `TapLargeVerifier.verifySegment` return
false, and the game rejected the claim (`claimOf(<ID>).status = Rejected`, `outcomeOf` = challenger,
`VerifierFalse`). The honest claim <ID A> of scenario A was accepted after its challenge window
(`accept` tx `<ACCEPT TX>`). Transactions: https://testnet.bscscan.com/tx/<hash>; the full list is in
`demo/inference/onchain/evidence-97.json`.
```

### Files

| File | Role |
|---|---|
| `gen_transcript.py` | writes `transcript.json` from `game_gen.py`'s mirror (standard library; `--check` compares with `game_moves.bin`) |
| `transcript.json` | the transactions: calldata, decoded arguments, sender role, expected index at each reveal, expected outcome |
| `contracts/script/RunInferenceDispute.s.sol` | the forge script: deployment file → upload if absent → claim → dispute or accept; asserts every expectation against the chain |
| `contracts/script/run-inference-dispute.sh` | the one command (forge script + evidence collection) |
| `contracts/script/anvil-inference-e2e.sh` | the local end-to-end test: deploys to anvil twice (default, case (a); `FAST_PARAMS` with an input provider, case (b)), runs B and A on both, warps the clock past the windows, checks that a dry run writes no state, and asserts Rejected / Accepted and the transaction counts from the evidence file (that Python block is the gate; forge's own output is filtered); the CI job `forge` runs it |
| `collect_evidence.py` | broadcast records → `evidence-<chainId>.json` + the paragraph above |

What the game's rules make impossible in one sitting: only the acceptance of an unchallenged claim, which needs the
challenge window to pass (24 hours by default, 10 minutes with `FAST_PARAMS`). Everything else, including the
whole bisection and the rejecting verifier call, is sequential and immediate.

---

## 中文

### 发送什么

`transcript.json`（由 `gen_transcript.py` 生成）记录两个场景的每一笔交易，与 `contracts/test/data/game_gen.py` 为 Foundry
端到端测试（`contracts/test/game/E2E.t.sol` 的 `inference/A_honest_unchallenged` 与 `inference/B_class_gate_as_claimant`）
生成的走法完全相同：`gen_transcript.py --check` 逐字节核对每一步的 calldata 与 `game_moves.bin` 中的记录一致。只有申领 id 和
挑战者地址要到运行时才知道，脚本把这两个字打进 calldata。输入方式由游戏合约决定（§7.1）：`inputProvider = 0x0`（默认部署，
情形 (a)）时申领自带 92 字节输入、由游戏自行计算 `inputRoot`；设有提供者（情形 (b)）时先由提供者 `publishInputs`。

| 步骤 | 发送方 | 交易数 | gas（anvil 回执，Foundry 1.8.3） | 说明 |
|---|---|---|---|---|
| 0. 上传 `build/mlp_digits.tapnet2`（88,854 字节，6 块），仅当 `largeStored` 为 false | 存储合约所有者（`addKnown`）、申领者（`beginLarge`、6 × `putChunk`） | 8 | **19,472,414**（`addKnown` 44,032；`beginLarge` 72,344；`putChunk` 3,566,338 / 3,558,223 / 3,558,679 / 3,558,220 / 3,557,497 / 1,557,081） | 每个存储合约一次；两个场景共用同一哈希 `0x561f036b…300e` |
| B. 类别谎言，诚实挑战者 | 申领者：`claim`、`commitBeats`、2 × `revealBeat`、`commitTuples`、3 × `revealSeg`、`proveSegment`（9 笔）；挑战者：`challenge`、`commitBeats`、2 × `revealBeat`、`commitTuples`、3 × `revealSeg`（8 笔） | **17** | **2,934,657**：`claim` 560,329 · `challenge` 143,540 · `commitBeats` 72,002 / 90,024 · `revealBeat` 68,985 / 55,027 / 51,798 / 60,078 · `commitTuples` 369,622 / 124,979 · `revealSeg` 54,332 / 65,955 / 54,459 / 58,451 / 54,471 / 58,024 · `proveSegment` 992,581 | 9 轮；第 90 拍的最终调用 `verify_segment(seg_2 → seg_3)`（见证 776 字节）返回 false；申领 **Rejected**，`outcomeOf` = 挑战者，`VerifierFalse`；挑战者获两份保证金 |
| A. 诚实申领，无人挑战 | 申领者：`claim`；任何人：窗口过后 `accept` | **2** | **597,963**（`claim` 543,229；`accept` 54,734） | 申领 **Accepted**；申领者保证金退回 |

合计：上传 + B + A 共 27 笔交易、23,005,034 gas；上传 + B 为 19 笔、22,407,071 gas。情形 (b) 的部署（设了 `INPUT_PROVIDER`）
每次申领前多一笔提供者的 `publishInputs`（35,984 gas），申领本身更便宜（约 235k，输入不上链）：B 为 18 笔、2,682,565 gas，
A 为 3 笔、325,971 gas（anvil，`FAST_PARAMS=true INPUT_PROVIDER=…`；本地测试两种部署都跑）。按 BSC 测试网 0.1 gwei 计约 0.0023 tBNB，
另加两份 `game.bond()` 保证金（默认各 0.01 tBNB；B 中申领者的保证金判给挑战者，A 中退回）。这里的 `claim` 比
`inference_gas.csv` 贵（560k 对 235k），因为情形 (a) 的申领自带输入且游戏要哈希 92 个输入叶；其余各步与测试数字一致。
`forge script` 的预估约为回执的 1.3 倍，按预估准备余额。

### 时间规则与等待

游戏的每一步**最多** `moveTime`（1 小时），没有最短间隔，所以场景 B 是一次广播：`forge script --slow` 逐笔发送 17 笔交易、
每笔等上一笔回执，在 BSC 测试网（3 秒出块）一两分钟完成。唯一必须等的是 `accept`：在 `block.timestamp > windowEnd`
（申领时间 + `challengeWindow`）之前回退 `TooEarly`。按部署脚本默认参数（挑战窗口 1 天、moveTime 1 小时、perBeatTime 60 秒）：

| | 默认部署（`largeNetlists`） | `FAST_PARAMS=true` 部署（`largeNetlistsFast`） |
|---|---|---|
| 场景 B（上传 + 申领 + 16 步争议） | 一次完成，无需等待 | 一次完成，无需等待 |
| 场景 A 第 1 步（上传 + 申领） | 一次完成 | 一次完成 |
| 场景 A 第 2 步（`accept`） | **24 小时后**（`STEP=accept`） | **10 分钟后**（`STEP=accept`） |

没有哪一步需要数天。`FAST_PARAMS`（加在 `DeployLargeNetlists.s.sol` 中）**只**把挑战窗口缩短到 10 分钟；`moveTime` 和
`perBeatTime` 保持默认，因为游戏步与步之间本就没有最短间隔，缩短 `moveTime` 只会让 RPC 卡顿变成致命问题（过期的一步回退
`TooLate`，随后争议以 `timeout` 判迟到方输）。快速变体把记录写到 `deployments/<chainId>.json` 的独立小节 `largeNetlistsFast`，
不会覆盖默认部署的记录；运行器用 `DEPLOYMENT_SECTION=largeNetlistsFast` 读取。截止时间也意味着：一小时内不能完成就不要开始
场景 B（RPC 出问题时 `--resume` 补发同一广播中未发出的交易；错过截止的一方以 `timeout` 判负）。

### 密钥

两把**不同的**测试密钥：`challenge` 从申领者自己的地址发出会回退 `NotYourTurn`，一把钥匙不能同时扮演两个角色。`CLAIMANT_KEY`
自然是部署密钥（存储合约所有者，上传时 `addKnown` 需要它；否则设 `OWNER_KEY`），`CHALLENGER_KEY` 是另一个从水龙头领了一份
保证金加 gas 的新账户。情形 (b) 的部署（设了 `INPUT_PROVIDER`）还需要 `PROVIDER_KEY`（提供者不能是申领者）。密钥只放在 shell
环境变量里，脚本从不打印。

### 命令（BSC 测试网，chainId 97）

```bash
export RPC_URL=https://bsc-testnet-rpc.publicnode.com
export CLAIMANT_KEY=…        # 部署密钥（存储合约所有者）；约 0.03 tBNB 足够全部
export CHALLENGER_KEY=…      # 另一把测试密钥；保证金 0.01 tBNB + gas

# 先要有 DEPLOY.md 的部署：contracts/deployments/97.json（小节 largeNetlists）
contracts/script/run-inference-dispute.sh                      # 场景 B：需要时上传，申领，争议；Rejected
SCENARIO=A contracts/script/run-inference-dispute.sh           # 场景 A：申领；打印窗口结束时间
# …… 24 小时后（FAST_PARAMS=true 部署并加 DEPLOYMENT_SECTION=largeNetlistsFast 时为 10 分钟后）：
SCENARIO=A STEP=accept contracts/script/run-inference-dispute.sh
```

封装脚本执行
`forge script script/RunInferenceDispute.s.sol:RunInferenceDispute --rpc-url "$RPC_URL" --broadcast --slow -vv`，
然后运行 `collect_evidence.py`：合并该链的全部广播记录，按申领 id 把每笔交易归入场景、标出角色与函数，只读回读
`claimOf`/`outcomeOf`，写出 `demo/inference/onchain/evidence-97.json`。`DRY=1` 只模拟不发送。其他开关：`STEP=upload|claim|
dispute|accept`，`CLAIM_ID`（状态文件丢失时指定已有申领），`START_MOVE`（从转录的某个下标续跑争议；通常
`FORGE_SCRIPT_FLAGS=--resume` 才是对的工具），`FORGE_SCRIPT_FLAGS=--legacy` 用于不支持 EIP-1559 的 RPC。状态文件只在真正广播时写入（`DRY=1` 从不写），但申领之后中断的广播
会让它领先于链上状态：用 `--resume` 修复，它会补发记录中尚未发出的交易。

X Layer 测试网（1952）用 `RPC_URL=https://testrpc.xlayer.tech`，其余相同。

### 发回什么

1. `demo/inference/onchain/evidence-<chainId>.json`：每笔交易的哈希、区块、角色、函数、gas，按场景分组，以及从链上读回的
   最终状态（与 `deployments/<chainId>.json` 一起提交）。
2. `contracts/deployments/<chainId>.inference.json`：状态文件（申领 id、双方地址、窗口结束时间）。
3. `contracts/broadcast/RunInferenceDispute.s.sol/<chainId>/run-*.json`（git 忽略；附件发回）。
4. `collect_evidence.py` 打印的段落（见下），填上 `<COMMIT>`。不要发任何密钥或水龙头账户。

### 证据段落模板（`collect_evidence.py` 会填好打印）

```markdown
**链上争议复现（BNB Smart Chain 测试网，chainId 97，<DATE>）。** 对已部署的游戏合约 `<GAME>`（提交 `<COMMIT>`），申领者就
分类器网表 `0x561f036b1c051727b724839a77498a5592b0f324436fd971153644c426df300e` 发布了申领 <ID>（T = 92，K = 8，13 个
检查点叶；checkpointRoot `0xcc0a5bfb…7e9e`），其中含一处类别谎言（第 90 拍翻转了送入类别寄存器的一个门：类别 0 而非 1），
交易 `<CLAIM TX>`；诚实挑战者在检查点索引 3 处发起挑战，交易 `<CHALLENGE TX>`；经 9 轮（共 17 笔交易，约 293 万 gas）后，
申领者自己的最终调用 `proveSegment`（交易 `<PROVE TX>`，`verify_segment(seg_2 → seg_3)`，见证 776 字节）使
`TapLargeVerifier.verifySegment` 返回 false，游戏判申领被拒（`claimOf(<ID>).status = Rejected`，`outcomeOf` = 挑战者，
`VerifierFalse`）。场景 A 的诚实申领 <ID A> 在挑战窗口过后被接受（`accept` 交易 `<ACCEPT TX>`）。交易见
https://testnet.bscscan.com/tx/<hash>；完整清单在 `demo/inference/onchain/evidence-97.json`。
```

### 文件

| 文件 | 作用 |
|---|---|
| `gen_transcript.py` | 用 `game_gen.py` 的镜像裁判生成 `transcript.json`（仅标准库；`--check` 与 `game_moves.bin` 比对） |
| `transcript.json` | 全部交易：calldata、解码后的参数、发送角色、每次揭示时游戏应要求的下标、预期结果 |
| `contracts/script/RunInferenceDispute.s.sol` | forge 脚本：读部署文件 → 缺则上传 → 申领 → 争议或接受；逐项对链上状态断言 |
| `contracts/script/run-inference-dispute.sh` | 一条命令（forge script + 证据收集） |
| `contracts/script/anvil-inference-e2e.sh` | 本地端到端测试：向 anvil 部署两套（默认，情形 (a)；`FAST_PARAMS` 加输入提供者，情形 (b)），在两套上跑 B 和 A，拨快时钟越过窗口，检查 dry run 不写状态，并按证据文件断言 Rejected / Accepted 与交易数（这段 Python 是判定点；forge 自身输出经 grep 过滤）；CI 的 `forge` 作业运行它 |
| `collect_evidence.py` | 广播记录 → `evidence-<chainId>.json` + 上面的段落 |

游戏规则下唯一不能一次完成的：无人挑战的申领要等挑战窗口过去才能接受（默认 24 小时，`FAST_PARAMS` 下 10 分钟）。其余一切，
包括整个二分和作出拒绝判决的验证器调用，都是顺序且即时的。
