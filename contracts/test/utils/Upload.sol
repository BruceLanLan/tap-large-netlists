// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {TapLargeHash} from "../../src/TapLargeHash.sol";
import {TapLargeNetlistStoreOwned} from "../../src/TapLargeNetlistStore.sol";

/// @notice Test helpers: chunking, the chunk tree, single-leaf proofs (§0) and uploads.
library Upload {
    uint256 internal constant CHUNK = 16384;

    function nChunks(uint256 len) internal pure returns (uint256) {
        return (len + CHUNK - 1) / CHUNK;
    }

    function chunk(bytes memory file, uint256 i) internal pure returns (bytes memory c) {
        uint256 start = i * CHUNK;
        uint256 end = start + CHUNK < file.length ? start + CHUNK : file.length;
        c = new bytes(end - start);
        assembly {
            mcopy_(add(c, 0x20), add(add(file, 0x20), start), sub(end, start))
            function mcopy_(dst, src, n) {
                for { let k := 0 } lt(k, n) { k := add(k, 32) } { mstore(add(dst, k), mload(add(src, k))) }
            }
        }
        // the word loop may have written past the end of c; clear the tail of the last word
        assembly {
            let n := mload(c)
            mstore(add(add(c, 0x20), n), 0)
        }
    }

    function leaves(bytes memory file) internal pure returns (bytes32[] memory lv) {
        uint256 n = nChunks(file.length);
        lv = new bytes32[](n);
        for (uint256 i; i < n; ++i) lv[i] = TapLargeHash.chunkLeaf(i, chunk(file, i));
    }

    /// All levels of the tree (level 0 = leaves).
    function levels(bytes32[] memory lv) internal view returns (bytes32[][] memory out) {
        uint256 depth = 1;
        for (uint256 w = lv.length; w > 1; w = (w + 1) >> 1) ++depth;
        out = new bytes32[][](depth);
        out[0] = lv;
        for (uint256 d = 1; d < depth; ++d) {
            bytes32[] memory prev = out[d - 1];
            uint256 w = (prev.length + 1) >> 1;
            bytes32[] memory cur = new bytes32[](w);
            for (uint256 i; i + 1 < prev.length; i += 2) cur[i >> 1] = TapLargeHash.node(prev[i], prev[i + 1]);
            if (prev.length & 1 == 1) cur[w - 1] = prev[prev.length - 1];
            out[d] = cur;
        }
    }

    function proof(bytes32[][] memory lvs, uint256 index) internal pure returns (bytes32[] memory p) {
        bytes32[] memory tmp = new bytes32[](lvs.length);
        uint256 k;
        uint256 i = index;
        for (uint256 d; d + 1 < lvs.length; ++d) {
            uint256 sib = i ^ 1;
            if (sib < lvs[d].length) tmp[k++] = lvs[d][sib];
            i >>= 1;
        }
        p = new bytes32[](k);
        for (uint256 j; j < k; ++j) p[j] = tmp[j];
    }

    function hashOf(bytes memory file) internal view returns (bytes32 nh2, bytes32 root) {
        bytes32[][] memory lvs = levels(leaves(file));
        root = lvs[lvs.length - 1][0];
        nh2 = TapLargeHash.netlistHash2(file.length, root);
    }

    /// beginLarge + putChunk for the given chunk indices (all chunks if `all`).
    function upload(TapLargeNetlistStoreOwned store, bytes memory file, bool all, uint256 only)
        internal
        returns (bytes32 nh2)
    {
        bytes32[][] memory lvs = levels(leaves(file));
        bytes32 root = lvs[lvs.length - 1][0];
        nh2 = TapLargeHash.netlistHash2(file.length, root);
        store.addKnown(nh2);
        store.beginLarge(nh2, uint64(file.length), root);
        uint256 n = nChunks(file.length);
        for (uint256 i; i < n; ++i) {
            if (all || i == only) store.putChunk(nh2, uint32(i), chunk(file, i), proof(lvs, i));
        }
    }
}
