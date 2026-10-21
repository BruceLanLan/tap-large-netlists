# Read-only verification of a TAP-7007 deployment

For editors. Only `eth_call`, `eth_getCode` and local computation: no key, no transaction. Addresses come from
the author's `contracts/deployments/<chainId>.json` (DEPLOY.md §7). Expected values are those of the examples in
`assets/tap-draft-large-netlists/vectors.json`, which the deployment script uploads.

```bash
export RPC_URL=…               # https://testrpc.xlayer.tech (1952) or a BSC Testnet RPC (97), DEPLOY.md §2
export STORE=<ADDRESS>         # largeNetlists.TapLargeNetlistStoreOwned
export VERIFIER=<ADDRESS>      # largeNetlists.TapLargeVerifier
export GAMELIB=<ADDRESS>       # largeNetlists.TapLargeGameLib
export GAME=<ADDRESS>          # largeNetlists.TapLargeDisputeGame
MSG=0xf5cd90005f48bc1eac7173c4f05c1af9710a1b6bd689d9377709c522627095b0     # netlistHash2 of `message`
LARGE=0x6abfe829d81de05e2997125e2aa31e24a8b4f3da306e2fee6621ade5e1a0758f   # netlistHash2 of `large`
```

Add `--block <N>` to every `cast call` for a record others can repeat, and write `N` into the Deployments section.

## 1. Code

```bash
for a in $STORE $VERIFIER $GAMELIB $GAME; do echo "$a $(cast codesize $a --rpc-url $RPC_URL) bytes, keccak $(cast keccak "$(cast code $a --rpc-url $RPC_URL)")"; done
```

`contracts/foundry.toml` sets `bytecode_hash = "none"` and `cbor_metadata = false`, so a local build with solc
0.8.28, optimizer 10,000 runs and EVM `shanghai` reproduces the runtime code exactly, except for: the store's
immutable `owner`, the verifier's immutables (`store`, the three budget words), the game's immutables (store,
verifier, bond, times, bounds, provider, `initialLeaf`) and the library address linked into the game. The library
and the verifier are the cleanest comparisons:

```bash
cd contracts && forge build --libraries src/game/TapLargeGameLib.sol:TapLargeGameLib:$GAMELIB
python3 - <<'EOF'
import json, os, subprocess
rpc = os.environ["RPC_URL"]
def code(a): return subprocess.check_output(["cast", "code", a, "--rpc-url", rpc]).decode().strip().lower()
def local(f, c): return "0x" + json.load(open(f"out/{f}/{c}.json"))["deployedBytecode"]["object"].lower()
# the library's own address is written into its first instructions (call protection): mask it
lib = code(os.environ["GAMELIB"]); loc = local("TapLargeGameLib.sol", "TapLargeGameLib")
print("TapLargeGameLib", "identical (address masked)" if lib[46:] == loc[46:] and len(lib) == len(loc) else "DIFFERENT")
# the verifier has immutables: compare lengths and all bytes outside the 4 immutable slots by diffing
ver = code(os.environ["VERIFIER"]); lv = local("TapLargeVerifier.sol", "TapLargeVerifier")
diff = sum(1 for a, b in zip(ver, lv) if a != b)
print("TapLargeVerifier", f"{len(ver)//2 - 1} bytes, {diff//2} differing bytes (expect ≤ 128 = 4 immutable words)")
EOF
```

`forge inspect src/TapLargeVerifier.sol:TapLargeVerifier bytecode` and the explorer's source verification (§4) do
the immutable-aware comparison for you.

## 2. ERC-165 and selectors

```bash
cast call $STORE "supportsInterface(bytes4)(bool)" 0x01ffc9a7 --rpc-url $RPC_URL   # true
cast call $STORE "supportsInterface(bytes4)(bool)" 0x8230af7f --rpc-url $RPC_URL   # true: ITapLargeNetlistStore
cast call $STORE "supportsInterface(bytes4)(bool)" 0xffffffff --rpc-url $RPC_URL   # false
```

`0x8230af7f` = XOR of the selectors of `beginLarge(bytes32,uint64,bytes32)`, `putChunk(bytes32,uint32,bytes,bytes32[])`,
`largeStored(bytes32)`, `largeChunk(bytes32,uint32)` and `largeInfo(bytes32)` (check with `cast sig` and
`forge inspect src/ITapLargeNetlistStore.sol:ITapLargeNetlistStore methods`). The verifier has no ERC-165; its
functions are identified by selector:

