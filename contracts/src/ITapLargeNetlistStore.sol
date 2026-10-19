// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

/// @title Optional on-chain storage of tapnet/2 files (TAP "Large Netlists and Verifiable Execution" §3); also the chunk oracle of §6.
/// @dev ERC-165 identifier 0x8230af7f (XOR of the five function selectors).
interface ITapLargeNetlistStore {
    function beginLarge(bytes32 netlistHash2, uint64 length, bytes32 chunkRoot) external;
    function putChunk(bytes32 netlistHash2, uint32 index, bytes calldata chunk, bytes32[] calldata proof) external;
    function largeStored(bytes32 netlistHash2) external view returns (bool);
    function largeChunk(bytes32 netlistHash2, uint32 index) external view returns (bytes memory);
    function largeInfo(bytes32 netlistHash2)
        external
        view
        returns (uint64 length, uint32 nIn, uint32 nOut, uint32 chunks, bool stored);

    event LargeBegun(bytes32 indexed netlistHash2, uint64 length, bytes32 chunkRoot);
    event ChunkStored(bytes32 indexed netlistHash2, uint32 index);
    event LargeStored(bytes32 indexed netlistHash2);

    error UnknownNetlist(bytes32 netlistHash2);
    error BadLength(uint64 length);
    error HashMismatch(bytes32 netlistHash2);
    error NotBegun(bytes32 netlistHash2);
    error BadChunk(uint32 index);
    error ChunkNotStored(uint32 index);
}
