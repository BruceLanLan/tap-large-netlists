// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Script} from "forge-std/Script.sol";
import {console2 as console} from "forge-std/console2.sol";
import {ITapLargeNetlistStore} from "../src/ITapLargeNetlistStore.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {TapLargeDisputeGame} from "../src/game/TapLargeDisputeGame.sol";

/// @title Test deployment of the TAP-7007 reference contracts (tapnet/2 store, one-step verifier, dispute game)
/// @notice Deploys TapLargeNetlistStoreOwned(deployer), TapLargeVerifier(store, 6000, 256, 16) (the RECOMMENDED
///         budget of §5.1), the external library TapLargeGameLib (forge deploys and links it) and
///         TapLargeDisputeGame with case-(a) parameters, then uploads the `message` example file of
///         assets/tap-draft-large-netlists/vectors.json (336 bytes, one chunk) so largeStored/largeInfo/largeChunk
///         answer. With UPLOAD_LARGE=true it also uploads the `large` example (89,509 bytes, 6 chunks, proofs from
///         the vectors), which is what a chunk-tree check needs.
///
///         Environment: PRIVATE_KEY (required, test key); optional BOND (wei), CHALLENGE_WINDOW, MOVE_TIME,
///         PER_BEAT_TIME (seconds), MAX_K, INPUT_PROVIDER, BUDGET_GATES, BUDGET_EXT_WORDS, BUDGET_CHUNKS,
///         UPLOAD_LARGE, ALLOW_MAINNET.
///         Writes deployments/<chainId>.json (section "largeNetlists").
///
///         forge script script/DeployLargeNetlists.s.sol --rpc-url "$RPC_URL" --broadcast
contract DeployLargeNetlists is Script {
    uint256 internal constant ANVIL = 31337;
    uint256 internal constant XLAYER_TESTNET = 1952;
    uint256 internal constant BSC_TESTNET = 97;
    string internal constant VECTORS = "../assets/tap-draft-large-netlists/vectors.json";
    uint256 internal constant CHUNK = 16384;

    struct Params {
        uint256 bond;
        uint64 challengeWindow;
        uint64 moveTime;
        uint64 perBeatTime;
        uint64 maxK;
        uint64 maxT;
        address inputProvider;
    }

    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(key);
        _checkChain();
        console.log("chain id      ", block.chainid);
        console.log("deployer      ", deployer);
        console.log("balance (wei) ", deployer.balance);

        Params memory p;
        p.bond = vm.envOr("BOND", uint256(0.01 ether));
        p.challengeWindow = uint64(vm.envOr("CHALLENGE_WINDOW", uint256(1 days)));
        p.moveTime = uint64(vm.envOr("MOVE_TIME", uint256(1 hours)));
        p.perBeatTime = uint64(vm.envOr("PER_BEAT_TIME", uint256(60)));
        p.maxK = uint64(vm.envOr("MAX_K", uint256(64)));
        p.maxT = p.maxK * 2047; // the §7.1 bound with at most 2,048 checkpoint leaves
        p.inputProvider = vm.envOr("INPUT_PROVIDER", address(0));
        bool uploadLarge = vm.envOr("UPLOAD_LARGE", false);

        string memory json = vm.readFile(VECTORS);

        vm.startBroadcast(key);

        TapLargeNetlistStoreOwned store = new TapLargeNetlistStoreOwned(deployer);
        // the RECOMMENDED budget of §5.1 (TapLargeVerifier.DEFAULT_*): (6000, 256, 16)
        TapLargeVerifier verifier = new TapLargeVerifier(
            store,
            uint32(vm.envOr("BUDGET_GATES", uint256(6000))),
            uint32(vm.envOr("BUDGET_EXT_WORDS", uint256(256))),
            uint32(vm.envOr("BUDGET_CHUNKS", uint256(16)))
        );
        TapLargeDisputeGame game = new TapLargeDisputeGame(
            TapLargeDisputeGame.Params({
                store: store,
                verifier: verifier,
                bond: p.bond,
                challengeWindow: p.challengeWindow,
                moveTime: p.moveTime,
                perBeatTime: p.perBeatTime,
                maxK: p.maxK,
                maxT: p.maxT,
                inputProvider: p.inputProvider
            })
        );

        bytes32 messageHash = _upload(store, json, "message");
        bytes32 largeHash;
        if (uploadLarge) largeHash = _upload(store, json, "large");

        vm.stopBroadcast();

        address gameLib = _linkedLibrary(address(game), address(store), address(verifier));
        _report(store, verifier, game, gameLib, p, messageHash, largeHash);
    }

    /// beginLarge + putChunk of every chunk of `.files.<name>` of the vectors, with the proofs recorded there
    /// (a one-chunk file has an empty proof). Returns netlistHash2.
    function _upload(TapLargeNetlistStoreOwned store, string memory json, string memory name)
        internal
        returns (bytes32 nh2)
    {
        string memory base = string.concat(".files.", name);
        bytes memory file = vm.parseBytes(string.concat("0x", vm.parseJsonString(json, string.concat(base, ".hex"))));
        nh2 = vm.parseJsonBytes32(json, string.concat(base, ".netlistHash2"));
        bytes32 root = vm.parseJsonBytes32(json, string.concat(base, ".chunkRoot"));
        uint256 n = (file.length + CHUNK - 1) / CHUNK;
        require(n == vm.parseJsonUint(json, string.concat(base, ".chunks")), "vectors: chunk count");

        store.addKnown(nh2);
        store.beginLarge(nh2, uint64(file.length), root);
        for (uint256 i; i < n; ++i) {
            bytes32[] memory proof;
            if (n > 1) {
                proof = vm.parseJsonBytes32Array(json, string.concat(base, ".chunkProofs[", vm.toString(i), "]"));
            }
            store.putChunk(nh2, uint32(i), _chunk(file, i), proof);
        }
        require(store.largeStored(nh2), "upload incomplete");
        console.log("uploaded", name, vm.toString(nh2));
    }

    function _chunk(bytes memory file, uint256 i) internal pure returns (bytes memory c) {
        uint256 start = i * CHUNK;
        uint256 end = start + CHUNK < file.length ? start + CHUNK : file.length;
        c = new bytes(end - start);
        for (uint256 k; k < c.length; ++k) {
            c[k] = file[start + k];
        }
    }

    /// The TapLargeGameLib address linked into the game: a PUSH20 immediate in its runtime code that holds code and
    /// is neither the game nor one of its immutable addresses (store, verifier).
    function _linkedLibrary(address game, address store, address verifier) internal view returns (address lib) {
        bytes memory code = game.code;
        for (uint256 i; i + 21 <= code.length; ++i) {
            if (code[i] != 0x73) continue; // PUSH20
            address a;
            assembly ("memory-safe") {
                a := shr(96, mload(add(add(code, 0x20), add(i, 1))))
            }
            if (a != address(0) && a != game && a != store && a != verifier && a.code.length > 0) return a;
        }
    }

    function _report(
        TapLargeNetlistStoreOwned store,
        TapLargeVerifier verifier,
        TapLargeDisputeGame game,
        address gameLib,
        Params memory p,
        bytes32 messageHash,
        bytes32 largeHash
    ) internal {
        (uint32 g, uint32 e, uint32 c) = verifier.segmentBudget();
        console.log("TapLargeNetlistStoreOwned", address(store));
        console.log("TapLargeVerifier         ", address(verifier));
        console.log("TapLargeGameLib          ", gameLib);
        console.log("TapLargeDisputeGame      ", address(game));
        console.log("segmentBudget            ", g, e, c);

        string memory obj = "largeNetlists";
        vm.serializeAddress(obj, "deployer", store.owner());
        vm.serializeUint(obj, "block", block.number);
        vm.serializeUint(obj, "timestamp", block.timestamp);
        vm.serializeAddress(obj, "TapLargeNetlistStoreOwned", address(store));
        vm.serializeAddress(obj, "TapLargeVerifier", address(verifier));
        vm.serializeAddress(obj, "TapLargeGameLib", gameLib);
        vm.serializeAddress(obj, "TapLargeDisputeGame", address(game));
        vm.serializeUint(obj, "budgetGates", g);
        vm.serializeUint(obj, "budgetExtWords", e);
        vm.serializeUint(obj, "budgetChunks", c);
        vm.serializeUint(obj, "bond", p.bond);
        vm.serializeUint(obj, "challengeWindow", p.challengeWindow);
        vm.serializeUint(obj, "moveTime", p.moveTime);
        vm.serializeUint(obj, "perBeatTime", p.perBeatTime);
        vm.serializeUint(obj, "maxK", p.maxK);
        vm.serializeUint(obj, "maxT", p.maxT);
        vm.serializeAddress(obj, "inputProvider", p.inputProvider);
        vm.serializeBytes32(obj, "messageNetlistHash2", messageHash);
        vm.serializeBytes32(obj, "largeNetlistHash2", largeHash);
        vm.serializeString(obj, "ITapLargeNetlistStore", _id(type(ITapLargeNetlistStore).interfaceId));
        vm.serializeString(obj, "verifySegmentSelector", _id(TapLargeVerifier.verifySegment.selector));
        vm.serializeString(obj, "verifyBeatEndSelector", _id(TapLargeVerifier.verifyBeatEnd.selector));
        string memory section =
            vm.serializeString(obj, "segmentBudgetSelector", _id(TapLargeVerifier.segmentBudget.selector));

        string memory path = string.concat("deployments/", vm.toString(block.chainid), ".json");
        if (!vm.exists(path)) {
            string memory root = "root";
            vm.serializeUint(root, "chainId", block.chainid);
            vm.writeJson(vm.serializeString(root, "chain", _chainName()), path);
        }
        vm.writeJson(section, path, ".largeNetlists");
        console.log("wrote section largeNetlists to", path);
    }

    function _id(bytes4 id) internal pure returns (string memory) {
        return vm.toString(abi.encodePacked(id));
    }

    function _checkChain() internal view {
        uint256 id = block.chainid;
        if (id == ANVIL || id == XLAYER_TESTNET || id == BSC_TESTNET) return;
        bool mainnet = id == 1 || id == 56 || id == 196 || id == 8453;
        if (mainnet && !vm.envOr("ALLOW_MAINNET", false)) {
            revert(
                "DeployLargeNetlists: mainnet chain id; this package is for testnets (set ALLOW_MAINNET=true to override)"
            );
        }
        console.log("WARNING: chain id is not one this package was written for; continuing");
    }

    function _chainName() internal view returns (string memory) {
        uint256 id = block.chainid;
        if (id == ANVIL) return "anvil";
        if (id == XLAYER_TESTNET) return "X Layer Testnet";
        if (id == BSC_TESTNET) return "BNB Smart Chain Testnet";
        return "unknown";
    }
}