| Function | Selector (`cast sig`) |
|---|---|
| `verifySegment(bytes32,bytes32,bytes32,(uint64,uint32,uint32,bytes32,bytes32,bytes32),(uint64,uint32,uint32,bytes32,bytes32,bytes32),bytes,(uint32,uint32,uint32))` | `0xaca8e751` |
| `verifyBeatEnd(bytes32,uint32,(uint64,uint32,uint32,bytes32,bytes32,bytes32),(uint32,bytes32),(uint32,bytes32),bytes)` | `0x364abc73` |
| `segmentBudget()` | `0x09683cea` |
| `largeChunk(bytes32,uint32)` / `largeInfo(bytes32)` (what the verifier calls on the store) | `0x75077164` / `0x260759cd` |

## 3. Getters

### 3.1 Store (`TapLargeNetlistStoreOwned`)

| Call | Expected |
|---|---|
| `cast call $STORE "owner()(address)"` | the deployer |
| `cast call $STORE "knownNetlist(bytes32)(bool)" $MSG` | `true`; `false` for a random hash |
| `cast call $STORE "largeStored(bytes32)(bool)" $MSG` | `true` |
| `cast call $STORE "largeInfo(bytes32)(uint64,uint32,uint32,uint32,bool)" $MSG` | `336 1 8 1 true` (length, nIn, nOut, chunks stored, stored) |
| `cast call $STORE "largeChunk(bytes32,uint32)(bytes)" $MSG 0` | the 336 bytes of `files.message.hex`; `sha256` of them = `0xe62eb706c3fc2a48756a083e8d01fe8a4a10a25893b2694aaf25d7281de1c5a5` (`files.message.sha256`) |
| `cast call $STORE "largeChunkPointer(bytes32,uint32)(address)" $MSG 0` | a contract whose code is `0x00` ‖ chunk (`cast code <pointer>`; 337 bytes) |
| `cast call $STORE "largeChunk(bytes32,uint32)(bytes)" $MSG 1` | reverts `ChunkNotStored(1)` |
| with `UPLOAD_LARGE`: `largeInfo $LARGE` | `89509 16 16 6 true` |
| `largeChunk $LARGE i` for `i` = 0 … 5 | chunks of `files.large.hex` (5 × 16,384 bytes + 7,589); their leaf hashes `SHA-256(0x02 ‖ u32be(i) ‖ chunk)` must be `files.large.chunkLeaves[i]` (`0x774a0541…`, `0x38cae52a…`, `0xf9b97afd…`, `0x89fc9429…`, `0xbbd21ea4…`, `0x91e57a10…`) and fold to `chunkRoot 0x64c498f2…ad78`, and `netlistHash2 = SHA-256(0x03 ‖ u64be(89509) ‖ chunkRoot)` = `$LARGE` |

Recompute the hashes with the reference implementation:

```bash
cd assets/tap-draft-large-netlists
python3 -c "
import json, tapnet2, subprocess, os
h = '$LARGE'
for i in range(6):
    out = subprocess.check_output(['cast','call',os.environ['STORE'],'largeChunk(bytes32,uint32)(bytes)',h,str(i),'--rpc-url',os.environ['RPC_URL']]).decode().strip()
    print(i, tapnet2.chunk_leaf(i, bytes.fromhex(out[2:])).hex())"
```

(`tapnet2.py` is the reference: `chunk_leaf(i, chunk)` = SHA-256(0x02 ‖ u32be(i) ‖ chunk), `chunk_root(data)`,
`netlist_hash2(data)` = SHA-256(0x03 ‖ u64be(len) ‖ root).) An uploaded file can also be fetched whole and checked: concatenate the chunks, `sha256sum`, and
compare with `files.<name>.sha256`.

### 3.2 Verifier (`TapLargeVerifier`)

| Call | Expected |
|---|---|
| `cast call $VERIFIER "store()(address)"` | `$STORE` |
| `cast call $VERIFIER "segmentBudget()(uint32,uint32,uint32)"` | `6000 256 16` (§5.1 RECOMMENDED) |
| `cast call $VERIFIER "DEFAULT_GATES()(uint32)"`, `DEFAULT_EXT_WORDS`, `DEFAULT_CHUNKS` | `6000`, `256`, `16` |
| `cast call $VERIFIER "ZERO_MEM()(bytes32)"` | `0x073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff` (`meta.zeroMem`) |

A live `verifySegment` / `verifyBeatEnd` check uses a vector: `contracts/test/Vectors.t.sol` replays every
`verifySegment` and `verifyBeatEnd` case of `vectors.json` against a local verifier. To run one against the
deployed contract, encode the vector's arguments with `cast calldata` and `cast call $VERIFIER <calldata>`; the
result must be the vector's `expect`. The uploaded `message` file is the chunk oracle of the `message` cases.

### 3.3 Game (`TapLargeDisputeGame`, informative)

