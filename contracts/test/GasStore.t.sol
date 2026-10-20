// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {ITapLargeNetlistStore} from "../src/ITapLargeNetlistStore.sol";
import {TapLargeNetlistStoreOwned} from "../src/TapLargeNetlistStore.sol";
import {Upload} from "./utils/Upload.sol";

contract StoreMeter {
    function measure(address target, bytes calldata data) external returns (uint256 used, bool ok) {
        uint256 g0 = gasleft();
        (ok,) = target.call(data);
        used = g0 - gasleft();
    }
}

/// Gas of beginLarge and putChunk for a 456-chunk file of 7,466,723 bytes (the INT-C16 size estimate of the
/// draft's Motivation; random bytes, which the store does not parse). Each call is measured from a small frame
/// after vm.cool of the store, as the gas the CALL costs its caller (no intrinsic or calldata gas).
contract GasStoreTest is Test {
    uint256 internal constant LEN = 7466723;

    struct Stats {
        uint256 total;
        uint256 totalCd;
        uint256 maxGas;
        uint256 minGas;
        uint256 firstGas;
        uint256 lastGas;
        uint256 cdBytesFull;
    }

    function _dup(TapLargeNetlistStoreOwned st, StoreMeter m, bytes memory file, bytes32[][] memory lvs, bytes32 h)
        internal
        returns (uint256 g)
    {
        bytes memory dup =
            abi.encodeCall(ITapLargeNetlistStore.putChunk, (h, uint32(5), Upload.chunk(file, 5), Upload.proof(lvs, 5)));
        vm.cool(address(st));
        (g,) = m.measure(address(st), dup);
    }

    function _uploadAll(
        TapLargeNetlistStoreOwned st,
        StoreMeter m,
        bytes memory file,
        bytes32[][] memory lvs,
        bytes32 h,
        uint256 n
    ) internal returns (Stats memory t) {
        t.minGas = type(uint256).max;
        for (uint256 i; i < n; ++i) {
            bytes memory cd =
                abi.encodeCall(ITapLargeNetlistStore.putChunk, (h, uint32(i), Upload.chunk(file, i), Upload.proof(lvs, i)));
            vm.cool(address(st));
            (uint256 g, bool ok) = m.measure(address(st), cd);
            assertTrue(ok);
            t.total += g;
            t.totalCd += _cdGas(cd);
            if (i == 0) t.firstGas = g;
            if (i == n - 1) t.lastGas = g;
            if (i + 1 < n) {
                if (g > t.maxGas) t.maxGas = g;
                if (g < t.minGas) t.minGas = g;
                t.cdBytesFull = cd.length;
            }
        }
    }

    function _cdGas(bytes memory cd) internal pure returns (uint256 g) {
        for (uint256 i; i < cd.length; ++i) g += cd[i] == 0 ? 4 : 16;
    }

    function test_gasStore() public {
        bytes memory file = new bytes(LEN);
        // deterministic pseudo-random content (mostly nonzero bytes, as in a real netlist's calldata)
        bytes32 s = keccak256("tapnet/2 gas");
        for (uint256 i; i < LEN; i += 32) {
            s = keccak256(abi.encode(s));
            assembly {
                mstore(add(add(file, 0x20), i), s)
            }
        }
        assembly {
            mstore(add(add(file, 0x20), LEN), 0)
        }
        uint256 n = Upload.nChunks(LEN);
        assertEq(n, 456);
        bytes32[][] memory lvs = Upload.levels(Upload.leaves(file));
        bytes32 root = lvs[lvs.length - 1][0];
        (bytes32 h,) = Upload.hashOf(file);
        TapLargeNetlistStoreOwned st = new TapLargeNetlistStoreOwned(address(this));
        st.addKnown(h);
        StoreMeter m = new StoreMeter();
        vm.cool(address(st));
        (uint256 gBegin, bool ok0) =
            m.measure(address(st), abi.encodeCall(ITapLargeNetlistStore.beginLarge, (h, uint64(LEN), root)));
        assertTrue(ok0);
        console.log("beginLarge gas", gBegin);
        Stats memory t = _uploadAll(st, m, file, lvs, h, n);
        assertTrue(st.largeStored(h));
        // a duplicate chunk is accepted as a no-op (still verifies the proof)
        uint256 gDup = _dup(st, m, file, lvs, h);
        vm.cool(address(st));
        vm.cool(st.largeChunkPointer(h, 7));
        vm.cool(address(st));
        (uint256 gRead,) = m.measure(address(st), abi.encodeCall(ITapLargeNetlistStore.largeChunk, (h, uint32(7))));
        (uint256 gReadWarm,) = m.measure(address(st), abi.encodeCall(ITapLargeNetlistStore.largeChunk, (h, uint32(7))));
        console.log("largeChunk (16,384 B) cold / warm gas", gRead, gReadWarm);
        console.log("putChunk full chunk (16,384 B): min / max gas", t.minGas, t.maxGas);
        console.log("putChunk chunk 0 (records nIn/nOut) gas", t.firstGas);
        console.log("putChunk last chunk gas (bytes)", t.lastGas, LEN - 455 * 16384);
        console.log("putChunk duplicate (no-op) gas", gDup);
        console.log("putChunk calldata bytes (full chunk, 9-hash proof)", t.cdBytesFull);
        console.log("all 456 putChunk: execution gas", t.total);
        console.log("all 456 putChunk: calldata gas (16/4 per byte)", t.totalCd);
        console.log("all 456 putChunk: + 21000 per tx", t.total + t.totalCd + 456 * 21000);
        console.log("execution gas per stored byte (x100)", t.total * 100 / LEN);
    }

    /// The TAPBOY-8 cartridge (demo/cartridge/build): its upload with RAM ports (one chunk) and with LATCH-based RAM
    /// (build/legacy, seven chunks): beginLarge and every putChunk, execution gas, plus calldata and 21,000 per
    /// transaction.
    function test_gasCartridgeUpload() public {
        string[2] memory paths = ["../demo/cartridge/build/dodge.tapnet2", "../demo/cartridge/build/legacy/dodge.tapnet2"];
        for (uint256 k; k < 2; ++k) {
            bytes memory file = vm.readFileBinary(paths[k]);
            uint256 n = Upload.nChunks(file.length);
            bytes32[][] memory lvs = Upload.levels(Upload.leaves(file));
            (bytes32 h,) = Upload.hashOf(file);
            TapLargeNetlistStoreOwned st = new TapLargeNetlistStoreOwned(address(this));
            st.addKnown(h);
            StoreMeter m = new StoreMeter();
            bytes memory cd0 = abi.encodeCall(ITapLargeNetlistStore.beginLarge, (h, uint64(file.length), lvs[lvs.length - 1][0]));
            vm.cool(address(st));
            (uint256 g0, bool ok0) = m.measure(address(st), cd0);
            assertTrue(ok0);
            Stats memory t = _uploadAll(st, m, file, lvs, h, n);
            assertTrue(st.largeStored(h));
            console.log(paths[k]);
            console.log("    bytes %d, chunks %d", file.length, n);
            console.log("    execution gas: beginLarge %d, putChunk total %d", g0, t.total);
            console.log("    transactions incl. calldata and 21,000 each: %d", g0 + _cdGas(cd0) + t.total + t.totalCd + (n + 1) * 21000);
        }
    }
}
