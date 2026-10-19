// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, Vm} from "forge-std/Test.sol";
import {ITapLargeNetlistStore} from "../src/ITapLargeNetlistStore.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../src/TapLargeHash.sol";
import {Upload} from "./utils/Upload.sol";

/// Exposes the number of chunks a verifySegment call fetched from the oracle (compared with `chunksRead`).
contract VerifierHarness is TapLargeVerifier {
    constructor(ITapLargeNetlistStore s) TapLargeVerifier(s, 6000, 256, 16) {}

    function verifySegmentReads(
        bytes32 nh2,
        bytes32 stateRoot,
        SegTuple calldata a,
        SegTuple calldata b,
        bytes calldata witness,
        Budget calldata budget
    ) external view returns (bool, uint256) {
        return _verifySegment(nh2, stateRoot, a, b, witness, budget);
    }
}

/// Replays every verify_segment, beat-end and store vector of assets/tap-draft-large-netlists/vectors.json and asserts the same
/// accept/reject as the Python reference.
contract VectorsTest is Test {
    bytes32 internal constant VECTORS_SHA256 = 0x902afb750facf46f389d4ca20463cf23a004c98d319c4294c4ab73205b096015;
    string internal json;

    TapLargeNetlistStoreOwned internal storeFile;
    TapLargeNetlistStoreOwned internal storeNone;
    TapLargeNetlistStoreOwned internal storeChunk0;
    VerifierHarness internal vFile;
    VerifierHarness internal vNone;
    VerifierHarness internal vChunk0;

    string[4] internal names = ["sbox", "mul4", "message", "large"];
    mapping(bytes32 => bytes32) internal nh2Of; // keccak(name) => netlistHash2

    function setUp() public {
        json = vm.readFile("../assets/tap-draft-large-netlists/vectors.json");
        storeFile = new TapLargeNetlistStoreOwned(address(this));
        storeNone = new TapLargeNetlistStoreOwned(address(this));
        storeChunk0 = new TapLargeNetlistStoreOwned(address(this));
        for (uint256 i; i < 4; ++i) {
            bytes memory file = _file(names[i]);
            bytes32 h = Upload.upload(storeFile, file, true, 0);
            Upload.upload(storeChunk0, file, false, 0);
            nh2Of[keccak256(bytes(names[i]))] = h;
        }
        vFile = new VerifierHarness(storeFile);
        vNone = new VerifierHarness(storeNone);
        vChunk0 = new VerifierHarness(storeChunk0);
    }

    // ------------------------------------------------------------------ helpers

    function _file(string memory name) internal view returns (bytes memory) {
        return vm.parseBytes(string.concat("0x", vm.parseJsonString(json, string.concat(".files.", name, ".hex"))));
    }

    function _u(string memory key) internal view returns (uint256) {
        return vm.parseJsonUint(json, key);
    }

    function _b32(string memory key) internal view returns (bytes32) {
        return vm.parseJsonBytes32(json, key);
    }

    function _str(string memory key) internal view returns (string memory) {
        return vm.parseJsonString(json, key);
    }

    function _hex(string memory key) internal view returns (bytes memory) {
        return vm.parseBytes(string.concat("0x", vm.parseJsonString(json, key)));
    }

    function _seg(string memory key) internal view returns (TapLargeVerifier.SegTuple memory t) {
        t.byteOffset = uint64(_u(string.concat(key, "[0]")));
        t.firstSignal = uint32(_u(string.concat(key, "[1]")));
        t.latchOrdinal = uint32(_u(string.concat(key, "[2]")));
        t.p = _b32(string.concat(key, "[3]"));
        t.d = _b32(string.concat(key, "[4]"));
    }

    function _end(string memory key) internal view returns (TapLargeVerifier.EndTuple memory t) {
        t.ordinal = uint32(_u(string.concat(key, "[0]")));
        t.root = _b32(string.concat(key, "[1]"));
    }

    function _verifier(string memory oracle) internal view returns (VerifierHarness) {
        bytes32 o = keccak256(bytes(oracle));
        if (o == keccak256("file")) return vFile;
        if (o == keccak256("none")) return vNone;
        if (o == keccak256("chunk0")) return vChunk0;
        revert("oracle");
    }

    function _eq(string memory a, string memory b) internal pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }

    // ------------------------------------------------------------------ tests

    function test_vectorsFileHash() public view {
        assertEq(sha256(bytes(json)), VECTORS_SHA256, "vectors.json SHA-256");
    }

    function test_files() public view {
        for (uint256 i; i < 4; ++i) {
            string memory k = string.concat(".files.", names[i]);
            bytes memory file = _file(names[i]);
            (bytes32 nh2, bytes32 root) = Upload.hashOf(file);
            assertEq(nh2, _b32(string.concat(k, ".netlistHash2")), names[i]);
            assertEq(root, _b32(string.concat(k, ".chunkRoot")), names[i]);
            assertEq(file.length, _u(string.concat(k, ".metrics.bytes")));
            bytes32[] memory lv = Upload.leaves(file);
            bytes32[] memory want = vm.parseJsonBytes32Array(json, string.concat(k, ".chunkLeaves"));
            assertEq(lv.length, want.length);
            for (uint256 j; j < lv.length; ++j) assertEq(lv[j], want[j]);
            (uint64 len, uint32 nIn, uint32 nOut, uint32 chunks, bool stored) = storeFile.largeInfo(nh2);
            assertEq(len, file.length);
            assertEq(nIn, _u(string.concat(k, ".metrics.nIn")));
            assertEq(nOut, _u(string.concat(k, ".metrics.nOut")));
            assertEq(chunks, lv.length);
            assertTrue(stored);
        }
    }

    function test_chunkProofsOfVectors() public {
        // the chunk proofs published in the vectors are accepted by putChunk
        bytes memory file = _file("large");
        TapLargeNetlistStoreOwned st = new TapLargeNetlistStoreOwned(address(this));
        bytes32 h = _b32(".files.large.netlistHash2");
        st.addKnown(h);
        st.beginLarge(h, uint64(file.length), _b32(".files.large.chunkRoot"));
        uint256 n = _u(".files.large.chunks");
        for (uint256 i; i < n; ++i) {
            bytes32[] memory p =
                vm.parseJsonBytes32Array(json, string.concat(".files.large.chunkProofs[", vm.toString(i), "]"));
            st.putChunk(h, uint32(i), Upload.chunk(file, i), p);
        }
        assertTrue(st.largeStored(h));
    }

    function test_verifySegmentVectors() public view {
        uint256 total;
        uint256 positives;
        for (uint256 i;; ++i) {
            string memory k = string.concat(".verifySegment[", vm.toString(i), "]");
            if (!vm.keyExistsJson(json, k)) break;
            TapLargeVerifier.Budget memory bud = TapLargeVerifier.Budget(
                uint32(_u(string.concat(k, ".budgetUsed.gates"))),
                uint32(_u(string.concat(k, ".budgetUsed.ext_words"))),
                uint32(_u(string.concat(k, ".budgetUsed.chunks")))
            );
            bool expect = vm.parseJsonBool(json, string.concat(k, ".expect"));
            (bool got, uint256 reads) = _verifier(_str(string.concat(k, ".oracle"))).verifySegmentReads(
                nh2Of[keccak256(bytes(_str(string.concat(k, ".example"))))],
                _b32(string.concat(k, ".stateRoot")),
                _seg(string.concat(k, ".a")),
                _seg(string.concat(k, ".b")),
                _hex(string.concat(k, ".witnessHex")),
                bud
            );
            assertEq(got, expect, string.concat("verifySegment case ", vm.toString(i), ": ", _str(string.concat(k, ".case"))));
            if (expect) {
                if (vm.keyExistsJson(json, string.concat(k, ".chunksRead"))) {
                    assertEq(reads, _u(string.concat(k, ".chunksRead")), string.concat("chunksRead of case ", vm.toString(i)));
                }
                ++positives;
            }
            ++total;
        }
        assertEq(total, 79);
        assertEq(positives, 27);
    }

    function test_verifyBeatEndVectors() public view {
        uint256 total;
        uint256 positives;
        for (uint256 i;; ++i) {
            string memory k = string.concat(".verifyBeatEnd[", vm.toString(i), "]");
            if (!vm.keyExistsJson(json, k)) break;
            bool expect = vm.parseJsonBool(json, string.concat(k, ".expect"));
            bool got = _verifier(_str(string.concat(k, ".oracle"))).verifyBeatEnd(
                nh2Of[keccak256(bytes(_str(string.concat(k, ".example"))))],
                uint32(_u(string.concat(k, ".nOut"))),
                _seg(string.concat(k, ".segM")),
                _end(string.concat(k, ".a")),
                _end(string.concat(k, ".b")),
                _hex(string.concat(k, ".witnessHex"))
            );
            assertEq(got, expect, string.concat("verifyBeatEnd case ", vm.toString(i), ": ", _str(string.concat(k, ".case"))));
            if (expect) ++positives;
            ++total;
        }
        assertEq(total, 44);
        assertEq(positives, 6);
    }

    function _errSel(string memory name) internal pure returns (bytes4) {
        if (_eq(name, "UnknownNetlist")) return ITapLargeNetlistStore.UnknownNetlist.selector;
        if (_eq(name, "BadLength")) return ITapLargeNetlistStore.BadLength.selector;
        if (_eq(name, "HashMismatch")) return ITapLargeNetlistStore.HashMismatch.selector;
        if (_eq(name, "NotBegun")) return ITapLargeNetlistStore.NotBegun.selector;
        if (_eq(name, "BadChunk")) return ITapLargeNetlistStore.BadChunk.selector;
        if (_eq(name, "ChunkNotStored")) return ITapLargeNetlistStore.ChunkNotStored.selector;
        revert("unknown error name");
    }

    function test_storeBeginLargeVectors() public {
        uint256 n;
        for (uint256 i;; ++i) {
            string memory k = string.concat(".store.beginLarge[", vm.toString(i), "]");
            if (!vm.keyExistsJson(json, k)) break;
            TapLargeNetlistStoreOwned st = new TapLargeNetlistStoreOwned(address(this));
            bytes32 h = _b32(string.concat(k, ".netlistHash2"));
            uint64 len = uint64(_u(string.concat(k, ".length")));
            bytes32 root = _b32(string.concat(k, ".chunkRoot"));
            if (vm.parseJsonBool(json, string.concat(k, ".known"))) st.addKnown(h);
            string memory expect = _str(string.concat(k, ".expect"));
            if (_eq(expect, "ok")) {
                vm.expectEmit(true, false, false, true, address(st));
                emit ITapLargeNetlistStore.LargeBegun(h, len, root);
                st.beginLarge(h, len, root);
                // repeated call: no effect, no event
                vm.recordLogs();
                st.beginLarge(h, len, root);
                assertEq(vm.getRecordedLogs().length, 0);
            } else {
                vm.expectPartialRevert(_errSel(expect));
                st.beginLarge(h, len, root);
            }
            if (vm.keyExistsJson(json, string.concat(k, ".largeInfo"))) {
                (uint64 l, uint32 nIn, uint32 nOut, uint32 chunks, bool stored) = st.largeInfo(h);
                assertEq(l, _u(string.concat(k, ".largeInfo[0]")));
                assertEq(nIn, _u(string.concat(k, ".largeInfo[1]")));
                assertEq(nOut, _u(string.concat(k, ".largeInfo[2]")));
                assertEq(chunks, _u(string.concat(k, ".largeInfo[3]")));
                assertEq(stored, vm.parseJsonBool(json, string.concat(k, ".largeInfo[4]")));
            }
            ++n;
        }
        assertEq(n, 6);
    }

    function test_storeUploadVector() public {
        bytes memory file = _file("large");
        bytes32 h = _b32(".store.upload.netlistHash2");
        bytes32[][] memory lvs = Upload.levels(Upload.leaves(file));
        bytes32 root = lvs[lvs.length - 1][0];
        TapLargeNetlistStoreOwned st = new TapLargeNetlistStoreOwned(address(this));
        st.addKnown(h);
        // putChunk before beginLarge
        assertEq(_str(".store.upload.putChunkBeforeBegin"), "NotBegun");
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.NotBegun.selector, h));
        st.putChunk(h, 0, Upload.chunk(file, 0), Upload.proof(lvs, 0));
        // largeChunk of a chunk never stored
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.ChunkNotStored.selector, uint32(0)));
        st.largeChunk(h, 0);

        vm.recordLogs();
        st.beginLarge(h, uint64(file.length), root);
        uint256[] memory order = vm.parseJsonUintArray(json, ".store.upload.order");
        for (uint256 j; j < order.length; ++j) {
            st.putChunk(h, uint32(order[j]), Upload.chunk(file, order[j]), Upload.proof(lvs, order[j]));
            string memory k = string.concat(".store.upload.largeInfoAfterEach[", vm.toString(j), "]");
            (uint64 l, uint32 nIn, uint32 nOut, uint32 chunks, bool stored) = st.largeInfo(h);
            assertEq(l, _u(string.concat(k, "[0]")));
            assertEq(nIn, _u(string.concat(k, "[1]")));
            assertEq(nOut, _u(string.concat(k, "[2]")));
            assertEq(chunks, _u(string.concat(k, "[3]")));
            assertEq(stored, vm.parseJsonBool(json, string.concat(k, "[4]")));
            assertEq(st.largeStored(h), stored);
        }
        // the event list, exactly
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 nev;
        for (uint256 i;; ++i) {
            string memory k = string.concat(".store.upload.events[", vm.toString(i), "]");
            if (!vm.keyExistsJson(json, k)) break;
            string memory name = _str(string.concat(k, "[0]"));
            Vm.Log memory lg = logs[i];
            assertEq(lg.emitter, address(st));
            assertEq(lg.topics[1], h);
            if (_eq(name, "LargeBegun")) {
                assertEq(lg.topics[0], ITapLargeNetlistStore.LargeBegun.selector);
                assertEq(lg.data, abi.encode(uint64(_u(string.concat(k, "[1][1]"))), _b32(string.concat(k, "[1][2]"))));
            } else if (_eq(name, "ChunkStored")) {
                assertEq(lg.topics[0], ITapLargeNetlistStore.ChunkStored.selector);
                assertEq(lg.data, abi.encode(uint32(_u(string.concat(k, "[1][1]")))));
            } else {
                assertEq(name, "LargeStored");
                assertEq(lg.topics[0], ITapLargeNetlistStore.LargeStored.selector);
                assertEq(lg.data.length, 0);
            }
            ++nev;
        }
        assertEq(nev, logs.length, "no other events (the duplicate chunk 0 emits nothing)");
        // every chunk reads back exactly as accepted
        for (uint256 i; i < Upload.nChunks(file.length); ++i) assertEq(st.largeChunk(h, uint32(i)), Upload.chunk(file, i));
        // tampered chunk
        assertEq(_str(".store.upload.tamperedChunk"), "BadChunk");
        bytes memory bad = Upload.chunk(file, 1);
        bad[bad.length - 1] ^= 0x01;
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadChunk.selector, uint32(1)));
        st.putChunk(h, 1, bad, Upload.proof(lvs, 1));
    }

    function test_checkpointVectors() public view {
        uint256 n;
        while (vm.keyExistsJson(json, string.concat(".checkpoints.leaves[", vm.toString(n), "]"))) ++n;
        bytes32[] memory leaves = new bytes32[](n);
        for (uint256 i; i < n; ++i) {
            string memory k = string.concat(".checkpoints.leaves[", vm.toString(i), "]");
            leaves[i] = TapLargeHash.checkpointLeaf(
                uint64(_u(string.concat(k, ".t"))), _b32(string.concat(k, ".stateRoot")), _b32(string.concat(k, ".ioHash"))
            );
            assertEq(leaves[i], _b32(string.concat(k, ".leaf")));
        }
        assertEq(leaves[0], _b32(".checkpoints.initialLeaf"));
        assertEq(TapLargeHash.merkleRoot(leaves), _b32(".checkpoints.checkpointRoot"));
        bytes32[] memory il = vm.parseJsonBytes32Array(json, ".checkpoints.inputs.leaves");
        string[] memory packed = vm.parseJsonStringArray(json, ".checkpoints.inputs.packed");
        for (uint256 t; t < il.length; ++t) {
            assertEq(TapLargeHash.inputLeaf(uint64(t), vm.parseBytes(_hexPrefixed(packed[t]))), il[t]);
        }
        assertEq(TapLargeHash.merkleRoot(il), _b32(".checkpoints.inputs.inputRoot"));
    }

    function _hexPrefixed(string memory s) internal pure returns (string memory) {
        bytes memory b = bytes(s);
        if (b.length >= 2 && b[0] == "0" && b[1] == "x") return s;
        return string.concat("0x", s);
    }

    function test_interfaceIds() public view {
        assertEq(type(ITapLargeNetlistStore).interfaceId, bytes4(0x8230af7f));
        assertEq(ITapLargeNetlistStore.beginLarge.selector, bytes4(0xb0fc46a0));
        assertEq(ITapLargeNetlistStore.putChunk.selector, bytes4(0x806ca833));
        assertEq(ITapLargeNetlistStore.largeStored.selector, bytes4(0xe1a06945));
        assertEq(ITapLargeNetlistStore.largeChunk.selector, bytes4(0x75077164));
        assertEq(ITapLargeNetlistStore.largeInfo.selector, bytes4(0x260759cd));
        assertEq(TapLargeVerifier.segmentBudget.selector, bytes4(0x09683cea));
        assertEq(ITapLargeNetlistStore.UnknownNetlist.selector, bytes4(0x1dd49e73));
        assertEq(ITapLargeNetlistStore.BadLength.selector, bytes4(0xf55c3a0f));
        assertEq(ITapLargeNetlistStore.HashMismatch.selector, bytes4(0x7d7e4bfb));
        assertEq(ITapLargeNetlistStore.NotBegun.selector, bytes4(0xc7be356f));
        assertEq(ITapLargeNetlistStore.BadChunk.selector, bytes4(0xa46ef7e6));
        assertEq(ITapLargeNetlistStore.ChunkNotStored.selector, bytes4(0x23f6aba8));
        assertEq(ITapLargeNetlistStore.LargeBegun.selector, 0x9f1baa4bbf9155c50182876bbfc80a6ae68dc45e89869b743774f6a9148dcd0f);
        assertEq(ITapLargeNetlistStore.ChunkStored.selector, 0x0b105209a631d6f65bf6ca2cfee3327230af70d7ad1215e49d434cf751b7c7d8);
        assertEq(ITapLargeNetlistStore.LargeStored.selector, 0xbea4f9cc5ffc14f00d94052699882369f432531457ac3211008d81a630c3c573);
        assertTrue(storeFile.supportsInterface(0x8230af7f));
        assertTrue(storeFile.supportsInterface(0x01ffc9a7));
        assertFalse(storeFile.supportsInterface(0xffffffff));
    }
}
