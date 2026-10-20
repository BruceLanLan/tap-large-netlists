// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {TapLargeHash} from "../TapLargeHash.sol";

/// @title Tree computations of the informative reference game (TapLargeDisputeGame), as an external library
/// @notice Kept out of the game's code so that the game stays below the 24,576-byte code size limit (EIP-170);
///         the game calls these functions with DELEGATECALL.
library TapLargeGameLib {
    error BadInputData();

    /// checkpointRoot = tree(L_0 … L_{n−1}) (§7.1), computed from the published list.
    function checkpointRoot(bytes32[] calldata leaves) external view returns (bytes32) {
        return TapLargeHash.merkleRoot(leaves);
    }

    /// Case (a): inputRoot = tree(inputLeaf(0 … T−1)) over `data`, the concatenated packed inputs of the T
    /// beats (⌈nIn/8⌉ bytes each, unused high bits zero; §4.4). Reverts on a wrong length or nonzero padding.
    function inputRoot(uint64 T, uint256 nIn, bytes calldata data) external view returns (bytes32) {
        uint256 nb = (nIn + 7) / 8;
        if (data.length != uint256(T) * nb) revert BadInputData();
        bytes32[] memory leaves = new bytes32[](T);
        for (uint256 t; t < T; ++t) {
            bytes calldata v = data[t * nb:(t + 1) * nb];
            if (nIn % 8 != 0 && uint8(v[nb - 1]) >> (nIn % 8) != 0) revert BadInputData();
            leaves[t] = TapLargeHash.inputLeaf(uint64(t), v);
        }
        return TapLargeHash.merkleRoot(leaves);
    }

    /// P of the public seg_0 (§5.3): prefixRoot(0x04, (0, 1, inputs…), 2 + nIn), built from packedInputs by
    /// shifting the bit stream up by two places.
    function seg0P(bytes calldata packed, uint256 nIn) external view returns (bytes32) {
        uint256 s = 2 + nIn;
        uint256 nWords = (s + 255) / 256;
        bytes memory bits = new bytes(nWords * 32);
        uint256 nBytes = (s + 7) / 8;
        uint256 len = packed.length;
        for (uint256 k; k < nBytes; ++k) {
            uint256 cur = k < len ? uint8(packed[k]) : 0;
            uint256 prev = k > 0 && k - 1 < len ? uint8(packed[k - 1]) : 0;
            uint256 b = ((cur << 2) | (prev >> 6)) & 0xff;
            if (k == 0) b |= 2; // signal 0 = 0, signal 1 = 1
            bits[k] = bytes1(uint8(b));
        }
        bytes32[] memory leaves = new bytes32[](nWords);
        for (uint256 k; k < nWords; ++k) {
            bytes32 word;
            assembly ("memory-safe") {
                word := mload(add(add(bits, 0x20), shl(5, k)))
            }
            leaves[k] = TapLargeHash.wordLeaf(0x04, k, word);
        }
        return TapLargeHash.merkleRoot(leaves);
    }
}
