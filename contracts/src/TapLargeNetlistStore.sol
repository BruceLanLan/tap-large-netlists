// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {ITapLargeNetlistStore} from "./ITapLargeNetlistStore.sol";
import {TapLargeHash} from "./TapLargeHash.sol";

/// @title Staged on-chain storage of tapnet/2 files (TAP "Large Netlists and Verifiable Execution" §2.1, §3)
/// @notice One SSTORE2 data contract per 16,384-byte chunk (runtime code 0x00 ‖ chunk, so it cannot be called).
///         A store for which `largeStored(h)` is true is the chunk oracle of the verifiers (§6).
/// @dev    Which netlist hashes are *known* (§3 item 7) is left to the derived contract through
///         `knownNetlist`: for example an owner-maintained registry, or a netlist-bound NFT collection or
///         companion (§8) answering from its pools. For tests and local use,
///         `TapLargeNetlistStoreOwned` below keeps an owner-registered set.
abstract contract TapLargeNetlistStore is ITapLargeNetlistStore {
    uint256 internal constant CHUNK = 16384;
    uint256 internal constant HEADER_LEN = 16;
    uint256 internal constant MAX_FILE = 67108864; // 64 MiB

    error StoreFailed();

    struct Large {
        uint64 length; // 0 until beginLarge succeeded (a begun file has length > 16)
        uint32 nIn; // bytes 8..11 of chunk 0, recorded when chunk 0 is stored
        uint32 nOut; // bytes 12..15 of chunk 0
        uint32 stored; // number of distinct chunks stored
        uint32 n; // ceil(length / CHUNK)
        bytes32 chunkRoot;
    }

    mapping(bytes32 netlistHash2 => Large) private _large;
    mapping(bytes32 netlistHash2 => mapping(uint256 index => address)) private _chunk;

    /// @notice True if `netlistHash2` is a known netlist hash (§3 item 7).
    function knownNetlist(bytes32 netlistHash2) public view virtual returns (bool);

    /// @inheritdoc ITapLargeNetlistStore
    function beginLarge(bytes32 netlistHash2, uint64 length, bytes32 chunkRoot) external {
        if (!knownNetlist(netlistHash2)) revert UnknownNetlist(netlistHash2);
        if (length <= HEADER_LEN || length > MAX_FILE) revert BadLength(length);
        if (TapLargeHash.netlistHash2(length, chunkRoot) != netlistHash2) revert HashMismatch(netlistHash2);
        Large storage l = _large[netlistHash2];
        if (l.length != 0) return; // repeated successful call: no effect, no event
        l.length = length;
        l.n = uint32((uint256(length) + CHUNK - 1) / CHUNK);
        l.chunkRoot = chunkRoot;
        emit LargeBegun(netlistHash2, length, chunkRoot);
    }

    /// @inheritdoc ITapLargeNetlistStore
    /// @dev The chunk is copied to memory once: it is hashed in place as 0x02 ‖ u32be(index) ‖ chunk and then
    ///      deployed from the same buffer behind an 11-byte SSTORE2 init code.
    function putChunk(bytes32 netlistHash2, uint32 index, bytes calldata chunk, bytes32[] calldata proof) external {
        Large storage l = _large[netlistHash2];
        uint256 length = l.length;
        if (length == 0) revert NotBegun(netlistHash2);
        uint256 n = l.n;
        uint256 len = chunk.length;
        if (index >= n || len != (index + 1 < n ? CHUNK : length - CHUNK * (n - 1))) revert BadChunk(index);

        uint256 p;
        bytes32 leaf;
        assembly ("memory-safe") {
            p := mload(0x40)
            // init code `61 <len+1:2> 80 60 0a 3d 39 3d f3` + runtime 0x00 (STOP), then the chunk at p + 11
            mstore(p, shl(168, or(or(shl(80, 0x61), shl(64, add(len, 1))), 0x80600a3d393df300)))
            calldatacopy(add(p, 11), chunk.offset, len)
            let saved := mload(p)
            // leaf preimage 0x02 ‖ u32be(index) ‖ chunk at p + 6
            mstore8(add(p, 6), 0x02)
            mstore8(add(p, 7), shr(24, index))
            mstore8(add(p, 8), shr(16, index))
            mstore8(add(p, 9), shr(8, index))
            mstore8(add(p, 10), index)
            if iszero(staticcall(gas(), 0x02, add(p, 6), add(len, 5), 0x00, 0x20)) { revert(0, 0) }
            leaf := mload(0x00)
            mstore(p, saved)
            mstore(0x40, and(add(add(p, add(len, 11)), 31), not(31))) // keep the buffer for `create`
        }
        if (!TapLargeHash.verify(l.chunkRoot, n, index, leaf, proof)) revert BadChunk(index);

        mapping(uint256 => address) storage slots = _chunk[netlistHash2];
        if (slots[index] != address(0)) return; // already stored: no effect, no event
        address pointer;
        assembly ("memory-safe") {
            pointer := create(0, p, add(len, 11))
        }
        if (pointer == address(0)) revert StoreFailed();
        slots[index] = pointer;
        uint32 stored = l.stored + 1;
        l.stored = stored;
        if (index == 0) {
            uint256 head;
            assembly ("memory-safe") {
                head := calldataload(add(chunk.offset, 8))
            }
            l.nIn = uint32(head >> 224);
            l.nOut = uint32(head >> 192);
        }
        emit ChunkStored(netlistHash2, index);
        if (stored == n) emit LargeStored(netlistHash2);
    }

    /// @inheritdoc ITapLargeNetlistStore
    function largeStored(bytes32 netlistHash2) public view returns (bool) {
        Large storage l = _large[netlistHash2];
        return l.length != 0 && l.stored == l.n;
    }

    /// @inheritdoc ITapLargeNetlistStore
    /// @dev Returns the ABI encoding straight from the data contract's code (one copy).
    function largeChunk(bytes32 netlistHash2, uint32 index) external view returns (bytes memory) {
        address pointer = _chunk[netlistHash2][index];
        if (pointer == address(0)) revert ChunkNotStored(index);
        assembly ("memory-safe") {
            let p := mload(0x40)
            let len := sub(extcodesize(pointer), 1)
            mstore(p, 0x20)
            mstore(add(p, 0x20), len)
            extcodecopy(pointer, add(p, 0x40), 1, len)
            let padded := and(add(len, 31), not(31))
            mstore(add(add(p, 0x40), len), 0) // zero the padding
            return(p, add(0x40, padded))
        }
    }

    /// @inheritdoc ITapLargeNetlistStore
    function largeInfo(bytes32 netlistHash2)
        external
        view
        returns (uint64 length, uint32 nIn, uint32 nOut, uint32 chunks, bool stored)
    {
        Large storage l = _large[netlistHash2];
        length = l.length;
        nIn = l.nIn;
        nOut = l.nOut;
        chunks = l.stored;
        stored = length != 0 && chunks == l.n;
    }

    /// @notice Data contract of a stored chunk (address(0) if not stored). Not part of the interface.
    function largeChunkPointer(bytes32 netlistHash2, uint32 index) external view returns (address) {
        return _chunk[netlistHash2][index];
    }

    /// @notice ERC-165.
    function supportsInterface(bytes4 interfaceId) public view virtual returns (bool) {
        return interfaceId == 0x01ffc9a7 || interfaceId == type(ITapLargeNetlistStore).interfaceId;
    }
}

/// @title A stand-alone store whose known netlist hashes are registered by an owner (tests and local use)
/// @dev   Owner-registered known hashes, for tests and local use: `addKnown` plays the role of an application's commit.
contract TapLargeNetlistStoreOwned is TapLargeNetlistStore {
    address public immutable owner;
    mapping(bytes32 => bool) private _known;

    error NotOwner();

    constructor(address owner_) {
        owner = owner_;
    }

    function addKnown(bytes32 netlistHash2) external {
        if (msg.sender != owner) revert NotOwner();
        _known[netlistHash2] = true;
    }

    function knownNetlist(bytes32 netlistHash2) public view override returns (bool) {
        return _known[netlistHash2];
    }
}
