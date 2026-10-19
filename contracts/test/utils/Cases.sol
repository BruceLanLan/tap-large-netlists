// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {TapLargeVerifier} from "../../src/TapLargeVerifier.sol";

/// @notice Reader of the binary case files written by test/data/binfmt.py.
library Cases {
    struct Reader {
        bytes data;
        uint256 p;
    }

    struct Case {
        uint8 kind; // 0 segment, 1 beat-end
        uint32 fileIdx;
        uint8 oracle;
        uint8 expect; // 0, 1, 2 = not asserted
        uint32 info;
        bytes32 stateRoot;
        TapLargeVerifier.SegTuple a; // segment: a; beat-end: segM
        TapLargeVerifier.SegTuple b;
        TapLargeVerifier.Budget budget;
        uint32 nOut;
        TapLargeVerifier.EndTuple ea;
        TapLargeVerifier.EndTuple eb;
        bytes witness;
        string name;
    }

    function uintN(Reader memory r, uint256 n) internal pure returns (uint256 v) {
        bytes memory d = r.data;
        uint256 p = r.p;
        require(p + n <= d.length, "cases: truncated");
        assembly {
            v := shr(sub(256, shl(3, n)), mload(add(add(d, 0x20), p)))
        }
        r.p = p + n;
    }

    function b32(Reader memory r) internal pure returns (bytes32 v) {
        v = bytes32(uintN(r, 32));
    }

    function bytesN(Reader memory r, uint256 n) internal pure returns (bytes memory out) {
        require(r.p + n <= r.data.length, "cases: truncated");
        out = new bytes(n);
        bytes memory d = r.data;
        uint256 p = r.p;
        for (uint256 k; k < n; k += 32) {
            assembly {
                mstore(add(add(out, 0x20), k), mload(add(add(add(d, 0x20), p), k)))
            }
        }
        assembly {
            mstore(add(add(out, 0x20), n), 0)
        }
        r.p = p + n;
    }

    function tuple(Reader memory r) internal pure returns (TapLargeVerifier.SegTuple memory t) {
        t.byteOffset = uint64(uintN(r, 8));
        t.firstSignal = uint32(uintN(r, 4));
        t.latchOrdinal = uint32(uintN(r, 4));
        t.p = b32(r);
        t.d = b32(r);
    }

    function open(bytes memory data) internal pure returns (Reader memory r, bytes[] memory files) {
        r = Reader(data, 0);
        require(uintN(r, 4) == 0x54444331, "cases: magic");
        uint256 nf = uintN(r, 4);
        files = new bytes[](nf);
        for (uint256 i; i < nf; ++i) files[i] = bytesN(r, uintN(r, 4));
    }

    function next(Reader memory r) internal pure returns (Case memory c) {
        c.kind = uint8(uintN(r, 1));
        c.fileIdx = uint32(uintN(r, 4));
        c.oracle = uint8(uintN(r, 1));
        c.expect = uint8(uintN(r, 1));
        c.info = uint32(uintN(r, 4));
        if (c.kind == 0) {
            c.stateRoot = b32(r);
            c.a = tuple(r);
            c.b = tuple(r);
            c.budget = TapLargeVerifier.Budget(uint32(uintN(r, 4)), uint32(uintN(r, 4)), uint32(uintN(r, 4)));
        } else {
            c.nOut = uint32(uintN(r, 4));
            c.a = tuple(r);
            c.ea = TapLargeVerifier.EndTuple(uint32(uintN(r, 4)), b32(r));
            c.eb = TapLargeVerifier.EndTuple(uint32(uintN(r, 4)), b32(r));
        }
        c.witness = bytesN(r, uintN(r, 4));
        c.name = string(bytesN(r, uintN(r, 2)));
    }
}