| Call | Expected (defaults) |
|---|---|
| `cast call $GAME "store()(address)"`, `"verifier()(address)"` | `$STORE`, `$VERIFIER` |
| `cast call $GAME "bond()(uint256)"` | `10000000000000000` (0.01 ether, or `BOND`) |
| `cast call $GAME "challengeWindow()(uint64)"`, `moveTime`, `perBeatTime` | `86400`, `3600`, `60` |
| `cast call $GAME "maxK()(uint64)"`, `maxT` | `64`, `131008` (= 64 × 2047) |
| `cast call $GAME "inputProvider()(address)"` | `0x0` (case (a)) |
| `cast call $GAME "MAX_CHECKPOINTS()(uint256)"` | `2048` |
| `cast call $GAME "initialLeaf()(bytes32)"` | `0x2770ce5f9266ec7331a4479dfdfe1f22f261a49d52d5f17c2f7b64865d4b6d0f` (`checkpoints.initialLeaf` = `checkpointLeaf(0, EMPTY, EMPTY)`) |
| `cast call $GAME "segmentBudget()(uint32,uint32,uint32)"` | `6000 256 16` (forwarded from the verifier) |
| `cast call $GAME "claimOf(uint256)" 0` | reverts (no claim made) |
| `cast call $GAME "credit(address)(uint256)" <any>` | `0` |

### 3.4 Library (`TapLargeGameLib`)

Stateless, called by the game with DELEGATECALL. Read-only use:

```bash
# checkpointRoot of one leaf is the leaf itself
cast call $GAMELIB "checkpointRoot(bytes32[])(bytes32)" "[0x2770ce5f9266ec7331a4479dfdfe1f22f261a49d52d5f17c2f7b64865d4b6d0f]" --rpc-url $RPC_URL
# inputRoot of T = 1 beat, nIn = 1 (the message example), input bit 0
cast call $GAMELIB "inputRoot(uint64,uint256,bytes)(bytes32)" 1 1 0x00 --rpc-url $RPC_URL
```

Compare with `checkpoints.*` and the `inputs` of a `checkpoints` vector in `vectors.json`.

## 4. Source verification on the explorer

Optional; needs the author's explorer API key (named here only as an environment variable) and the constructor
arguments from `deployments/<chainId>.json`.

```bash
cd contracts
# BNB Smart Chain Testnet (97), BscScan through the Etherscan v2 API
forge verify-contract --chain 97 --verifier etherscan --etherscan-api-key "$ETHERSCAN_API_KEY" \
  --constructor-args "$(cast abi-encode 'constructor(address)' <DEPLOYER>)" \
  $STORE src/TapLargeNetlistStore.sol:TapLargeNetlistStoreOwned --watch
forge verify-contract --chain 97 --verifier etherscan --etherscan-api-key "$ETHERSCAN_API_KEY" \
  --constructor-args "$(cast abi-encode 'constructor(address,uint32,uint32,uint32)' $STORE 6000 256 16)" \
  $VERIFIER src/TapLargeVerifier.sol:TapLargeVerifier --watch
forge verify-contract --chain 97 --verifier etherscan --etherscan-api-key "$ETHERSCAN_API_KEY" \
  $GAMELIB src/game/TapLargeGameLib.sol:TapLargeGameLib --watch
forge verify-contract --chain 97 --verifier etherscan --etherscan-api-key "$ETHERSCAN_API_KEY" \
  --libraries src/game/TapLargeGameLib.sol:TapLargeGameLib:$GAMELIB \
  --constructor-args "$(cast abi-encode 'constructor((address,address,uint256,uint64,uint64,uint64,uint64,uint64,address))' "($STORE,$VERIFIER,10000000000000000,86400,3600,60,64,131008,0x0000000000000000000000000000000000000000)")" \
  $GAME src/game/TapLargeDisputeGame.sol:TapLargeDisputeGame --watch

# X Layer Testnet (1952): OKLink's API (URL from the X Layer docs "Verify a contract" page,
# https://web3.okx.com/xlayer/docs; confirm it there before use), same four commands with
#   --chain 1952 --verifier oklink --etherscan-api-key "$OKLINK_API_KEY" \
#   --verifier-url https://www.oklink.com/api/v5/explorer/contract/verify-source-code-plugin/XLAYER_TESTNET
# Keyless where the chain is listed at https://sourcify.dev/#/chains: --verifier sourcify
```

`forge verify-contract --show-standard-json-input …` prints the standard JSON for an explorer's web form.

## 5. Record

Chain id, block read at, the four addresses and code hashes (§1), the ERC-165 and selector table (§2),
`segmentBudget`, `largeInfo` of the uploaded files and one chunk-leaf recomputation (§3.1), the game's immutable
parameters (§3.3). Draft text with placeholders: `docs/deploy/deployments-sections.md`.
