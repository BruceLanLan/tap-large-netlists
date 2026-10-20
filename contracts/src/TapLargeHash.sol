// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

/// @title Hashes of the draft TAP "Large Netlists and Verifiable Execution" (§2.1, §4)
/// @notice SHA-256 with the one-byte domains of §4.1. Bit-identical to the Python reference `assets/tap-draft-large-netlists/tapnet2.py`.
///         Used by the store (chunk proofs) and by tests and games; the verifier has its own inlined copies.
library TapLargeHash {
    uint256 internal constant CHUNK = 16384;

    /// SHA-256(0x01 ‖ left ‖ right): node of every tree of the TAP (§0).
    function node(bytes32 left, bytes32 right) internal view returns (bytes32 h) {
        assembly ("memory-safe") {
            let p := mload(0x40)
            mstore8(p, 0x01)
            mstore(add(p, 1), left)
            mstore(add(p, 33), right)
            if iszero(staticcall(gas(), 0x02, p, 65, 0x00, 0x20)) { revert(0, 0) }
            h := mload(0x00)
        }
    }

    /// SHA-256(0x02 ‖ u32be(i) ‖ chunk_i).
    function chunkLeaf(uint256 i, bytes memory chunk) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x02), uint32(i), chunk));
    }

    /// netlistHash2 = SHA-256(0x03 ‖ u64be(len) ‖ chunkRoot).
    function netlistHash2(uint256 length, bytes32 chunkRoot) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x03), uint64(length), chunkRoot));
    }

    /// SHA-256(dom ‖ u32be(k) ‖ word): word leaf of a prefix tree (0x04 signals/outputs, 0x05 state, 0x09 d-table).
    function wordLeaf(uint8 dom, uint256 k, bytes32 word) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(dom, uint32(k), word));
    }

    /// segLeaf(j, seg) of §4.5.
    function segLeaf(uint256 j, uint64 byteOffset, uint32 firstSignal, uint32 latchOrdinal, bytes32 p, bytes32 d)
        internal
        pure
        returns (bytes32)
    {
        return sha256(abi.encodePacked(bytes2(0x0700), uint32(j), byteOffset, firstSignal, latchOrdinal, p, d));
    }

    /// segLeaf(j, seg) of §4.5 for a file with MEM: M is appended to the preimage (181 bytes).
    function segLeafM(
        uint256 j,
        uint64 byteOffset,
        uint32 firstSignal,
        uint32 latchOrdinal,
        bytes32 p,
        bytes32 d,
        bytes32 m
    ) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes2(0x0700), uint32(j), byteOffset, firstSignal, latchOrdinal, p, d, m));
    }

    /// segLeaf (hasMem = false) or segLeafM (hasMem = true) of §4.5, in one compact routine (used by the game).
    function segLeafOf(
        bool hasMem,
        uint256 j,
        uint64 byteOffset,
        uint32 firstSignal,
        uint32 latchOrdinal,
        bytes32 p,
        bytes32 d,
        bytes32 m
    ) internal view returns (bytes32 h) {
        assembly ("memory-safe") {
            let q := mload(0x40)
            // 0x07 ‖ 0x00 ‖ u32 j ‖ u64 byteOffset ‖ u32 firstSignal ‖ u32 latchOrdinal: 22 bytes
            let w := or(shl(240, 0x0700), shl(208, and(j, 0xffffffff)))
            w := or(w, shl(144, and(byteOffset, 0xffffffffffffffff)))
            w := or(w, or(shl(112, and(firstSignal, 0xffffffff)), shl(80, and(latchOrdinal, 0xffffffff))))
            mstore(q, w)
            mstore(add(q, 22), p)
            mstore(add(q, 54), d)
            mstore(add(q, 86), m)
            let n := 86
            if hasMem { n := 118 }
            if iszero(staticcall(gas(), 0x02, q, n, 0x00, 0x20)) { revert(0, 0) }
            h := mload(0x00)
        }
    }

    /// memLeaf(word) = SHA-256(0x0B ‖ word): leaf of the memory tree (§4.6), without an index.
    function memLeaf(bytes32 word) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x0b), word));
    }

    /// stateRoot of a file with MEM after beat 0: SHA-256(0x0C ‖ latchRoot ‖ memRoot) (§4.6).
    function memStateRoot(bytes32 latchRoot, bytes32 memRoot) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x0c), latchRoot, memRoot));
    }

    /// endLeaf(kind, k, (ordinal, root)) of §4.5, kind 1 (outputs) or 2 (state).
    function endLeaf(uint8 kind, uint256 k, uint32 ordinal, bytes32 root) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x07), kind, uint32(k), ordinal, root));
    }

    /// checkpointLeaf(t, stateRoot, io) of §7.1 (also the beat leaf of §7.3).
    function checkpointLeaf(uint64 t, bytes32 stateRoot, bytes32 io) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x06), t, stateRoot, io));
    }

    /// io_{t+1} = SHA-256(0x08 ‖ io_t ‖ outRoot_t).
    function ioNext(bytes32 io, bytes32 outRoot) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x08), io, outRoot));
    }

    /// inputLeaf(t) = SHA-256(0x0A ‖ u64be(t) ‖ SHA-256(packedInputs_t)).
    function inputLeaf(uint64 t, bytes memory packedInputs) internal pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x0a), t, sha256(packedInputs)));
    }

    /// Single-leaf proof of §0 (siblings bottom-up; an odd last node moves up without a sibling).
    function verify(bytes32 root, uint256 size, uint256 index, bytes32 leaf, bytes32[] calldata proof)
        internal
        view
        returns (bool)
    {
        if (index >= size) return false;
        bytes32 h = leaf;
        uint256 p;
        uint256 len = proof.length;
        for (uint256 width = size; width > 1; width = (width + 1) >> 1) {
            if (index & 1 == 1) {
                if (p >= len) return false;
                h = node(proof[p++], h);
            } else if (index + 1 < width) {
                if (p >= len) return false;
                h = node(h, proof[p++]);
            }
            index >>= 1;
        }
        return p == len && h == root;
    }

    /// Root of tree(leaves) (§0); leaves must be non-empty.
    function merkleRoot(bytes32[] memory leaves) internal view returns (bytes32) {
        uint256 n = leaves.length;
        require(n > 0, "empty tree");
        bytes32[] memory lv = new bytes32[](n);
        for (uint256 i; i < n; ++i) lv[i] = leaves[i];
        while (n > 1) {
            uint256 m;
            for (uint256 i; i + 1 < n; i += 2) lv[m++] = node(lv[i], lv[i + 1]);
            if (n & 1 == 1) lv[m++] = lv[n - 1];
            n = m;
        }
        return lv[0];
    }
}
