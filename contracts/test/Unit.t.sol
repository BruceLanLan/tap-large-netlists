// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, Vm} from "forge-std/Test.sol";
import {ITapLargeNetlistStore} from "../src/ITapLargeNetlistStore.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {TapLargeVerifier} from "../src/TapLargeVerifier.sol";
import {TapLargeHash} from "../src/TapLargeHash.sol";
import {Upload} from "./utils/Upload.sol";

/// A chunk oracle with configurable answers (to exercise the oracle checks of §6).
contract MockOracle {
    uint64 public len;
    bool public stored;
    bytes public chunk0;

    function set(uint64 l, bool s, bytes calldata c) external {
        len = l;
        stored = s;
        chunk0 = c;
    }

    function largeInfo(bytes32) external view returns (uint64, uint32, uint32, uint32, bool) {
        return (len, 0, 0, 1, stored);
    }

    function largeChunk(bytes32, uint32) external view returns (bytes memory) {
        return chunk0;
    }
}

contract UnitTest is Test {
    bytes32 internal constant ZM = 0x073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff; // ZERO_MEM
    TapLargeNetlistStoreOwned internal st;

    function setUp() public {
        st = new TapLargeNetlistStoreOwned(address(this));
    }

    /// A 23-byte file: nIn = 1, nOut = 1, one NAND(2, 2).
    function _file() internal pure returns (bytes memory) {
        return bytes.concat(bytes16(0x5441504e455402000000000100000001), hex"00000002000002");
    }

    // ---------------------------------------------------------------- store

    function test_onlyOwnerAddsKnown() public {
        vm.prank(address(0xbeef));
        vm.expectRevert(TapLargeNetlistStoreOwned.NotOwner.selector);
        st.addKnown(bytes32(uint256(1)));
    }

    function test_beginLargeChecks() public {
        bytes32 root = keccak256("root");
        bytes32 h = TapLargeHash.netlistHash2(100, root);
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.UnknownNetlist.selector, h));
        st.beginLarge(h, 100, root);
        st.addKnown(h);
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.HashMismatch.selector, h));
        st.beginLarge(h, 100, keccak256("other"));
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadLength.selector, uint64(16)));
        st.beginLarge(h, 16, root);
        st.beginLarge(h, 100, root);
        (uint64 l,,, uint32 c, bool s) = st.largeInfo(h);
        assertEq(l, 100);
        assertEq(c, 0);
        assertFalse(s);
    }

    function test_putChunkChecks() public {
        bytes memory file = new bytes(40000); // 3 chunks; content is not parsed by the store
        for (uint256 i; i < file.length; ++i) file[i] = bytes1(uint8(i * 7 + 1));
        file[8] = 0x00;
        file[9] = 0x00;
        file[10] = 0x00;
        file[11] = 0x05; // nIn = 5
        file[15] = 0x09; // nOut = 9 (bytes 12..14 are nonzero filler: nOut = u32be of bytes 12..15)
        bytes32[][] memory lvs = Upload.levels(Upload.leaves(file));
        bytes32 root = lvs[lvs.length - 1][0];
        bytes32 h = TapLargeHash.netlistHash2(file.length, root);
        st.addKnown(h);
        st.beginLarge(h, uint64(file.length), root);
        // index out of range, wrong length, wrong proof, proof for another index
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadChunk.selector, uint32(3)));
        st.putChunk(h, 3, Upload.chunk(file, 2), Upload.proof(lvs, 2));
        bytes memory shortC = Upload.chunk(file, 1);
        assembly {
            mstore(shortC, sub(mload(shortC), 1))
        }
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadChunk.selector, uint32(1)));
        st.putChunk(h, 1, shortC, Upload.proof(lvs, 1));
        bytes32[] memory p = Upload.proof(lvs, 1);
        p[0] = bytes32(uint256(p[0]) ^ 1);
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadChunk.selector, uint32(1)));
        st.putChunk(h, 1, Upload.chunk(file, 1), p);
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.BadChunk.selector, uint32(1)));
        st.putChunk(h, 1, Upload.chunk(file, 1), Upload.proof(lvs, 0));
        // nIn / nOut only once chunk 0 is stored
        st.putChunk(h, 2, Upload.chunk(file, 2), Upload.proof(lvs, 2));
        (, uint32 nIn, uint32 nOut,,) = st.largeInfo(h);
        assertEq(nIn, 0);
        assertEq(nOut, 0);
        st.putChunk(h, 0, Upload.chunk(file, 0), Upload.proof(lvs, 0));
        (, nIn, nOut,,) = st.largeInfo(h);
        assertEq(nIn, 5);
        assertEq(nOut, uint32(bytes4(bytes.concat(file[12], file[13], file[14], file[15]))));
        // duplicate: no event, no change
        vm.recordLogs();
        st.putChunk(h, 0, Upload.chunk(file, 0), Upload.proof(lvs, 0));
        assertEq(vm.getRecordedLogs().length, 0);
        (,,, uint32 chunks, bool stored) = st.largeInfo(h);
        assertEq(chunks, 2);
        assertFalse(stored);
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.ChunkNotStored.selector, uint32(1)));
        st.largeChunk(h, 1);
        st.putChunk(h, 1, Upload.chunk(file, 1), Upload.proof(lvs, 1));
        assertTrue(st.largeStored(h));
        assertEq(st.largeChunk(h, 2), Upload.chunk(file, 2));
        vm.expectRevert(abi.encodeWithSelector(ITapLargeNetlistStore.ChunkNotStored.selector, uint32(0)));
        st.largeChunk(keccak256("never begun"), 0);
    }

    // ---------------------------------------------------------------- verifier

    function test_budgetDefaults() public {
        TapLargeVerifier v = new TapLargeVerifier(st, 6000, 256, 16);
        (uint32 g, uint32 e, uint32 c) = v.segmentBudget();
        assertEq(g, v.DEFAULT_GATES());
        assertEq(e, v.DEFAULT_EXT_WORDS());
        assertEq(c, v.DEFAULT_CHUNKS());
        assertEq(address(v.store()), address(st));
    }

    /// The one segment of the 23-byte file, beat with input 1: V = (0, 1, 1, NAND(1, 1) = 0).
    function _case()
        internal
        pure
        returns (TapLargeVerifier.SegTuple memory a, TapLargeVerifier.SegTuple memory b, bytes memory w)
    {
        bytes32 word = bytes32(uint256(0x06) << 248); // bits 1 and 2
        a = TapLargeVerifier.SegTuple(16, 3, 0, TapLargeHash.wordLeaf(0x04, 0, word), bytes32(0), ZM);
        b = TapLargeVerifier.SegTuple(23, 4, 0, TapLargeHash.wordLeaf(0x04, 0, word), bytes32(0), ZM);
        // frontier(P): 0 peaks, partial word; words(P) 0; hashes 0; frontier(D): 0 peaks, no partial;
        // stateSize 0; words 0; hashes 0
        w = bytes.concat(hex"0001", word, hex"0000", hex"0000", hex"0000", hex"00000000", hex"0000", hex"0000");
    }

    function test_verifySegmentSmallFile() public {
        bytes memory file = _file();
        bytes32 h = Upload.upload(st, file, true, 0);
        TapLargeVerifier v = new TapLargeVerifier(st, 6000, 256, 16);
        (TapLargeVerifier.SegTuple memory a, TapLargeVerifier.SegTuple memory b, bytes memory w) = _case();
        TapLargeVerifier.Budget memory bud = TapLargeVerifier.Budget(6000, 256, 16);
        assertTrue(v.verifySegment(h, bytes32(0), ZM, a, b, w, bud));
        // wrong claimed value of signal 3
        b.p = TapLargeHash.wordLeaf(0x04, 0, bytes32(uint256(0x0e) << 248));
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, w, bud));
        // byteOffset below the header / at the end of the file
        (a, b,) = _case();
        a.byteOffset = 15;
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, w, bud));
        a.byteOffset = 23;
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, w, bud));
        // not stored
        (a, b,) = _case();
        assertFalse(v.verifySegment(keccak256("unknown"), bytes32(0), ZM, a, b, w, bud));
    }

    function test_oracleChecks() public {
        MockOracle o = new MockOracle();
        TapLargeVerifier v = new TapLargeVerifier(ITapLargeNetlistStore(address(o)), 6000, 256, 16);
        (TapLargeVerifier.SegTuple memory a, TapLargeVerifier.SegTuple memory b, bytes memory w) = _case();
        TapLargeVerifier.Budget memory bud = TapLargeVerifier.Budget(6000, 256, 16);
        bytes memory file = _file();
        o.set(23, true, file);
        assertTrue(v.verifySegment(bytes32(0), bytes32(0), ZM, a, b, w, bud));
        o.set(23, false, file); // largeStored false
        assertFalse(v.verifySegment(bytes32(0), bytes32(0), ZM, a, b, w, bud));
        o.set(67108865, true, file); // fileLen above 64 MiB
        assertFalse(v.verifySegment(bytes32(0), bytes32(0), ZM, a, b, w, bud));
        o.set(23, true, bytes.concat(file, hex"00")); // chunk of the wrong length
        assertFalse(v.verifySegment(bytes32(0), bytes32(0), ZM, a, b, w, bud));
        o.set(24, true, bytes.concat(file, hex"00")); // a longer file: the record after the NAND is truncated
        assertFalse(v.verifySegment(bytes32(0), bytes32(0), ZM, a, b, w, bud));
    }

    function test_witnessEncodingFailures() public {
        bytes32 h = Upload.upload(st, _file(), true, 0);
        TapLargeVerifier v = new TapLargeVerifier(st, 6000, 256, 16);
        (TapLargeVerifier.SegTuple memory a, TapLargeVerifier.SegTuple memory b, bytes memory w) = _case();
        TapLargeVerifier.Budget memory bud = TapLargeVerifier.Budget(6000, 256, 16);
        bytes memory bad = bytes.concat(w, hex"00"); // trailing byte
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, bad, bud));
        bad = bytes.concat(w);
        bad[1] = 0x02; // flag other than 0 or 1
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, bad, bud));
        bad = new bytes(w.length - 1); // truncated
        for (uint256 i; i < bad.length; ++i) bad[i] = w[i];
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, bad, bud));
        // EMPTY stateRoot with a nonzero stateSize
        bad = bytes.concat(w);
        bad[bad.length - 5] = 0x01;
        assertFalse(v.verifySegment(h, bytes32(0), ZM, a, b, bad, bud));
    }
}
