// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Script} from "forge-std/Script.sol";
import {console2 as console} from "forge-std/console2.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../src/TapLargeHash.sol";
import {TapLargeDisputeGame} from "../src/game/TapLargeDisputeGame.sol";
import {Upload} from "../test/utils/Upload.sol";

/// @title Replays the inference demo's disputes (demo/inference) against DEPLOYED TAP-7007 reference contracts
/// @notice Reads deployments/<chainId>.json (section DEPLOYMENT_SECTION, default "largeNetlists") and the
///         transcript demo/inference/onchain/transcript.json (written by gen_transcript.py from the same move
///         generator as the Foundry end-to-end tests), uploads the 88,854-byte classifier netlist (6 chunks) if the
///         store does not hold it, and sends the recorded transactions of one scenario from two accounts:
///
///           SCENARIO=B  (default)  a claimant whose class lie (a gate flipped at beat 90) is challenged by an
///                                  honest challenger; 16 dispute transactions end in the claimant's own
///                                  proveSegment call, verifySegment returns false, the claim is Rejected;
///           SCENARIO=A             an honest claim that nobody challenges; `accept` after the challenge window.
///
///         Environment: CLAIMANT_KEY and CHALLENGER_KEY (two DIFFERENT test keys: the game rejects a challenge by
///         the claimant's own address), each funded with the bond (`game.bond()`) plus gas; OWNER_KEY (the store
///         owner = deployer; defaults to CLAIMANT_KEY; needed only when the netlist is not uploaded yet);
///         PROVIDER_KEY (only for a case-(b) deployment, INPUT_PROVIDER set; the provider cannot be the claimant);
///         STEP = all | upload | claim | dispute | accept (default all); CLAIM_ID to name an existing claim;
///         START_MOVE (transcript index to resume a dispute from; normally use `forge script --resume`).
///
///         Time rules of the game: every move has moveTime seconds at most and no minimum, so a whole dispute is
///         one broadcast (use --slow: one receipt before the next transaction). Only `accept` has to wait: it
///         reverts TooEarly until block.timestamp > windowEnd (challengeWindow after the claim). Scenario A is
///         therefore two runs: STEP=all (upload + claim), then STEP=accept after the window.
///         State (claim id, parties, window end, status) is kept in deployments/<chainId>.inference.json.
///
///         forge script script/RunInferenceDispute.s.sol:RunInferenceDispute --rpc-url "$RPC_URL" --broadcast --slow
contract RunInferenceDispute is Script {
    string internal constant TRANSCRIPT = "../demo/inference/onchain/transcript.json";
    string internal constant NETLIST = "../demo/inference/build/mlp_digits.tapnet2";
    uint8 internal constant PHASE_NONE = 0;
    uint8 internal constant PHASE_RESOLVED = 8;

    struct Ctx {
        TapLargeNetlistStoreOwned store;
        TapLargeVerifier verifier;
        TapLargeDisputeGame game;
        string section;
        string scenario;
        string step;
        string json; // the transcript
        string statePath;
        string stateKey;
        bytes32 nh2;
        uint256 bond;
        bool caseB;
        uint256 claimantKey;
        uint256 challengerKey;
        uint256 ownerKey;
        uint256 providerKey;
        address claimant;
        address challenger;
        bool hasClaim;
        uint256 claimId;
    }

    Ctx internal c;
    uint256 internal broadcasting; // key currently broadcasting, 0 if none

    function run() external {
        _checkChain();
        _load();
        bool all = _eq(c.step, "all");
        if (all || _eq(c.step, "upload")) _upload();
        if (all || _eq(c.step, "claim")) {
            if (c.hasClaim) console.log("claim exists, id", c.claimId);
            else _claim();
        }
        if (_eq(c.scenario, "B") && (all || _eq(c.step, "dispute"))) {
            require(c.hasClaim, "no claim yet: run STEP=claim (or STEP=all) first, or set CLAIM_ID");
            _dispute();
        }
        if (_eq(c.scenario, "A") && (all || _eq(c.step, "accept"))) {
            require(c.hasClaim, "no claim yet: run STEP=claim (or STEP=all) first, or set CLAIM_ID");
            _accept(!all);
        }
        _stop();
        _status();
    }

    // ================================================================== setup

    function _load() internal {
        c.section = vm.envOr("DEPLOYMENT_SECTION", string("largeNetlists"));
        c.scenario = vm.envOr("SCENARIO", string("B"));
        c.step = vm.envOr("STEP", string("all"));
        require(_eq(c.scenario, "A") || _eq(c.scenario, "B"), "SCENARIO must be A or B");
        string memory depPath = string.concat("deployments/", vm.toString(block.chainid), ".json");
        require(vm.exists(depPath), string.concat("missing ", depPath, ": deploy first (DEPLOY.md)"));
        string memory dep = vm.readFile(depPath);
        string memory base = string.concat(".", c.section, ".");
        c.store = TapLargeNetlistStoreOwned(vm.parseJsonAddress(dep, string.concat(base, "TapLargeNetlistStoreOwned")));
        c.verifier = TapLargeVerifier(vm.parseJsonAddress(dep, string.concat(base, "TapLargeVerifier")));
        c.game = TapLargeDisputeGame(vm.parseJsonAddress(dep, string.concat(base, "TapLargeDisputeGame")));
        require(address(c.game.store()) == address(c.store), "game.store() differs from the deployment file");
        require(address(c.game.verifier()) == address(c.verifier), "game.verifier() differs from the deployment file");

        c.json = vm.readFile(TRANSCRIPT);
        c.nh2 = vm.parseJsonBytes32(c.json, ".netlist.netlistHash2");
        c.bond = c.game.bond();
        c.caseB = c.game.inputProvider() != address(0);

        c.claimantKey = vm.envUint("CLAIMANT_KEY");
        c.challengerKey = vm.envUint("CHALLENGER_KEY");
        c.ownerKey = vm.envOr("OWNER_KEY", c.claimantKey);
        c.providerKey = vm.envOr("PROVIDER_KEY", uint256(0));
        c.claimant = vm.addr(c.claimantKey);
        c.challenger = vm.addr(c.challengerKey);
        require(c.claimant != c.challenger, "CLAIMANT_KEY and CHALLENGER_KEY must differ (the game rejects a challenge by the claimant)");
        if (c.caseB) {
            require(c.providerKey != 0, "case-(b) deployment: set PROVIDER_KEY (the game's inputProvider)");
            require(vm.addr(c.providerKey) == c.game.inputProvider(), "PROVIDER_KEY is not the game's inputProvider");
            require(vm.addr(c.providerKey) != c.claimant, "the input provider cannot be the claimant");
        }

        c.statePath = string.concat("deployments/", vm.toString(block.chainid), ".inference.json");
        c.stateKey = string.concat(c.section, "_", c.scenario);
        uint256 forced = vm.envOr("CLAIM_ID", type(uint256).max);
        if (forced != type(uint256).max) {
            c.claimId = forced;
            c.hasClaim = true;
        } else if (vm.exists(c.statePath)) {
            string memory st = vm.readFile(c.statePath);
            string memory k = string.concat(".", c.stateKey);
            if (vm.keyExistsJson(st, k)) {
                c.claimId = vm.parseJsonUint(st, string.concat(k, ".claimId"));
                c.hasClaim = true;
            }
        }
        if (c.hasClaim) {
            TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
            require(cl.claimant == c.claimant, "the recorded claim was not made by CLAIMANT_KEY");
            require(cl.netlistHash2 == c.nh2, "the recorded claim is about another netlist");
        }

        console.log("chain id        ", block.chainid);
        console.log("deployment      ", c.section);
        console.log("game            ", address(c.game));
        console.log("store           ", address(c.store));
        console.log("scenario / step ", c.scenario, c.step);
        console.log("claimant        ", c.claimant, c.claimant.balance);
        console.log("challenger      ", c.challenger, c.challenger.balance);
        console.log("bond (wei)      ", c.bond);
        console.log("input case      ", c.caseB ? "(b) provider publishes" : "(a) inputs in the claim");
        console.log("netlistHash2    ", vm.toString(c.nh2));
    }

    // ================================================================== 1. the netlist (6 chunks)

    function _upload() internal {
        if (c.store.largeStored(c.nh2)) {
            console.log("netlist already stored; no upload");
            return;
        }
        bytes memory file = vm.readFileBinary(NETLIST);
        require(file.length == vm.parseJsonUint(c.json, ".netlist.bytes"), "netlist file length differs from the transcript");
        bytes32[][] memory lvs = Upload.levels(Upload.leaves(file));
        bytes32 root = lvs[lvs.length - 1][0];
        require(root == vm.parseJsonBytes32(c.json, ".netlist.chunkRoot"), "chunk root differs from the transcript");
        require(TapLargeHash.netlistHash2(file.length, root) == c.nh2, "netlistHash2 differs from the transcript");
        uint256 n = Upload.nChunks(file.length);

        if (!c.store.knownNetlist(c.nh2)) {
            require(
                c.store.owner() == vm.addr(c.ownerKey),
                "the netlist is not known to the store and OWNER_KEY is not the store owner (the deployer): set OWNER_KEY"
            );
            _as(c.ownerKey);
            c.store.addKnown(c.nh2);
            console.log("tx addKnown (owner)");
        }
        (uint64 len,,,,) = c.store.largeInfo(c.nh2);
        _as(c.claimantKey);
        if (len == 0) {
            c.store.beginLarge(c.nh2, uint64(file.length), root);
            console.log("tx beginLarge (claimant)");
        }
        for (uint256 i; i < n; ++i) {
            if (c.store.largeChunkPointer(c.nh2, uint32(i)) != address(0)) continue;
            c.store.putChunk(c.nh2, uint32(i), Upload.chunk(file, i), Upload.proof(lvs, i));
            console.log("tx putChunk (claimant)", i);
        }
        require(c.store.largeStored(c.nh2), "upload incomplete");
    }

    // ================================================================== 2. the claim

    function _claim() internal {
        string memory p = _tx(1);
        require(_eq(vm.parseJsonString(c.json, string.concat(p, ".function")), "claim"), "transcript: tx 1 is not the claim");
        if (c.caseB) {
            string memory p0 = _tx(0);
            bytes memory pub = vm.parseJsonBytes(c.json, string.concat(p0, ".calldata"));
            _checkSelector(pub, "publishInputs");
            _as(c.providerKey);
            _send(address(c.game), pub, 0, "publishInputs (inputProvider)");
        }
        bytes memory cd = vm.parseJsonBytes(c.json, string.concat(p, c.caseB ? ".calldataCaseB" : ".calldataCaseA"));
        _checkSelector(cd, "claim");
        _as(c.claimantKey);
        bytes memory ret = _send(address(c.game), cd, c.bond, "claim (claimant)");
        c.claimId = abi.decode(ret, (uint256));
        c.hasClaim = true;
        TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
        require(cl.claimant == c.claimant, "claim: claimant");
        require(cl.T == vm.parseJsonUint(c.json, string.concat(p, ".expect.T")), "claim: T");
        require(cl.K == vm.parseJsonUint(c.json, string.concat(p, ".expect.K")), "claim: K");
        require(cl.inputRoot == vm.parseJsonBytes32(c.json, string.concat(p, ".expect.inputRoot")), "claim: inputRoot");
        require(
            cl.checkpointRoot == vm.parseJsonBytes32(c.json, string.concat(p, ".expect.checkpointRoot")),
            "claim: checkpointRoot"
        );
        console.log("claim id", c.claimId, "window ends at", cl.windowEnd);
        _save("Open");
    }

    // ================================================================== 3. the dispute (scenario B)

    function _dispute() internal {
        (TapLargeDisputeGame.Phase phase,,,,,,) = c.game.stateOf(c.claimId, c.challenger);
        uint256 start = vm.envOr("START_MOVE", uint256(2));
        if (uint8(phase) == PHASE_RESOLVED) {
            console.log("dispute already resolved");
            return;
        }
        require(
            uint8(phase) == PHASE_NONE || start > 2,
            "dispute in progress: rerun the interrupted broadcast with --resume, or set START_MOVE"
        );
        uint256 n = vm.parseJsonUint(c.json, string.concat(".scenarios.", c.scenario, ".nTxs"));
        for (uint256 i = start; i < n; ++i) {
            string memory p = _tx(i);
            string memory fn = vm.parseJsonString(c.json, string.concat(p, ".function"));
            string memory role = vm.parseJsonString(c.json, string.concat(p, ".role"));
            bytes memory cd = vm.parseJsonBytes(c.json, string.concat(p, ".calldata"));
            uint256 patch = vm.parseJsonUint(c.json, string.concat(p, ".patch"));
            _checkSelector(cd, fn);
            _patch(cd, patch);
            _checkAsk(p, fn);
            uint256 value = _eq(vm.parseJsonString(c.json, string.concat(p, ".value")), "bond") ? c.bond : 0;
            _as(_eq(role, "claimant") ? c.claimantKey : c.challengerKey);
            _send(address(c.game), cd, value, string.concat(vm.toString(i), " ", fn, " (", role, ")"));
        }
        _checkDisputeOutcome();
        _save("Rejected");
    }

    /// Before a reveal: the game must ask for the index the mirror predicted (E2E.t.sol does the same).
    function _checkAsk(string memory p, string memory fn) internal view {
        string memory k = string.concat(p, ".expect.ask");
        if (!vm.keyExistsJson(c.json, k)) return;
        uint256 want = vm.parseJsonUint(c.json, k);
        (,,, uint64 lo, uint64 hi, uint64 ask,) = c.game.stateOf(c.claimId, c.challenger);
        uint256 got = _eq(fn, "revealBeat") ? (uint256(lo) + hi) / 2 : ask;
        require(got == want, string.concat("the game asks for another index than the transcript at ", fn));
    }

    function _checkDisputeOutcome() internal view {
        string memory d = string.concat(".scenarios.", c.scenario, ".dispute");
        (address w, TapLargeDisputeGame.Role role, TapLargeDisputeGame.Reason reason) =
            c.game.outcomeOf(c.claimId, c.challenger);
        (TapLargeDisputeGame.Phase phase,,,,,, uint8 kind) = c.game.stateOf(c.claimId, c.challenger);
        require(uint8(phase) == PHASE_RESOLVED, "dispute not resolved");
        bool chWins = _eq(vm.parseJsonString(c.json, string.concat(d, ".winnerRole")), "challenger");
        require(uint8(role) == (chWins ? 1 : 0), "winner role differs from the transcript");
        require(w == (chWins ? c.challenger : c.claimant), "winner address");
        require(uint8(reason) == vm.parseJsonUint(c.json, string.concat(d, ".reasonCode")), "reason differs from the transcript");
        require(kind == vm.parseJsonUint(c.json, string.concat(d, ".pickedListCode")), "picked list differs from the transcript");
        TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
        require(
            uint8(cl.status) == vm.parseJsonUint(c.json, string.concat(".scenarios.", c.scenario, ".finalStatusCode")),
            "claim status differs from the transcript"
        );
        console.log("dispute resolved: winner", w, "reason code", uint8(reason));
    }

    // ================================================================== 4. accept (scenario A)

    function _accept(bool explicitStep) internal {
        TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
        if (uint8(cl.status) == uint8(TapLargeDisputeGame.Status.Accepted)) {
            console.log("claim already accepted");
            return;
        }
        if (block.timestamp <= cl.windowEnd) {
            string memory msg_ = string.concat(
                "the challenge window of claim ",
                vm.toString(c.claimId),
                " ends at timestamp ",
                vm.toString(cl.windowEnd),
                " (now ",
                vm.toString(block.timestamp),
                "): run again with STEP=accept after that"
            );
            if (explicitStep) revert(msg_);
            console.log(msg_);
            return;
        }
        string memory p = _tx(2);
        bytes memory cd = vm.parseJsonBytes(c.json, string.concat(p, ".calldata"));
        _checkSelector(cd, "accept");
        _patch(cd, 1);
        _as(c.claimantKey); // anyone may call; the claimant's key is at hand
        _send(address(c.game), cd, 0, "accept (claimant)");
        cl = c.game.claimOf(c.claimId);
        require(uint8(cl.status) == uint8(TapLargeDisputeGame.Status.Accepted), "claim not accepted");
        _save("Accepted");
    }

    // ================================================================== helpers

    function _status() internal view {
        if (!c.hasClaim) return;
        TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
        string[4] memory names = ["None", "Open", "Rejected", "Accepted"];
        console.log("final claim status:", names[uint8(cl.status)], "claim id", c.claimId);
        console.log("credit claimant / challenger (wei):", c.game.credit(c.claimant), c.game.credit(c.challenger));
    }

    function _tx(uint256 i) internal view returns (string memory) {
        return string.concat(".scenarios.", c.scenario, ".txs[", vm.toString(i), "]");
    }

    /// Starts broadcasting with `key` (ending the previous broadcaster first).
    function _as(uint256 key) internal {
        if (broadcasting == key) return;
        _stop();
        vm.startBroadcast(key);
        broadcasting = key;
    }

    function _stop() internal {
        if (broadcasting != 0) vm.stopBroadcast();
        broadcasting = 0;
    }

    function _send(address to, bytes memory cd, uint256 value, string memory what) internal returns (bytes memory ret) {
        console.log("tx", what);
        bool ok;
        (ok, ret) = to.call{value: value}(cd);
        if (!ok) {
            console.log("REVERTED:", what);
            assembly ("memory-safe") {
                revert(add(ret, 0x20), mload(ret))
            }
        }
    }

    /// Claim id (word 0 after the selector) and challenger (word 1): the two run-time values of a move.
    function _patch(bytes memory cd, uint256 mode) internal view {
        uint256 id = c.claimId;
        address ch = c.challenger;
        if (mode >= 1) {
            assembly ("memory-safe") {
                mstore(add(cd, 36), id)
            }
        }
        if (mode >= 2) {
            assembly ("memory-safe") {
                mstore(add(cd, 68), ch)
            }
        }
    }

    function _checkSelector(bytes memory cd, string memory fn) internal pure {
        bytes4 got;
        assembly ("memory-safe") {
            got := mload(add(cd, 32))
        }
        require(got == _selector(fn), string.concat("transcript calldata of ", fn, " has the wrong selector"));
    }

    function _selector(string memory fn) internal pure returns (bytes4) {
        if (_eq(fn, "claim")) return TapLargeDisputeGame.claim.selector;
        if (_eq(fn, "publishInputs")) return TapLargeDisputeGame.publishInputs.selector;
        if (_eq(fn, "accept")) return TapLargeDisputeGame.accept.selector;
        if (_eq(fn, "challenge")) return TapLargeDisputeGame.challenge.selector;
        if (_eq(fn, "commitBeats")) return TapLargeDisputeGame.commitBeats.selector;
        if (_eq(fn, "revealBeat")) return TapLargeDisputeGame.revealBeat.selector;
        if (_eq(fn, "commitTuples")) return TapLargeDisputeGame.commitTuples.selector;
        if (_eq(fn, "revealSeg")) return TapLargeDisputeGame.revealSeg.selector;
        if (_eq(fn, "revealEnd")) return TapLargeDisputeGame.revealEnd.selector;
        if (_eq(fn, "proveSegment")) return TapLargeDisputeGame.proveSegment.selector;
        if (_eq(fn, "proveBeatEnd")) return TapLargeDisputeGame.proveBeatEnd.selector;
        revert(string.concat("unknown function in the transcript: ", fn));
    }

    function _save(string memory status) internal {
        TapLargeDisputeGame.Claim memory cl = c.game.claimOf(c.claimId);
        string memory o = string.concat("state_", c.stateKey);
        vm.serializeUint(o, "chainId", block.chainid);
        vm.serializeString(o, "section", c.section);
        vm.serializeString(o, "scenario", c.scenario);
        vm.serializeAddress(o, "game", address(c.game));
        vm.serializeAddress(o, "store", address(c.store));
        vm.serializeBytes32(o, "netlistHash2", c.nh2);
        vm.serializeUint(o, "claimId", c.claimId);
        vm.serializeAddress(o, "claimant", c.claimant);
        vm.serializeAddress(o, "challenger", c.challenger);
        vm.serializeAddress(o, "inputProvider", c.game.inputProvider());
        vm.serializeUint(o, "bond", c.bond);
        vm.serializeUint(o, "windowEnd", cl.windowEnd);
        vm.serializeBytes32(o, "checkpointRoot", cl.checkpointRoot);
        string memory s = vm.serializeString(o, "status", status);
        if (!vm.exists(c.statePath)) {
            string memory root = "root";
            vm.writeJson(vm.serializeUint(root, "chainId", block.chainid), c.statePath);
        }
        vm.writeJson(s, c.statePath, string.concat(".", c.stateKey));
        console.log("state written to", c.statePath);
    }

    function _checkChain() internal view {
        uint256 id = block.chainid;
        bool mainnet = id == 1 || id == 56 || id == 196 || id == 8453;
        if (mainnet && !vm.envOr("ALLOW_MAINNET", false)) {
            revert("RunInferenceDispute: mainnet chain id; this package is for testnets");
        }
    }

    function _eq(string memory a, string memory b) internal pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }
}
