# Draft "Deployments" section for TAP-7007

TAP-01 §7.2 item 9 asks, for every chain: the addresses, the implementation each proxy must point to, how anyone
can verify these read-only, and whether each upgrade path is sealed. None of these contracts is a proxy. Replace
`<ADDRESS>`, `<BLOCK>`, `<DATE>`, `<COMMIT>` from the author's `contracts/deployments/<chainId>.json` and the
editors' read-back (`docs/deploy/VERIFY.md`). The TAP currently says "This TAP depends on no deployed contract";
that stays true, and the paragraph below makes the testnet deployment's status explicit.

```markdown
## Deployments

This TAP depends on no deployed contract. A store conforming to §3 is the chunk oracle of the verifiers (§6), and an
application chooses its store, its verifier and its game; this TAP records none as canonical.

**Reference deployment (testnet).** The reference implementation (`contracts/`, commit `<COMMIT>`) is deployed on
<CHAIN NAME> (chainId <CHAINID>) so that provers, challengers and tools can be tested against live contracts. The
dispute game is the informative game of §7 with test parameters; it is not production code (Reference
Implementation). Read back on <DATE> at block <BLOCK>, read-only, from two RPC operators (`<RPC 1>`, `<RPC 2>`);
every value matched.

| Contract | Address | Implementation it must point to | How to verify read-only | Sealed? |
|---|---|---|---|---|
| `TapLargeNetlistStoreOwned` (chunk oracle, §3) | `<ADDRESS>` | n.a. (not a proxy) | `supportsInterface(0x8230af7f)` = true (`ITapLargeNetlistStore`); `largeInfo(0xf5cd9000…95b0)` = `(336, 1, 8, 1, true)` for the `message` example of `assets/tap-draft-large-netlists/vectors.json` and `largeChunk(h, 0)` hashes (SHA-256) to `0xe62eb706…c5a5`; <if uploaded> `largeInfo(0x6abfe829…758f)` = `(89509, 16, 16, 6, true)` and the six chunk leaves equal `files.large.chunkLeaves`; each `largeChunkPointer` holds `0x00 ‖ chunk`; code equals a local build except the immutable `owner` | no upgrade path. The owner `<DEPLOYER>` may `addKnown` further hashes (an application's commit, §3 item 7) and can remove or change nothing; stored chunks are immutable code |
| `TapLargeVerifier` (verify_segment and beat-end verifiers, §6) | `<ADDRESS>` | n.a. (not a proxy) | `store()` = the store above; `segmentBudget()` = `(6000, 256, 16)` (§5.1 RECOMMENDED); `ZERO_MEM()` = `0x073f71c2…dcff`; selectors `verifySegment` `0xaca8e751`, `verifyBeatEnd` `0x364abc73`; any `verifySegment`/`verifyBeatEnd` vector of `vectors.json` whose file is uploaded returns its `expect`; code equals a local build except four immutable words | stateless and ownerless: nothing to seal |
| `TapLargeGameLib` (external library of the game) | `<ADDRESS>` | n.a. | `cast code` equals `deployedBytecode` of a local build apart from the library's own address in its call-protection prefix; `checkpointRoot([L])` = `L` | stateless |
| `TapLargeDisputeGame` (informative game, §7) | `<ADDRESS>` | n.a. (not a proxy) | `store()`, `verifier()` = the two above; `bond()` = `<BOND>` wei; `challengeWindow()` = 86400; `moveTime()` = 3600; `perBeatTime()` = 60; `maxK()` = 64; `maxT()` = 131008; `inputProvider()` = `0x0` (case (a)); `MAX_CHECKPOINTS()` = 2048; `initialLeaf()` = `0x2770ce5f…6d0f` = `checkpointLeaf(0, EMPTY, EMPTY)`; `segmentBudget()` = `(6000, 256, 16)`; code equals a local build linked to the library above, except the immutables | every parameter is immutable; no owner, no upgrade path. Bonds are held by the contract and settled per dispute (§7.1); nobody can withdraw another party's credit |

Deployer `<DEPLOYER>` (a test account). Transactions:
`contracts/broadcast/DeployLargeNetlists.s.sol/<CHAINID>/run-latest.json` at commit `<COMMIT>`. No mainnet
deployment exists. This TAP records deployments; it cannot order them.
```

If the author did not set `UPLOAD_LARGE`, drop the `<if uploaded>` clause; the Deployments section must list only
what was read back.
