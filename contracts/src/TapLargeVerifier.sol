// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

import {ITapLargeNetlistStore} from "./ITapLargeNetlistStore.sol";

/// @title One-step verifiers of the TAP "Large Netlists and Verifiable Execution" (§6): verify_segment and the beat-end verifiers
/// @notice Deterministic view functions of their arguments and of the chunk oracle (§6): the store given at
///         construction, read only through `largeInfo` and `largeChunk`. Witnesses use the encoding of §6.3 and
///         carry tree proofs only, never file bytes. Bit-identical in accept/reject to `assets/tap-draft-large-netlists/tapnet2.py`.
/// @dev    "Fail" (§6) is implemented by ending the call frame with the ABI encoding of `false`
///         (`mstore(0, 0) return(0, 64)`) from wherever the failure is detected, so these functions must be
///         called externally (a game calls this contract; it does not inherit it).
///
///         Hot-path design (gas): the file is read chunk by chunk into one reused 16 KiB buffer (reads are
///         monotonic in the file offset, so each chunk is fetched once); external P words are found through a
///         2-byte-per-word table indexed by word number; the P and D accumulators keep only their peaks, the
///         partial word, and the completed words of this segment (for reading back internal signals).
contract TapLargeVerifier {
    struct SegTuple {
        uint64 byteOffset;
        uint32 firstSignal;
        uint32 latchOrdinal;
        bytes32 p;
        bytes32 d;
        bytes32 m; // memory root after the write ports before this boundary (§4.5); ZERO_MEM without MEM
    }

    struct EndTuple {
        uint32 ordinal;
        bytes32 root;
    }

    struct Budget {
        uint32 gates;
        uint32 extWords;
        uint32 chunks;
    }

    /// RECOMMENDED default budget (§5.1), chosen from the measured gas of this contract (test/GasVerify.t.sol).
    uint32 public constant DEFAULT_GATES = 6000;
    uint32 public constant DEFAULT_EXT_WORDS = 256;
    uint32 public constant DEFAULT_CHUNKS = 16;

    uint256 internal constant CHUNK = 16384;
    uint256 internal constant MAX_FILE = 67108864;
    /// root of the all-zero memory tree (§4.6): Z_24, Z_0 = SHA-256(0x0B ‖ 0^32), Z_{h+1} = node(Z_h, Z_h)
    bytes32 public constant ZERO_MEM = 0x073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff;
    /// largeChunk(bytes32,uint32) selector, left-aligned
    uint256 internal constant SEL_LARGE_CHUNK = 0x7507716400000000000000000000000000000000000000000000000000000000;
    /// largeInfo(bytes32) selector, left-aligned
    uint256 internal constant SEL_LARGE_INFO = 0x260759cd00000000000000000000000000000000000000000000000000000000;

    // ---- context block (memory), offsets of 32-byte fields ----
    uint256 internal constant C_NH2 = 0x000;
    uint256 internal constant C_STORE = 0x020;
    uint256 internal constant C_FLEN = 0x040;
    uint256 internal constant C_NCH = 0x060;
    uint256 internal constant C_LASTLEN = 0x080;
    uint256 internal constant C_CUR = 0x0a0; // chunk in the buffer, plus one (0: none)
    uint256 internal constant C_BUF = 0x0c0;
    uint256 internal constant C_NREAD = 0x0e0; // chunks fetched
    uint256 internal constant C_CALLBUF = 0x100;
    uint256 internal constant C_FS = 0x120;
    uint256 internal constant C_SIG = 0x140; // end signal of the segment (written at the end)
    uint256 internal constant C_FSW = 0x160; // fs >> 8
    uint256 internal constant C_PW = 0x180; // completed P words of this segment, from word fs >> 8 on
    uint256 internal constant C_TBL = 0x1a0; // u16 per P word index: witness slot + 1 (0: absent)
    uint256 internal constant C_CBM = 0x1c0; // bitmap of P words counted as external reads
    uint256 internal constant C_PWOFF = 0x1e0; // calldata offset of the P word entries
    uint256 internal constant C_PARTK = 0x200; // index of the partial word (or 2^256 - 1)
    uint256 internal constant C_OFF0 = 0x220;
    uint256 internal constant C_ZERO = 0x240; // stateRoot == EMPTY
    uint256 internal constant C_SWOFF = 0x260;
    uint256 internal constant C_SWN = 0x280;
    uint256 internal constant C_SPTR = 0x2a0;
    uint256 internal constant C_REFS = 0x2c0;
    uint256 internal constant C_GATES = 0x2e0;
    uint256 internal constant C_EXT = 0x300;
    uint256 internal constant C_SCOUNT = 0x320; // distinct structural chunks
    uint256 internal constant C_SLASTCH = 0x340; // last structural chunk + 1
    uint256 internal constant C_ROMCH = 0x360; // Σ min(data chunks, 2)
    uint256 internal constant C_SLAST = 0x380; // last state word counted + 1
    uint256 internal constant C_BG = 0x3a0;
    uint256 internal constant C_BE = 0x3c0;
    uint256 internal constant C_BC = 0x3e0;
    uint256 internal constant C_RX = 0x400;
    uint256 internal constant C_RY = 0x420;
    uint256 internal constant C_ROMA = 0x440;
    uint256 internal constant C_ROMW = 0x460;
    uint256 internal constant C_ROMDS = 0x480;
    uint256 internal constant C_PACC = 0x4a0; // accumulator: full, pcount, partial, cnt, stack pointer
    uint256 internal constant C_DACC = 0x540;
    // append proofs: (peaks offset, peaks count, partial flag, partial word), contiguous for _accInit
    uint256 internal constant C_PPEAKOFF = 0x5e0;
    uint256 internal constant C_PPEAKN = 0x600;
    uint256 internal constant C_PHASP = 0x620;
    uint256 internal constant C_PARTW = 0x640; // the witness partial word of P
    uint256 internal constant C_DPEAKOFF = 0x660;
    uint256 internal constant C_DPEAKN = 0x680;
    uint256 internal constant C_DHASP = 0x6a0;
    uint256 internal constant C_DPARTW = 0x6c0;
    uint256 internal constant C_PWN = 0x6e0;
    uint256 internal constant C_PPROOFOFF = 0x700;
    uint256 internal constant C_PPROOFN = 0x720;
    uint256 internal constant C_SSIZE = 0x740;
    uint256 internal constant C_SPROOFOFF = 0x760;
    uint256 internal constant C_SPROOFN = 0x780;
    uint256 internal constant C_OFFEND = 0x7a0;
    uint256 internal constant C_LATEND = 0x7c0;
    uint256 internal constant C_L0 = 0x7e0;
    uint256 internal constant C_OP = 0x800; // parsed record: opcode, end, end of structural bytes,
    uint256 internal constant C_END = 0x820; // gate cost, ROM chunk term, ext count including it
    uint256 internal constant C_SEND = 0x840;
    uint256 internal constant C_G = 0x860;
    uint256 internal constant C_RC = 0x880;
    uint256 internal constant C_EXTNEW = 0x8a0;
    uint256 internal constant C_SCURK = 0x8c0; // state word cached by stateBit: index + 1, word
    uint256 internal constant C_SCURW = 0x8e0;
    uint256 internal constant C_HOFF = 0x900; // offset of the next record
    uint256 internal constant C_LATCUR = 0x920; // latch ordinal of the next record
    uint256 internal constant C_HST = 0x940; // 2: stopped by the budget in the fast path
    uint256 internal constant C_FASTEND = 0x960;
    uint256 internal constant C_BUFBASE = 0x980; // buffer address minus the file offset of its chunk
    uint256 internal constant C_PWBASE = 0x9a0;
    // memory (RAM ports, §6.1)
    uint256 internal constant C_MEMROOT = 0x9c0; // memRoot argument
    uint256 internal constant C_ZMEM = 0x9e0; // memRoot == ZERO_MEM
    uint256 internal constant C_MR = 0xa00; // running memory root R
    uint256 internal constant C_RDCUR = 0xa20; // calldata offset of the next read proof
    uint256 internal constant C_RDN = 0xa40; // read proofs left
    uint256 internal constant C_WRCUR = 0xa60; // calldata offset of the next write proof
    uint256 internal constant C_WRN = 0xa80; // write proofs left
    uint256 internal constant C_ZTAB = 0xaa0; // memory table Z_0 … Z_23 (only when the witness has proofs)
    uint256 internal constant C_BANK = 0xac0; // parsed RAM port: bank; ROM port: 1 (data through loadData)
    uint256 internal constant C_CHBM = 0xae0; // bitmap of the chunks fetched (C_NREAD counts distinct chunks)
    uint256 internal constant C_SIZE = 0xb00;

    ITapLargeNetlistStore public immutable store;
    uint32 internal immutable _gates;
    uint32 internal immutable _extWords;
    uint32 internal immutable _chunks;

    constructor(ITapLargeNetlistStore store_, uint32 gates, uint32 extWords, uint32 chunks) {
        store = store_;
        _gates = gates;
        _extWords = extWords;
        _chunks = chunks;
    }

    /// @notice The budget of claims verified with this contract (§5.1); fixed at construction.
    function segmentBudget() external view returns (uint32 gates, uint32 extWords, uint32 chunks) {
        return (_gates, _extWords, _chunks);
    }

    // =====================================================================================================
    // verify_segment (§6.1)
    // =====================================================================================================

    /// @notice verify_segment(netlistHash2, latchRoot, memRoot, a, b, witness, budget) of §6.1. For a file
    ///         without MEM, latchRoot is the agreed stateRoot and memRoot = ZERO_MEM; for a file with MEM they are
    ///         the opening of the agreed stateRoot (EMPTY and ZERO_MEM in beat 0). The EMPTY rule (LATCHes read 0)
    ///         is selected by latchRoot alone, the ZERO_MEM rule (RAM reads give 0, no read proof) by memRoot alone.
    function verifySegment(
        bytes32 netlistHash2,
        bytes32 latchRoot,
        bytes32 memRoot,
        SegTuple calldata a,
        SegTuple calldata b,
        bytes calldata witness,
        Budget calldata budget
    ) external view returns (bool) {
        (bool ok,) = _verifySegment(netlistHash2, latchRoot, memRoot, a, b, witness, budget);
        return ok;
    }

    /// @dev Returns (result, distinct chunks fetched from the oracle). A failure returns `false` from the call frame.
    function _verifySegment(
        bytes32 nh2,
        bytes32 latchRoot,
        bytes32 memRoot,
        SegTuple calldata a,
        SegTuple calldata b,
        bytes calldata witness,
        Budget calldata budget
    ) internal view returns (bool, uint256) {
        uint256 c = _segBegin(nh2, a, witness);
        _segProofs(c, latchRoot, a);
        bytes32 m0 = a.m;
        assembly ("memory-safe") {
            mstore(add(c, C_MEMROOT), memRoot)
            mstore(add(c, C_ZMEM), eq(memRoot, ZERO_MEM))
            mstore(add(c, C_MR), m0)
        }
        _prepareSegment(c, budget);
        _runSegment(c, _ld(c, C_OFF0), _ld(c, C_L0));
        return (_segEnd(c, b), _ld(c, C_NREAD));
    }

    /// @dev §6.1 steps 1 and 2, and the witness parse. Returns the context.
    function _segBegin(bytes32 nh2, SegTuple calldata a, bytes calldata witness) internal view returns (uint256 c) {
        // step 1: the oracle supplies the whole file, fileLen <= 64 MiB
        uint256 fileLen = _fileLen(nh2);
        if (fileLen > MAX_FILE) _fail();
        // step 2
        uint256 off0 = a.byteOffset;
        if (off0 < 16 || off0 >= fileLen) _fail();
        c = _alloc(C_SIZE);
        uint256 fs = a.firstSignal;
        uint256 l0 = a.latchOrdinal;
        assembly ("memory-safe") {
            mstore(add(c, C_NH2), nh2)
            mstore(add(c, C_FLEN), fileLen)
            mstore(add(c, C_OFF0), off0)
            mstore(add(c, C_FS), fs)
            mstore(add(c, C_L0), l0)
        }
        _parseSegWitness(c, _cdOffset(witness), witness.length);
    }

    /// @dev §6.1 steps 3 and 4.
    function _segProofs(uint256 c, bytes32 latchRoot, SegTuple calldata a) internal view {
        uint256 fs = _ld(c, C_FS);
        // step 3: append proofs of P at fs and of D at latchOrdinal
        _accSetup(c + C_PACC);
        _accSetup(c + C_DACC);
        if (!_accInit(c + C_PACC, 0x04, false, fs, c + C_PPEAKOFF, a.p)) _fail();
        if (!_accInit(c + C_DACC, 0x09, true, _ld(c, C_L0), c + C_DPEAKOFF, a.d)) _fail();
        // step 4: P words (the partial word's index must not be among them), state words / EMPTY rule
        uint256 pwOff = _ld(c, C_PWOFF);
        uint256 pwN = _ld(c, C_PWN);
        if (pwN != 0 && _ld(c, C_PHASP) != 0 && _idxAt(pwOff, pwN - 1) >= (fs >> 8)) _fail();
        if (!_checkWords(a.p, (fs + 255) >> 8, 0x04, pwOff, pwN, _ld(c, C_PPROOFOFF), _ld(c, C_PPROOFN))) _fail();
        if (latchRoot == bytes32(0)) {
            if (_ld(c, C_SSIZE) != 0 || _ld(c, C_SWN) != 0 || _ld(c, C_SPROOFN) != 0) _fail();
            assembly ("memory-safe") {
                mstore(add(c, C_ZERO), 1)
            }
        } else if (
            !_checkWords(
                latchRoot, _ld(c, C_SSIZE), 0x05, _ld(c, C_SWOFF), _ld(c, C_SWN), _ld(c, C_SPROOFOFF), _ld(c, C_SPROOFN)
            )
        ) _fail();
    }

    /// @dev §6.1 step 6 (no proofs left over) and step 7.
    function _segEnd(uint256 c, SegTuple calldata b) internal view returns (bool) {
        if (_ld(c, C_RDN) != 0 || _ld(c, C_WRN) != 0) return false;
        return b.byteOffset == _ld(c, C_OFFEND) && b.firstSignal == _ld(c, C_SIG)
            && b.latchOrdinal == _ld(c, C_LATEND) && b.p == _accRoot(c + C_PACC, 0x04, false)
            && b.d == _accRoot(c + C_DACC, 0x09, true) && b.m == bytes32(_ld(c, C_MR));
    }

    /// @dev Witness encoding of §6.3 (segment): fails if truncated, with trailing bytes, a bad flag, or word
    ///      indices that do not strictly increase. The memory part, present iff bytes follow the state
    ///      multiproof, fails on nRead = nWrite = 0, a zeroMask bit above 23, or a spelled-out sibling equal to
    ///      Z_h; it is parsed here (so the proof offsets are fixed) and the table Z_0 … Z_23 is built for it.
    function _parseSegWitness(uint256 c, uint256 p, uint256 len) internal view {
        assembly ("memory-safe") {
            function fail() {
                mstore(0, 0)
                mstore(0x20, 0)
                return(0, 0x40)
            }
            let e := add(p, len)
            // returns the cursor after a `words` list whose entries are checked to strictly increase
            function words(q, end, cOff, nOff, ctx) -> r {
                if gt(add(q, 2), end) { fail() }
                let n := shr(240, calldataload(q))
                q := add(q, 2)
                if gt(add(q, mul(n, 36)), end) { fail() }
                mstore(add(ctx, cOff), q)
                mstore(add(ctx, nOff), n)
                let last := 0
                for { let i := 0 } lt(i, n) { i := add(i, 1) } {
                    let k := shr(224, calldataload(add(q, mul(i, 36))))
                    if and(gt(i, 0), iszero(gt(k, last))) { fail() }
                    last := k
                }
                r := add(q, mul(n, 36))
            }
            function hashes16(q, end, cOff, nOff, ctx) -> r {
                if gt(add(q, 2), end) { fail() }
                let n := shr(240, calldataload(q))
                q := add(q, 2)
                if gt(add(q, shl(5, n)), end) { fail() }
                mstore(add(ctx, cOff), q)
                mstore(add(ctx, nOff), n)
                r := add(q, shl(5, n))
            }
            function frontier(q, end, offOff, nOff, hasOff, partOff, ctx) -> r {
                if gt(add(q, 1), end) { fail() }
                let n := shr(248, calldataload(q))
                q := add(q, 1)
                mstore(add(ctx, offOff), q)
                mstore(add(ctx, nOff), n)
                q := add(q, shl(5, n))
                if gt(add(q, 1), end) { fail() }
                let flag := shr(248, calldataload(q))
                q := add(q, 1)
                if gt(flag, 1) { fail() }
                mstore(add(ctx, hasOff), flag)
                if flag {
                    if gt(add(q, 32), end) { fail() }
                    mstore(add(ctx, partOff), calldataload(q))
                    q := add(q, 32)
                }
                r := q
            }
            let q := frontier(p, e, C_PPEAKOFF, C_PPEAKN, C_PHASP, C_PARTW, c)
            q := words(q, e, C_PWOFF, C_PWN, c)
            q := hashes16(q, e, C_PPROOFOFF, C_PPROOFN, c)
            q := frontier(q, e, C_DPEAKOFF, C_DPEAKN, C_DHASP, C_DPARTW, c)
            if gt(add(q, 4), e) { fail() }
            mstore(add(c, C_SSIZE), shr(224, calldataload(q)))
            q := add(q, 4)
            q := words(q, e, C_SWOFF, C_SWN, c)
            q := hashes16(q, e, C_SPROOFOFF, C_SPROOFN, c)
            if lt(q, e) {
                // memory = u16(nRead) ‖ access × nRead ‖ u16(nWrite) ‖ access × nWrite,
                // access = word ‖ u32(zeroMask) ‖ sibling × (24 − popcount(zeroMask))
                let zt := mload(0x40)
                mstore(0x40, add(zt, 0x300))
                mstore(add(c, C_ZTAB), zt)
                mstore8(0x00, 0x0b)
                mstore(0x01, 0)
                if iszero(staticcall(gas(), 0x02, 0x00, 33, zt, 0x20)) { revert(0, 0) }
                for { let h := 1 } lt(h, 24) { h := add(h, 1) } {
                    let z := mload(add(zt, shl(5, sub(h, 1))))
                    mstore8(0x00, 0x01)
                    mstore(0x01, z)
                    mstore(0x21, z)
                    if iszero(staticcall(gas(), 0x02, 0x00, 65, add(zt, shl(5, h)), 0x20)) { revert(0, 0) }
                }
                mstore(0x40, add(zt, 0x300))
                let total := 0
                for { let k := 0 } lt(k, 2) { k := add(k, 1) } {
                    if gt(add(q, 2), e) { fail() }
                    let n := shr(240, calldataload(q))
                    q := add(q, 2)
                    switch k
                    case 0 {
                        mstore(add(c, C_RDCUR), q)
                        mstore(add(c, C_RDN), n)
                    }
                    default {
                        mstore(add(c, C_WRCUR), q)
                        mstore(add(c, C_WRN), n)
                    }
                    total := add(total, n)
                    for { let i := 0 } lt(i, n) { i := add(i, 1) } {
                        if gt(add(q, 36), e) { fail() }
                        let mask := shr(224, calldataload(add(q, 32)))
                        if shr(24, mask) { fail() }
                        q := add(q, 36)
                        for { let h := 0 } lt(h, 24) { h := add(h, 1) } {
                            if iszero(and(shr(h, mask), 1)) {
                                if gt(add(q, 32), e) { fail() }
                                if eq(calldataload(q), mload(add(zt, shl(5, h)))) { fail() }
                                q := add(q, 32)
                            }
                        }
                    }
                }
                if iszero(total) { fail() }
            }
            if iszero(eq(q, e)) { fail() }
        }
    }

    /// @dev Buffers and counters of the segment loop. The P-word table and the counted-words bitmap cover word
    ///      indices below min(ceil(fs/256), 65536): references are u24, so no other P word can be read.
    function _prepareSegment(uint256 c, Budget calldata budget) internal view {
        uint256 fileLen = _ld(c, C_FLEN);
        uint256 fs = _ld(c, C_FS);
        uint256 nPw = (fs + 255) >> 8;
        uint256 tblN = nPw < 65536 ? nPw : 65536;
        address st = address(store);
        uint256 bg = budget.gates;
        uint256 be = budget.extWords;
        uint256 bc = budget.chunks;
        assembly ("memory-safe") {
            mstore(add(c, C_STORE), st)
            let nch := shr(14, add(fileLen, 16383))
            mstore(add(c, C_NCH), nch)
            mstore(add(c, C_LASTLEN), sub(fileLen, shl(14, sub(nch, 1))))
            mstore(add(c, C_FSW), shr(8, fs))
            mstore(add(c, C_BG), bg)
            mstore(add(c, C_BE), be)
            mstore(add(c, C_BC), bc)
            switch mload(add(c, C_PHASP))
            case 0 { mstore(add(c, C_PARTK), not(0)) }
            default { mstore(add(c, C_PARTK), shr(8, fs)) }

            let m := mload(0x40)
            mstore(add(c, C_CALLBUF), m)
            m := add(m, 0x60)
            mstore(add(c, C_BUF), m)
            m := add(m, add(CHUNK, 0x40))
            // references of the parsed record, packed as in the file (3 bytes each): at most 1 + 20 + 256
            mstore(add(c, C_REFS), m)
            m := add(m, 0x360)
            // bitmap of the chunks fetched (at most 4,096 chunks), zeroed
            calldatacopy(m, calldatasize(), 0x200)
            mstore(add(c, C_CHBM), m)
            m := add(m, 0x200)
            // P-word table, zeroed
            let tbl := m
            let tblBytes := and(add(shl(1, tblN), 31), not(31))
            calldatacopy(tbl, calldatasize(), tblBytes)
            mstore(add(c, C_TBL), tbl)
            m := add(m, tblBytes)
            // counted bitmap, zeroed
            let cbmBytes := shl(5, shr(8, add(tblN, 255)))
            calldatacopy(m, calldatasize(), cbmBytes)
            mstore(add(c, C_CBM), m)
            m := add(m, cbmBytes)
            // fill the table: entry = slot + 1
            let pwOff := mload(add(c, C_PWOFF))
            let pwN := mload(add(c, C_PWN))
            for { let s := 0 } lt(s, pwN) { s := add(s, 1) } {
                let k := shr(224, calldataload(add(pwOff, mul(s, 36))))
                if lt(k, tblN) {
                    let at := add(tbl, shl(1, k))
                    let v := add(s, 1)
                    mstore8(at, shr(8, v))
                    mstore8(add(at, 1), v)
                }
            }
            // completed words of the segment grow from here (word k at PWBASE + 32 k, PWBASE = m - 32 (fs >> 8),
            // computed modulo 2^256); nothing is allocated after this point
            mstore(add(c, C_PW), m)
            mstore(add(c, C_PWBASE), sub(m, shl(5, shr(8, fs))))
            mstore(0x40, m)
        }
    }

    /// @dev The canonical segment from (off, fs, l0) under the budget (§5.3), evaluated as it is determined
    ///      (§6.1 steps 5 and 6). Records are read through the oracle; the record after the segment is parsed
    ///      for maximality. Writes the end offset and latch ordinal to the context.
    ///
    ///      A NAND or LATCH record that lies inside the chunk in the buffer, in a chunk already counted as a
    ///      structural chunk, takes a fast path (its chunk cost is zero); every other record takes the general
    ///      path (parse, admit, evaluate). Both compute the same cost and values.
    function _runSegment(uint256 c, uint256 off, uint256 lat) internal view {
        assembly {
            function fail() {
                mstore(0, 0)
                mstore(0x20, 0)
                return(0, 0x40)
            }
            // ---------------- hashing (scratch 0x00..0x40; the free memory pointer is restored)
            function nodeHash(l, r) -> h {
                let fmp := mload(0x40)
                mstore(0x01, l)
                mstore(0x21, r)
                mstore8(0x00, 0x01)
                if iszero(staticcall(gas(), 0x02, 0x00, 65, 0x00, 0x20)) { revert(0, 0) }
                h := mload(0x00)
                mstore(0x40, fmp)
            }
            function wordLeaf(dom, k, w) -> h {
                mstore(0x01, shl(224, k))
                mstore(0x05, w)
                mstore8(0x00, dom)
                if iszero(staticcall(gas(), 0x02, 0x00, 37, 0x00, 0x20)) { revert(0, 0) }
                h := mload(0x00)
            }
            // accumulator: +0 full, +0x20 pcount, +0x40 partial, +0x60 cnt, +0x80 stack pointer
            function pushLeaf(acc, leaf) {
                let full := mload(acc)
                let cnt := mload(add(acc, 0x60))
                let st := mload(add(acc, 0x80))
                let h := leaf
                for { let f := full } and(f, 1) { f := shr(1, f) } {
                    cnt := sub(cnt, 1)
                    h := nodeHash(mload(add(st, shl(5, cnt))), h)
                }
                mstore(add(st, shl(5, cnt)), h)
                mstore(add(acc, 0x60), add(cnt, 1))
                mstore(acc, add(full, 1))
            }
            // bit o of a bit word (bit o mod 8 of byte o / 8)
            function bitAt(word, o) -> v {
                v := and(shr(add(sub(248, and(o, 0xf8)), and(o, 7)), word), 1)
            }
            // byte reversal: converts a bit word between the byte layout of §4.2 and the "LSB layout" (bit o at
            // integer bit o) used for the completed P words of the segment and inside the fast path
            function rev(x) -> r {
                r := or(
                    shr(8, and(x, 0xff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00)),
                    shl(8, and(x, 0x00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff00ff))
                )
                r := or(
                    shr(16, and(r, 0xffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000)),
                    shl(16, and(r, 0x0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff0000ffff))
                )
                r := or(
                    shr(32, and(r, 0xffffffff00000000ffffffff00000000ffffffff00000000ffffffff00000000)),
                    shl(32, and(r, 0x00000000ffffffff00000000ffffffff00000000ffffffff00000000ffffffff))
                )
                r := or(
                    shr(64, and(r, 0xffffffffffffffff0000000000000000ffffffffffffffff0000000000000000)),
                    shl(64, and(r, 0x0000000000000000ffffffffffffffff0000000000000000ffffffffffffffff))
                )
                r := or(shr(128, r), shl(128, r))
            }
            // ---------------- chunk oracle
            // fetches chunk ci (checked: index and length, §6); returns the offset of its bytes in the return data
            // and its length. C_NREAD counts distinct chunks (bitmap C_CHBM).
            function fetch(ctx, ci) -> ro, len {
                let q := mload(add(ctx, C_CALLBUF))
                mstore(q, SEL_LARGE_CHUNK)
                mstore(add(q, 4), mload(add(ctx, C_NH2)))
                mstore(add(q, 36), ci)
                if iszero(staticcall(gas(), mload(add(ctx, C_STORE)), q, 68, 0, 0)) { fail() }
                let rds := returndatasize()
                if lt(rds, 64) { fail() }
                returndatacopy(q, 0, 32)
                let o := mload(q)
                if gt(o, sub(rds, 32)) { fail() }
                returndatacopy(q, o, 32)
                len := mload(q)
                let n := mload(add(ctx, C_NCH))
                if iszero(lt(ci, n)) { fail() }
                let want := CHUNK
                if eq(ci, sub(n, 1)) { want := mload(add(ctx, C_LASTLEN)) }
                if iszero(eq(len, want)) { fail() }
                if gt(add(add(o, 32), len), rds) { fail() }
                ro := add(o, 32)
                let at := add(mload(add(ctx, C_CHBM)), shl(5, shr(8, ci)))
                let bit := shl(and(ci, 255), 1)
                if iszero(and(mload(at), bit)) {
                    mstore(at, or(mload(at), bit))
                    mstore(add(ctx, C_NREAD), add(mload(add(ctx, C_NREAD)), 1))
                }
            }
            // chunk ci into the buffer
            function ensure(ctx, ci) {
                let ro, len := fetch(ctx, ci)
                returndatacopy(mload(add(ctx, C_BUF)), ro, len)
                mstore(add(ctx, C_CUR), add(ci, 1))
            }
            // 32 bytes of chunk ci from offset o (o < its length; bytes past its end are zero), without replacing
            // the chunk in the buffer
            function chunkPart(ctx, ci, o) -> w {
                if eq(mload(add(ctx, C_CUR)), add(ci, 1)) {
                    w := mload(add(mload(add(ctx, C_BUF)), o))
                    leave
                }
                let ro, len := fetch(ctx, ci)
                let k := sub(len, o)
                if gt(k, 32) { k := 32 }
                let q := mload(add(ctx, C_CALLBUF))
                mstore(q, 0)
                returndatacopy(q, add(ro, o), k)
                w := mload(q)
            }
            // n (<= 32) bytes at file offset `at` (at + n <= fileLen), left-aligned, read without moving the
            // buffer: the data of a ROM read port, which may lie before the records being parsed
            function loadData(ctx, at, n) -> w {
                let ci := shr(14, at)
                let o := and(at, 16383)
                w := chunkPart(ctx, ci, o)
                let rem := sub(CHUNK, o)
                if lt(rem, n) {
                    w := and(w, not(shr(shl(3, rem), not(0))))
                    w := or(w, shr(shl(3, rem), chunkPart(ctx, add(ci, 1), 0)))
                }
            }
            // n (<= 32) bytes at file offset `at`, left-aligned; bytes after the n-th are unspecified
            function load(ctx, at, n) -> w {
                let ci := shr(14, at)
                let o := and(at, 16383)
                if iszero(eq(mload(add(ctx, C_CUR)), add(ci, 1))) { ensure(ctx, ci) }
                w := mload(add(mload(add(ctx, C_BUF)), o))
                let rem := sub(CHUNK, o)
                if lt(rem, n) {
                    w := and(w, not(shr(shl(3, rem), not(0))))
                    ensure(ctx, add(ci, 1))
                    w := or(w, shr(shl(3, rem), mload(mload(add(ctx, C_BUF)))))
                }
            }
            // ---------------- values
            // value of signal x: a constant, a value computed earlier in this segment, or an external P word
            function val(ctx, x, fs) -> v {
                if lt(x, 2) {
                    v := x
                    leave
                }
                let k := shr(8, x)
                let word := 0
                switch lt(x, fs)
                case 0 {
                    let full := mload(add(ctx, C_PACC))
                    if iszero(lt(x, add(shl(8, full), mload(add(ctx, add(C_PACC, 0x20)))))) { fail() }
                    switch eq(k, full)
                    case 1 { word := mload(add(ctx, add(C_PACC, 0x40))) }
                    default {
                        // completed words are kept in LSB layout
                        v := and(shr(and(x, 255), mload(add(mload(add(ctx, C_PWBASE)), shl(5, k)))), 1)
                        leave
                    }
                }
                default {
                    switch eq(k, mload(add(ctx, C_PARTK)))
                    case 1 { word := mload(add(ctx, C_PARTW)) }
                    default {
                        let e := shr(240, mload(add(mload(add(ctx, C_TBL)), shl(1, k))))
                        if iszero(e) { fail() }
                        word := calldataload(add(mload(add(ctx, C_PWOFF)), add(mul(sub(e, 1), 36), 4)))
                    }
                }
                v := and(shr(add(sub(248, and(x, 0xf8)), and(x, 7)), word), 1)
            }
            function appendP(ctx, v) {
                let pp := add(ctx, add(C_PACC, 0x20))
                let pcount := mload(pp)
                if v {
                    mstore(add(pp, 0x20), or(mload(add(pp, 0x20)), shl(add(sub(248, and(pcount, 0xf8)), and(pcount, 7)), 1)))
                }
                pcount := add(pcount, 1)
                switch eq(pcount, 256)
                case 1 {
                    let acc := add(ctx, C_PACC)
                    let full := mload(acc)
                    let word := mload(add(acc, 0x40))
                    mstore(add(mload(add(ctx, C_PWBASE)), shl(5, full)), rev(word))
                    pushLeaf(acc, wordLeaf(0x04, full, word))
                    mstore(pp, 0)
                    mstore(add(acc, 0x40), 0)
                }
                default { mstore(pp, pcount) }
            }
            function appendD(ctx, d) {
                let acc := add(ctx, C_DACC)
                let pcount := mload(add(acc, 0x20))
                let part := or(mload(add(acc, 0x40)), shl(sub(224, shl(5, pcount)), d))
                pcount := add(pcount, 1)
                switch eq(pcount, 8)
                case 1 {
                    pushLeaf(acc, wordLeaf(0x09, mload(acc), part))
                    mstore(add(acc, 0x20), 0)
                    mstore(add(acc, 0x40), 0)
                }
                default {
                    mstore(add(acc, 0x20), pcount)
                    mstore(add(acc, 0x40), part)
                }
            }
            // bit l of the beat's state: state words are found by a forward scan (ordinals only increase)
            function stateBit(ctx, l) -> v {
                let k := shr(8, l)
                if iszero(eq(add(k, 1), mload(add(ctx, C_SCURK)))) {
                    let n := mload(add(ctx, C_SWN))
                    let q := mload(add(ctx, C_SPTR))
                    let base := mload(add(ctx, C_SWOFF))
                    for {} 1 {} {
                        if iszero(lt(q, n)) { fail() }
                        let idx := shr(224, calldataload(add(base, mul(q, 36))))
                        if eq(idx, k) { break }
                        if gt(idx, k) { fail() }
                        q := add(q, 1)
                    }
                    mstore(add(ctx, C_SPTR), q)
                    mstore(add(ctx, C_SCURK), add(k, 1))
                    mstore(add(ctx, C_SCURW), calldataload(add(add(base, mul(q, 36)), 4)))
                }
                v := bitAt(mload(add(ctx, C_SCURW)), and(l, 255))
            }
            // 1 if r is an external read (2 <= r < fs) of a P word not counted yet; marks it
            function markP(ctx, r, fs) -> isNew {
                if lt(r, 2) { leave }
                if iszero(lt(r, fs)) { leave }
                let at := add(mload(add(ctx, C_CBM)), shl(5, shr(16, r)))
                let word := mload(at)
                let bit := shl(and(shr(8, r), 255), 1)
                if iszero(and(word, bit)) {
                    mstore(at, or(word, bit))
                    isNew := 1
                }
            }
            // over budget (gates, ext and the unchanged chunk cost of the segment)
            function over(ctx, gates, ext) -> o {
                o := or(
                    or(gt(gates, mload(add(ctx, C_BG))), gt(ext, mload(add(ctx, C_BE)))),
                    gt(add(mload(add(ctx, C_SCOUNT)), mload(add(ctx, C_ROMCH))), mload(add(ctx, C_BC)))
                )
            }
            // reference i of the parsed record (packed u24)
            function refAt(ctx, i) -> r {
                r := shr(232, mload(add(mload(add(ctx, C_REFS)), mul(3, i))))
            }
            // copies the n references at file offset src into the reference buffer; returns the number of P words
            // they newly count as external reads
            function copyRefs(ctx, src, n) -> extDelta {
                let refs := mload(add(ctx, C_REFS))
                let total := mul(3, n)
                for { let k := 0 } lt(k, total) { k := add(k, 32) } {
                    let nb := sub(total, k)
                    if gt(nb, 32) { nb := 32 }
                    mstore(add(refs, k), load(ctx, add(src, k), nb))
                }
                let fs := mload(add(ctx, C_FS))
                for { let i := 0 } lt(i, n) { i := add(i, 1) } {
                    extDelta := add(extDelta, markP(ctx, shr(232, mload(add(refs, mul(3, i)))), fs))
                }
            }
            // Σ value(ref[first + i]) · 2^i for i < a
            function addrOf(ctx, first, a) -> addr {
                let fs := mload(add(ctx, C_FS))
                for { let i := 0 } lt(i, a) { i := add(i, 1) } {
                    addr := or(addr, shl(i, val(ctx, refAt(ctx, add(first, i)), fs)))
                }
            }
            // ROM (port = 0) or ROM read port (port = 1, data read without moving the buffer)
            function evalRom(ctx, port) {
                let a := mload(add(ctx, C_ROMA))
                let w := mload(add(ctx, C_ROMW))
                let addr := addrOf(ctx, 0, a)
                let base := mul(addr, w)
                let lo := add(mload(add(ctx, C_ROMDS)), shr(3, base))
                let nb := add(sub(shr(3, add(base, sub(w, 1))), shr(3, base)), 1)
                if gt(add(lo, nb), mload(add(ctx, C_FLEN))) { fail() }
                let n1 := nb
                if gt(n1, 32) { n1 := 32 }
                let w1 := 0
                let w2 := 0
                switch port
                case 0 {
                    w1 := load(ctx, lo, n1)
                    if gt(nb, 32) { w2 := load(ctx, add(lo, 32), sub(nb, 32)) }
                }
                default {
                    w1 := loadData(ctx, lo, n1)
                    if gt(nb, 32) { w2 := loadData(ctx, add(lo, 32), sub(nb, 32)) }
                }
                let sh := and(base, 7)
                for { let j := 0 } lt(j, w) { j := add(j, 1) } {
                    let t := add(sh, j)
                    switch lt(t, 256)
                    case 1 { appendP(ctx, bitAt(w1, t)) }
                    default { appendP(ctx, bitAt(w2, sub(t, 256))) }
                }
            }
            // ---------------- memory (§4.6, §6.1)
            function memLeaf(word) -> h {
                mstore8(0x00, 0x0b)
                mstore(0x01, word)
                if iszero(staticcall(gas(), 0x02, 0x00, 33, 0x00, 0x20)) { revert(0, 0) }
                h := mload(0x00)
            }
            // folds leaf hA (and hB if two) with the 24 siblings of the memory proof at calldata offset cur
            // (zero siblings from the mask); returns both roots and the offset after the proof
            function memFold(ctx, cur, pos, hA, hB, two) -> rA, rB, next {
                let mask := shr(224, calldataload(add(cur, 32)))
                let q := add(cur, 36)
                let zt := mload(add(ctx, C_ZTAB))
                for { let h := 0 } lt(h, 24) { h := add(h, 1) } {
                    let sib := 0
                    switch and(shr(h, mask), 1)
                    case 1 { sib := mload(add(zt, shl(5, h))) }
                    default {
                        sib := calldataload(q)
                        q := add(q, 32)
                    }
                    switch and(shr(h, pos), 1)
                    case 0 {
                        hA := nodeHash(hA, sib)
                        if two { hB := nodeHash(hB, sib) }
                    }
                    default {
                        hA := nodeHash(sib, hA)
                        if two { hB := nodeHash(sib, hB) }
                    }
                }
                rA := hA
                rB := hB
                next := q
            }
            // (pos, o) of entry addr of the parsed port's bank: bit = addr · stride(w), pos = bank · 2^16 + bit / 256
            function memPos(ctx, addr) -> pos, o {
                let w := mload(add(ctx, C_ROMW))
                let st := 1
                for {} lt(st, w) {} { st := shl(1, st) }
                let bit := mul(addr, st)
                pos := or(shl(16, mload(add(ctx, C_BANK))), shr(8, bit))
                o := and(bit, 255)
            }
            function evalRamRead(ctx) {
                let w := mload(add(ctx, C_ROMW))
                let addr := addrOf(ctx, 0, mload(add(ctx, C_ROMA)))
                if mload(add(ctx, C_ZMEM)) {
                    for { let j := 0 } lt(j, w) { j := add(j, 1) } { appendP(ctx, 0) }
                    leave
                }
                let left := mload(add(ctx, C_RDN))
                if iszero(left) { fail() }
                let pos, o := memPos(ctx, addr)
                let cur := mload(add(ctx, C_RDCUR))
                let word := calldataload(cur)
                let r, r2, next := memFold(ctx, cur, pos, memLeaf(word), 0, 0)
                if iszero(eq(r, mload(add(ctx, C_MEMROOT)))) { fail() }
                mstore(add(ctx, C_RDCUR), next)
                mstore(add(ctx, C_RDN), sub(left, 1))
                for { let j := 0 } lt(j, w) { j := add(j, 1) } { appendP(ctx, bitAt(word, add(o, j))) }
            }
            function evalRamWrite(ctx) {
                let fs := mload(add(ctx, C_FS))
                if iszero(val(ctx, refAt(ctx, 0), fs)) { leave }
                let a := mload(add(ctx, C_ROMA))
                let w := mload(add(ctx, C_ROMW))
                let pos, o := memPos(ctx, addrOf(ctx, 1, a))
                let left := mload(add(ctx, C_WRN))
                if iszero(left) { fail() }
                let cur := mload(add(ctx, C_WRCUR))
                let old := calldataload(cur)
                let nw := old
                for { let j := 0 } lt(j, w) { j := add(j, 1) } {
                    let t := add(o, j)
                    let bp := add(sub(248, and(t, 0xf8)), and(t, 7))
                    nw := or(and(nw, not(shl(bp, 1))), shl(bp, val(ctx, refAt(ctx, add(add(1, a), j)), fs)))
                }
                let rOld, rNew, next := memFold(ctx, cur, pos, memLeaf(old), memLeaf(nw), 1)
                if iszero(eq(rOld, mload(add(ctx, C_MR)))) { fail() }
                mstore(add(ctx, C_MR), rNew)
                mstore(add(ctx, C_WRCUR), next)
                mstore(add(ctx, C_WRN), sub(left, 1))
            }
            // ---------------- fast path
            // value of an external signal (2 <= x < fs): the partial word of the append proof or a P word
            function extVal(ctx, x) -> v {
                let k := shr(8, x)
                let word := mload(add(ctx, C_PARTW))
                if iszero(eq(k, mload(add(ctx, C_PARTK)))) {
                    let e := shr(240, mload(add(mload(add(ctx, C_TBL)), shl(1, k))))
                    if iszero(e) { fail() }
                    word := calldataload(add(mload(add(ctx, C_PWOFF)), add(mul(sub(e, 1), 36), 4)))
                }
                v := and(shr(add(sub(248, and(x, 0xf8)), and(x, 7)), word), 1)
            }
            // external read 2 <= x < fs in the fast path: counts its word (bit 1 of r if new) and looks up its value
            // (bit 0) without failing; bit 2 is set if the word is missing, which fails only if the record is admitted
            function extRef(ctx, x) -> r {
                let k := shr(8, x)
                let at := add(mload(add(ctx, C_CBM)), shl(5, shr(8, k)))
                let word := mload(at)
                let bit := shl(and(k, 255), 1)
                if iszero(and(word, bit)) {
                    mstore(at, or(word, bit))
                    r := 2
                }
                switch eq(k, mload(add(ctx, C_PARTK)))
                case 1 { word := mload(add(ctx, C_PARTW)) }
                default {
                    let e := shr(240, mload(add(mload(add(ctx, C_TBL)), shl(1, k))))
                    if iszero(e) {
                        r := or(r, 4)
                        leave
                    }
                    word := calldataload(add(mload(add(ctx, C_PWOFF)), add(mul(sub(e, 1), 36), 4)))
                }
                r := or(r, and(shr(add(sub(248, and(x, 0xf8)), and(x, 7)), word), 1))
            }
            // a completed P word (index full, LSB layout) of this segment: keep it and push its leaf
            function flushP(ctx, full, wordLsb) {
                mstore(add(mload(add(ctx, C_PWBASE)), shl(5, full)), wordLsb)
                let acc := add(ctx, C_PACC)
                mstore(acc, full)
                pushLeaf(acc, wordLeaf(0x04, full, rev(wordLsb)))
            }
            // Runs NAND and LATCH records while they lie inside the chunk in the buffer, which must already be a
            // counted structural chunk (so their chunk cost is zero), and the segment's chunk cost is within the
            // budget. Called only after the segment has a record, so every record is checked against the budget.
            // The P accumulator is held as sig = next signal (= 256 * full + pcount) and its partial word in LSB
            // layout. Stops at the first other record; sets C_HST = 2 if a record is over the budget.
            function hot(ctx) {
                {
                    let cur := mload(add(ctx, C_CUR))
                    if iszero(eq(cur, mload(add(ctx, C_SLASTCH)))) { leave }
                    if gt(add(mload(add(ctx, C_SCOUNT)), mload(add(ctx, C_ROMCH))), mload(add(ctx, C_BC))) { leave }
                    if iszero(lt(mload(add(ctx, C_GATES)), mload(add(ctx, C_BG)))) { leave }
                    // a first record over the extWords budget (a port's memory units, or a ROM with many address
                    // words): no further record fits, which the general path decides
                    if gt(mload(add(ctx, C_EXT)), mload(add(ctx, C_BE))) { leave }
                    let fe := shl(14, cur)
                    if lt(mload(add(ctx, C_FLEN)), fe) { fe := mload(add(ctx, C_FLEN)) }
                    // a record starting at or before fe - 7 lies inside (NAND 7 bytes, LATCH 4)
                    mstore(add(ctx, C_FASTEND), sub(fe, 7))
                    mstore(add(ctx, C_BUFBASE), sub(mload(add(ctx, C_BUF)), shl(14, sub(cur, 1))))
                }
                let ho := mload(add(ctx, C_HOFF))
                let fs := mload(add(ctx, C_FS))
                let sig := add(shl(8, mload(add(ctx, C_PACC))), mload(add(ctx, add(C_PACC, 0x20))))
                let part := rev(mload(add(ctx, add(C_PACC, 0x40))))
                let left := sub(mload(add(ctx, C_BG)), mload(add(ctx, C_GATES)))
                for {} iszero(gt(ho, mload(add(ctx, C_FASTEND)))) {} {
                    let w := mload(add(mload(add(ctx, C_BUFBASE)), ho))
                    let v := shr(248, w)
                    if gt(v, 1) { break }
                    if iszero(left) {
                        mstore(add(ctx, C_HST), 2)
                        break
                    }
                    switch v
                    case 0 {
                        let x := and(shr(224, w), 0xffffff)
                        let y := and(shr(200, w), 0xffffff)
                        // external reads: count new words and look up values; a missing word fails only if the
                        // record is admitted (8 marks a value not looked up yet)
                        w := 8
                        v := 8
                        if and(gt(x, 1), lt(x, fs)) {
                            w := extRef(ctx, x)
                            if and(w, 2) {
                                let e := add(mload(add(ctx, C_EXT)), 1)
                                mstore(add(ctx, C_EXT), e)
                                if gt(e, mload(add(ctx, C_BE))) {
                                    mstore(add(ctx, C_HST), 2)
                                    break
                                }
                            }
                        }
                        if and(gt(y, 1), lt(y, fs)) {
                            v := extRef(ctx, y)
                            if and(v, 2) {
                                let e := add(mload(add(ctx, C_EXT)), 1)
                                mstore(add(ctx, C_EXT), e)
                                if gt(e, mload(add(ctx, C_BE))) {
                                    mstore(add(ctx, C_HST), 2)
                                    break
                                }
                            }
                        }
                        // admitted: values of x and y (both are evaluated, as in the reference)
                        switch w
                        case 8 {
                            w := x
                            if gt(x, 1) {
                                if iszero(lt(x, sig)) { fail() }
                                let wd := part
                                if iszero(eq(shr(8, x), shr(8, sig))) {
                                    wd := mload(add(mload(add(ctx, C_PWBASE)), shl(5, shr(8, x))))
                                }
                                w := and(shr(and(x, 255), wd), 1)
                            }
                        }
                        default {
                            if and(w, 4) { fail() }
                            w := and(w, 1)
                        }
                        switch v
                        case 8 {
                            v := y
                            if gt(y, 1) {
                                if iszero(lt(y, sig)) { fail() }
                                let wd := part
                                if iszero(eq(shr(8, y), shr(8, sig))) {
                                    wd := mload(add(mload(add(ctx, C_PWBASE)), shl(5, shr(8, y))))
                                }
                                v := and(shr(and(y, 255), wd), 1)
                            }
                        }
                        default {
                            if and(v, 4) { fail() }
                            v := and(v, 1)
                        }
                        v := sub(1, and(w, v))
                        ho := add(ho, 7)
                    }
                    default {
                        let l := mload(add(ctx, C_LATCUR))
                        let sk := add(shr(8, l), 1)
                        if iszero(eq(sk, mload(add(ctx, C_SLAST)))) {
                            mstore(add(ctx, C_SLAST), sk)
                            let e := add(mload(add(ctx, C_EXT)), 1)
                            mstore(add(ctx, C_EXT), e)
                            if gt(e, mload(add(ctx, C_BE))) {
                                mstore(add(ctx, C_HST), 2)
                                break
                            }
                        }
                        v := 0
                        if iszero(mload(add(ctx, C_ZERO))) { v := stateBit(ctx, l) }
                        appendD(ctx, and(shr(224, w), 0xffffff))
                        mstore(add(ctx, C_LATCUR), add(l, 1))
                        ho := add(ho, 4)
                    }
                    left := sub(left, 1)
                    part := or(part, shl(and(sig, 255), v))
                    sig := add(sig, 1)
                    if iszero(and(sig, 255)) {
                        flushP(ctx, sub(shr(8, sig), 1), part)
                        part := 0
                    }
                }
                mstore(add(ctx, C_HOFF), ho)
                mstore(add(ctx, C_GATES), sub(mload(add(ctx, C_BG)), left))
                mstore(add(ctx, C_PACC), shr(8, sig))
                mstore(add(ctx, add(C_PACC, 0x20)), and(sig, 255))
                mstore(add(ctx, add(C_PACC, 0x40)), rev(part))
            }
            // ---------------- general path: parse (with its tentative cost), admit or stop, evaluate
            function parseRom(ctx, at, h, fileLen) -> extDelta {
                let a := and(shr(240, h), 0xff)
                let w := and(shr(224, h), 0xffff)
                if or(lt(a, 1), gt(a, 20)) { fail() }
                if or(lt(w, 1), gt(w, 256)) { fail() }
                let ds := add(add(at, 4), mul(3, a))
                let end := add(ds, shr(3, add(mul(shl(a, 1), w), 7)))
                if gt(end, fileLen) { fail() }
                mstore(add(ctx, C_END), end)
                mstore(add(ctx, C_SEND), ds)
                mstore(add(ctx, C_G), add(w, a))
                let romc := add(sub(shr(14, sub(end, 1)), shr(14, ds)), 1)
                if gt(romc, 2) { romc := 2 }
                mstore(add(ctx, C_RC), romc)
                mstore(add(ctx, C_ROMA), a)
                mstore(add(ctx, C_ROMW), w)
                mstore(add(ctx, C_ROMDS), ds)
                extDelta := copyRefs(ctx, add(at, 4), a)
            }
            // RAM read port 0x11, RAM write port 0x12, ROM read port 0x13: the header (5 or 8 bytes) is checked
            // against fileLen, then the dimensions, then the record length, before the references are read; the
            // structural bytes are the whole record; a RAM port adds MEM_WEIGHT = 4 external units of its own
            function parsePort(ctx, at, op, fileLen) -> extDelta {
                let hl := 5
                if eq(op, 0x13) { hl := 8 }
                if gt(add(at, hl), fileLen) { fail() }
                let h := load(ctx, at, hl)
                let a := byte(2, h)
                let w := or(shl(8, byte(3, h)), byte(4, h))
                let bank := byte(1, h)
                if eq(op, 0x13) {
                    a := byte(5, h)
                    w := or(shl(8, byte(6, h)), byte(7, h))
                    bank := 0
                }
                if or(or(lt(a, 1), gt(a, 20)), or(lt(w, 1), gt(w, 256))) { fail() }
                let nrefs := a
                let romc := 0
                switch op
                case 0x13 {
                    let ds := add(add(shr(224, shl(8, h)), 4), mul(3, a))
                    let dend := add(ds, shr(3, add(mul(shl(a, 1), w), 7)))
                    romc := add(sub(shr(14, sub(dend, 1)), shr(14, ds)), 1)
                    if gt(romc, 2) { romc := 2 }
                    mstore(add(ctx, C_ROMDS), ds)
                    mstore(add(ctx, C_G), add(w, a))
                }
                default {
                    let st := 1
                    for {} lt(st, w) {} { st := shl(1, st) }
                    if gt(shl(a, st), 0x1000000) { fail() }
                    switch op
                    case 0x12 {
                        nrefs := add(add(1, a), w)
                        mstore(add(ctx, C_G), add(add(1, a), w))
                    }
                    default { mstore(add(ctx, C_G), add(w, a)) }
                }
                let end := add(add(at, hl), mul(3, nrefs))
                if gt(end, fileLen) { fail() }
                mstore(add(ctx, C_END), end)
                mstore(add(ctx, C_SEND), end)
                mstore(add(ctx, C_RC), romc)
                mstore(add(ctx, C_ROMA), a)
                mstore(add(ctx, C_ROMW), w)
                mstore(add(ctx, C_BANK), bank)
                extDelta := copyRefs(ctx, add(at, hl), nrefs)
                if iszero(eq(op, 0x13)) { extDelta := add(extDelta, 4) }
            }
            function parse(ctx, at, l) {
                let fileLen := mload(add(ctx, C_FLEN))
                if gt(add(at, 4), fileLen) { fail() }
                let h := load(ctx, at, 4)
                let op := shr(248, h)
                mstore(add(ctx, C_OP), op)
                let ext := mload(add(ctx, C_EXT))
                switch op
                case 0 {
                    let end := add(at, 7)
                    if gt(end, fileLen) { fail() }
                    mstore(add(ctx, C_END), end)
                    mstore(add(ctx, C_SEND), end)
                    mstore(add(ctx, C_G), 1)
                    mstore(add(ctx, C_RC), 0)
                    let x := and(shr(224, h), 0xffffff)
                    let y := shr(232, load(ctx, add(at, 4), 3))
                    mstore(add(ctx, C_RX), x)
                    mstore(add(ctx, C_RY), y)
                    let fs := mload(add(ctx, C_FS))
                    ext := add(add(ext, markP(ctx, x, fs)), markP(ctx, y, fs))
                }
                case 1 {
                    mstore(add(ctx, C_END), add(at, 4))
                    mstore(add(ctx, C_SEND), add(at, 4))
                    mstore(add(ctx, C_G), 1)
                    mstore(add(ctx, C_RC), 0)
                    mstore(add(ctx, C_RX), and(shr(224, h), 0xffffff))
                    let sk := add(shr(8, l), 1)
                    if iszero(eq(sk, mload(add(ctx, C_SLAST)))) {
                        ext := add(ext, 1)
                        mstore(add(ctx, C_SLAST), sk)
                    }
                }
                case 0x10 { ext := add(ext, parseRom(ctx, at, h, fileLen)) }
                case 0x11 { ext := add(ext, parsePort(ctx, at, op, fileLen)) }
                case 0x12 { ext := add(ext, parsePort(ctx, at, op, fileLen)) }
                case 0x13 { ext := add(ext, parsePort(ctx, at, op, fileLen)) }
                default { fail() }
                mstore(add(ctx, C_EXTNEW), ext)
            }
            // cost of the segment including the parsed record; commits it unless it is over the budget
            // and the segment already has a record (then 0: the segment ends before it)
            function admit(ctx, at, nrec) -> ok {
                // distinct structural chunks: ranges of successive records never go backwards
                let sc := mload(add(ctx, C_SCOUNT))
                let last := mload(add(ctx, C_SLASTCH))
                let c1 := add(shr(14, sub(mload(add(ctx, C_SEND)), 1)), 1)
                if gt(c1, last) {
                    let first := shr(14, at)
                    if gt(last, first) { first := last }
                    sc := add(sc, sub(c1, first))
                    last := c1
                }
                let gates := add(mload(add(ctx, C_GATES)), mload(add(ctx, C_G)))
                let rc := add(mload(add(ctx, C_ROMCH)), mload(add(ctx, C_RC)))
                let ext := mload(add(ctx, C_EXTNEW))
                if nrec {
                    if or(
                        or(gt(gates, mload(add(ctx, C_BG))), gt(ext, mload(add(ctx, C_BE)))),
                        gt(add(sc, rc), mload(add(ctx, C_BC)))
                    ) { leave }
                }
                mstore(add(ctx, C_GATES), gates)
                mstore(add(ctx, C_EXT), ext)
                mstore(add(ctx, C_SCOUNT), sc)
                mstore(add(ctx, C_SLASTCH), last)
                mstore(add(ctx, C_ROMCH), rc)
                ok := 1
            }
            function evaluate(ctx, l) -> l2 {
                l2 := l
                switch mload(add(ctx, C_OP))
                case 0 {
                    let fs := mload(add(ctx, C_FS))
                    appendP(ctx, sub(1, and(val(ctx, mload(add(ctx, C_RX)), fs), val(ctx, mload(add(ctx, C_RY)), fs))))
                }
                case 1 {
                    let v := 0
                    if iszero(mload(add(ctx, C_ZERO))) { v := stateBit(ctx, l) }
                    appendD(ctx, mload(add(ctx, C_RX)))
                    appendP(ctx, v)
                    l2 := add(l, 1)
                }
                case 0x10 { evalRom(ctx, 0) }
                case 0x13 { evalRom(ctx, 1) }
                case 0x11 { evalRamRead(ctx) }
                default { evalRamWrite(ctx) }
            }

            mstore(add(c, C_HOFF), off)
            mstore(add(c, C_LATCUR), lat)
            for { let nrec := 0 } 1 { nrec := 1 } {
                if nrec {
                    hot(c)
                    if mload(add(c, C_HST)) { break }
                }
                let at := mload(add(c, C_HOFF))
                if iszero(lt(at, mload(add(c, C_FLEN)))) { break }
                parse(c, at, mload(add(c, C_LATCUR)))
                if iszero(admit(c, at, nrec)) { break }
                mstore(add(c, C_LATCUR), evaluate(c, mload(add(c, C_LATCUR))))
                mstore(add(c, C_HOFF), mload(add(c, C_END)))
            }
            off := mload(add(c, C_HOFF))
            lat := mload(add(c, C_LATCUR))
            mstore(add(c, C_OFFEND), off)
            mstore(add(c, C_LATEND), lat)
            mstore(add(c, C_SIG), add(shl(8, mload(add(c, C_PACC))), mload(add(c, add(C_PACC, 0x20)))))
        }
    }

    // =====================================================================================================
    // Beat-end verifiers (§6.2)
    // =====================================================================================================

    /// @notice verify_beat_end(netlistHash2, nOut, seg_m, a, b, witness) of §6.2 (output and state kinds).
    function verifyBeatEnd(
        bytes32 netlistHash2,
        uint32 nOut,
        SegTuple calldata segM,
        EndTuple calldata a,
        EndTuple calldata b,
        bytes calldata witness
    ) external view returns (bool) {
        // step 1
        if (segM.byteOffset != _fileLen(netlistHash2)) _fail();
        // witness: kind ‖ words(P) ‖ hashes16 ‖ words(D) ‖ hashes16 ‖ hashes8(peaks)
        uint256 c = _alloc(0x240);
        _parseEndWitness(c, _cdOffset(witness), witness.length);
        // step 2
        uint256 S = segM.firstSignal;
        if (!_checkWords(segM.p, (S + 255) >> 8, 0x04, _ld(c, 0x20), _ld(c, 0x40), _ld(c, 0x60), _ld(c, 0x100))) {
            _fail();
        }
        // step 3
        uint256 total;
        uint8 dom;
        if (_ld(c, 0x00) == 1) {
            if (_ld(c, 0xa0) != 0 || _ld(c, 0x120) != 0) _fail();
            if (nOut < 1 || uint256(nOut) + 2 > S) _fail();
            total = nOut;
            dom = 0x04;
        } else {
            total = segM.latchOrdinal;
            dom = 0x05;
            if (!_checkWords(segM.d, (total + 7) >> 3, 0x09, _ld(c, 0x80), _ld(c, 0xa0), _ld(c, 0xc0), _ld(c, 0x120))) {
                _fail();
            }
        }
        assembly ("memory-safe") {
            mstore(add(c, 0x1e0), S)
            mstore(add(c, 0x200), nOut)
            mstore(add(c, 0x220), total)
        }
        return _beatEndTail(c, dom, a, b);
    }

    /// @dev Steps 4 to 7 of §6.2; the context holds S (0x1e0), nOut (0x200) and total (0x220).
    function _beatEndTail(uint256 c, uint8 dom, EndTuple calldata a, EndTuple calldata b) internal view returns (bool) {
        // step 4
        uint256 lo = a.ordinal;
        uint256 total = _ld(c, 0x220);
        if (lo & 255 != 0 || lo >= total) _fail();
        // step 5: append proof, peaks only
        uint256 acc = _alloc(0xa0);
        _accSetup(acc);
        if (!_accInit(acc, dom, false, lo, c + 0x160, a.root)) _fail();
        // step 6
        uint256 hi = lo + 256 < total ? lo + 256 : total;
        uint256 part = _endValues(c, _ld(c, 0x1e0), _ld(c, 0x200), lo, hi);
        assembly ("memory-safe") {
            mstore(add(acc, 0x20), sub(hi, lo))
            mstore(add(acc, 0x40), part)
        }
        if (hi - lo == 256) _accPush(acc, _wordLeaf(dom, lo >> 8, bytes32(part))); // a full word
        // step 7
        return b.ordinal == hi && b.root == _accRoot(acc, dom, false);
    }

    /// @dev The values i = lo .. hi - 1 of a beat-end step, packed as a bit word (bit i - lo).
    function _endValues(uint256 c, uint256 S, uint256 nOut, uint256 lo, uint256 hi) internal pure returns (uint256 part) {
        bool out = _ld(c, 0x00) == 1;
        uint256 pwOff = _ld(c, 0x20);
        uint256 pwN = _ld(c, 0x40);
        for (uint256 i = lo; i < hi; ++i) {
            uint256 x = out ? S - nOut + i : _u32At(_ld(c, 0x80), _ld(c, 0xa0), i);
            uint256 v;
            if (x < 2) {
                v = x;
            } else {
                if (x >= S) _fail();
                v = _bit(_wordAt(pwOff, pwN, x >> 8), x & 255);
            }
            if (v != 0) part |= uint256(1) << (248 - ((i - lo) & 0xf8) + ((i - lo) & 7));
        }
    }

    /// @dev Witness encoding of §6.3 (beat-end). Context: 0x00 kind, 0x20/0x40 P words, 0x60 P proof offset,
    ///      0x80/0xa0 D words, 0xc0 D proof offset, 0x100 P proof count, 0x120 D proof count, and the append
    ///      proof block 0x160 peaks offset, 0x180 peaks count, 0x1a0/0x1c0 zero (no partial word).
    function _parseEndWitness(uint256 c, uint256 p, uint256 len) internal pure {
        assembly ("memory-safe") {
            function fail() {
                mstore(0, 0)
                mstore(0x20, 0)
                return(0, 0x40)
            }
            function words(q, end, cOff, nOff, ctx) -> r {
                if gt(add(q, 2), end) { fail() }
                let n := shr(240, calldataload(q))
                q := add(q, 2)
                if gt(add(q, mul(n, 36)), end) { fail() }
                mstore(add(ctx, cOff), q)
                mstore(add(ctx, nOff), n)
                let last := 0
                for { let i := 0 } lt(i, n) { i := add(i, 1) } {
                    let k := shr(224, calldataload(add(q, mul(i, 36))))
                    if and(gt(i, 0), iszero(gt(k, last))) { fail() }
                    last := k
                }
                r := add(q, mul(n, 36))
            }
            function hashes(q, end, cb, cOff, nOff, ctx) -> r {
                if gt(add(q, cb), end) { fail() }
                let n := shr(sub(256, shl(3, cb)), calldataload(q))
                q := add(q, cb)
                if gt(add(q, shl(5, n)), end) { fail() }
                mstore(add(ctx, cOff), q)
                mstore(add(ctx, nOff), n)
                r := add(q, shl(5, n))
            }
            let e := add(p, len)
            if gt(add(p, 1), e) { fail() }
            let kind := shr(248, calldataload(p))
            if iszero(or(eq(kind, 1), eq(kind, 2))) { fail() }
            mstore(c, kind)
            let q := words(add(p, 1), e, 0x20, 0x40, c)
            q := hashes(q, e, 2, 0x60, 0x100, c)
            q := words(q, e, 0x80, 0xa0, c)
            q := hashes(q, e, 2, 0xc0, 0x120, c)
            q := hashes(q, e, 1, 0x160, 0x180, c)
            if iszero(eq(q, e)) { fail() }
        }
    }

    // =====================================================================================================
    // Shared helpers
    // =====================================================================================================

    function _fail() internal pure {
        assembly ("memory-safe") {
            mstore(0, 0)
            mstore(0x20, 0)
            return(0, 0x40)
        }
    }

    function _alloc(uint256 size) internal pure returns (uint256 p) {
        assembly ("memory-safe") {
            p := mload(0x40)
            calldatacopy(p, calldatasize(), size)
            mstore(0x40, add(p, size))
        }
    }

    function _cdOffset(bytes calldata x) internal pure returns (uint256 o) {
        assembly ("memory-safe") {
            o := x.offset
        }
    }

    function _ld(uint256 c, uint256 off) internal pure returns (uint256 v) {
        assembly ("memory-safe") {
            v := mload(add(c, off))
        }
    }

    /// fileLen from the chunk oracle; fails unless largeStored (largeInfo(...).stored) is true.
    function _fileLen(bytes32 nh2) internal view returns (uint256 len) {
        address st = address(store);
        assembly ("memory-safe") {
            let q := mload(0x40)
            mstore(q, SEL_LARGE_INFO)
            mstore(add(q, 4), nh2)
            let ok := staticcall(gas(), st, q, 36, q, 0xa0)
            if or(iszero(ok), lt(returndatasize(), 0xa0)) {
                mstore(0, 0)
                mstore(0x20, 0)
                return(0, 0x40)
            }
            if iszero(mload(add(q, 0x80))) {
                mstore(0, 0)
                mstore(0x20, 0)
                return(0, 0x40)
            }
            len := mload(q)
        }
    }

    function _node(bytes32 l, bytes32 r) internal view returns (bytes32 h) {
        assembly {
            let fmp := mload(0x40)
            mstore(0x01, l)
            mstore(0x21, r)
            mstore8(0x00, 0x01)
            if iszero(staticcall(gas(), 0x02, 0x00, 65, 0x00, 0x20)) { revert(0, 0) }
            h := mload(0x00)
            mstore(0x40, fmp)
        }
    }

    function _wordLeaf(uint8 dom, uint256 k, bytes32 w) internal view returns (bytes32 h) {
        assembly ("memory-safe") {
            mstore(0x01, shl(224, k))
            mstore(0x05, w)
            mstore8(0x00, dom)
            if iszero(staticcall(gas(), 0x02, 0x00, 37, 0x00, 0x20)) { revert(0, 0) }
            h := mload(0x00)
        }
    }

    /// Index of entry i of a `words` list in calldata.
    function _idxAt(uint256 off, uint256 i) internal pure returns (uint256 k) {
        assembly ("memory-safe") {
            k := shr(224, calldataload(add(off, mul(i, 36))))
        }
    }

    /// Word with index k of a sorted `words` list (binary search); fails if absent.
    function _wordAt(uint256 off, uint256 n, uint256 k) internal pure returns (uint256 w) {
        uint256 lo;
        uint256 hi = n;
        while (lo < hi) {
            uint256 mid = (lo + hi) >> 1;
            uint256 km = _idxAt(off, mid);
            if (km == k) {
                assembly ("memory-safe") {
                    w := calldataload(add(add(off, mul(mid, 36)), 4))
                }
                return w;
            }
            if (km < k) lo = mid + 1;
            else hi = mid;
        }
        _fail();
    }

    /// u32 value i of a d-table given by sorted u32 `words` (8 values per word); fails if absent.
    function _u32At(uint256 off, uint256 n, uint256 i) internal pure returns (uint256) {
        return (_wordAt(off, n, i >> 3) >> (224 - 32 * (i & 7))) & 0xffffffff;
    }

    function _bit(uint256 word, uint256 o) internal pure returns (uint256) {
        return (word >> (248 - (o & 0xf8) + (o & 7))) & 1;
    }

    /// Accumulator layout: +0 full, +0x20 pcount, +0x40 partial, +0x60 cnt, +0x80 stack pointer (40 words).
    function _accSetup(uint256 acc) internal pure {
        uint256 st = _alloc(40 * 32);
        assembly ("memory-safe") {
            mstore(add(acc, 0x80), st)
        }
    }

    /// Accept an append proof (§4.2) at length `count` with root `expect`: number of peaks against
    /// full = count / per, the partial word against count % per (unused slots zero), and the recovered root.
    /// `f` points to (peaks calldata offset, peaks count, partial flag, partial word).
    function _accInit(uint256 acc, uint8 dom, bool u32, uint256 count, uint256 f, bytes32 expect)
        internal
        view
        returns (bool)
    {
        if (!_frontierShape(u32, count, f)) return false;
        uint256 full = u32 ? count >> 3 : count >> 8;
        uint256 pcount = u32 ? count & 7 : count & 255;
        assembly ("memory-safe") {
            mstore(acc, full)
            mstore(add(acc, 0x20), pcount)
            mstore(add(acc, 0x40), mload(add(f, 0x60)))
            let peaksN := mload(add(f, 0x20))
            mstore(add(acc, 0x60), peaksN)
            calldatacopy(mload(add(acc, 0x80)), mload(f), shl(5, peaksN))
        }
        return _accRoot(acc, dom, u32) == expect;
    }

    /// The checks of an append proof that do not need hashing (§4.2).
    function _frontierShape(bool u32, uint256 count, uint256 f) internal pure returns (bool) {
        uint256 full = u32 ? count >> 3 : count >> 8;
        uint256 pcount = u32 ? count & 7 : count & 255;
        uint256 pop;
        for (; full != 0; full &= full - 1) ++pop;
        if (_ld(f, 0x20) != pop) return false;
        uint256 hasPartial = _ld(f, 0x40);
        if ((hasPartial != 0) != (pcount != 0)) return false;
        if (hasPartial == 0) return true;
        uint256 used;
        if (u32) {
            used = ~uint256(0) << (256 - 32 * pcount);
        } else {
            uint256 nb = pcount >> 3;
            used = (nb == 0 ? 0 : ~uint256(0) << (256 - 8 * nb)) | (((uint256(1) << (pcount & 7)) - 1) << (248 - 8 * nb));
        }
        return _ld(f, 0x60) & ~used == 0;
    }

    function _accPush(uint256 acc, bytes32 leaf) internal view {
        uint256 full;
        uint256 cnt;
        uint256 st;
        assembly ("memory-safe") {
            full := mload(acc)
            cnt := mload(add(acc, 0x60))
            st := mload(add(acc, 0x80))
        }
        bytes32 h = leaf;
        for (uint256 f = full; f & 1 == 1; f >>= 1) {
            --cnt;
            bytes32 left;
            assembly ("memory-safe") {
                left := mload(add(st, shl(5, cnt)))
            }
            h = _node(left, h);
        }
        assembly ("memory-safe") {
            mstore(add(st, shl(5, cnt)), h)
            mstore(add(acc, 0x60), add(cnt, 1))
            mstore(acc, add(full, 1))
            mstore(add(acc, 0x20), 0)
            mstore(add(acc, 0x40), 0)
        }
    }

    /// fold(peaks, wordLeaf(partial) if any) of §4.2; EMPTY if both are absent.
    function _accRoot(uint256 acc, uint8 dom, bool) internal view returns (bytes32 r) {
        uint256 full;
        uint256 pcount;
        uint256 part;
        uint256 cnt;
        uint256 st;
        assembly ("memory-safe") {
            full := mload(acc)
            pcount := mload(add(acc, 0x20))
            part := mload(add(acc, 0x40))
            cnt := mload(add(acc, 0x60))
            st := mload(add(acc, 0x80))
        }
        if (pcount == 0) {
            if (cnt == 0) return bytes32(0);
            --cnt;
            assembly ("memory-safe") {
                r := mload(add(st, shl(5, cnt)))
            }
        } else {
            r = _wordLeaf(dom, full, bytes32(part));
        }
        while (cnt != 0) {
            --cnt;
            bytes32 left;
            assembly ("memory-safe") {
                left := mload(add(st, shl(5, cnt)))
            }
            r = _node(left, r);
        }
    }

    /// _check_words of the reference: an empty word set needs an empty proof (root and size are not used);
    /// otherwise the words (leaves dom ‖ u32be(k) ‖ word) must verify against root with tree size `size`.
    function _checkWords(
        bytes32 root,
        uint256 size,
        uint8 dom,
        uint256 wordsOff,
        uint256 n,
        uint256 proofOff,
        uint256 proofN
    ) internal view returns (bool ok) {
        if (n == 0) return proofN == 0;
        if (size == 0 || _idxAt(wordsOff, n - 1) >= size) return false;
        // pr: (proof offset, proof length, proof elements consumed, dom, words offset)
        uint256 pr = _alloc(0xa0);
        assembly ("memory-safe") {
            mstore(pr, proofOff)
            mstore(add(pr, 0x20), proofN)
            mstore(add(pr, 0x80), wordsOff)
        }
        return _multiverify(pr, root, size, n, dom);
    }

    function _multiverify(uint256 pr, bytes32 root, uint256 size, uint256 n, uint8 dom) internal view returns (bool ok) {
        assembly ("memory-safe") {
            // multiverify of the reference, level by level over the known nodes in increasing index order.
            // Known nodes are (index, hash) entries of 64 bytes, rewritten in place at each level. sb is a
            // hashing buffer: sb[0] = 0x01, sb+1 left, sb+33 right, sb+0x60 output, sb+0x80 / sb+0xa0 the proof
            // cursor and end (calldata). Returns the root hash and the final proof cursor (0 if the proof ran out).
            function reduce(width, k, arr, sb) -> h0, fin {
                let pp := mload(add(sb, 0x80))
                for {} gt(width, 1) {} {
                    let rd := arr
                    let wr := arr
                    let endp := add(arr, shl(6, k))
                    for {} lt(rd, endp) {} {
                        let i := mload(rd)
                        switch and(i, 1)
                        case 0 {
                            switch lt(add(i, 1), width)
                            case 0 { mstore(add(sb, 0x60), mload(add(rd, 32))) } // odd last node moves up
                            default {
                                mstore(add(sb, 1), mload(add(rd, 32)))
                                switch and(lt(add(rd, 64), endp), eq(mload(add(rd, 64)), add(i, 1)))
                                case 1 {
                                    mstore(add(sb, 33), mload(add(rd, 96)))
                                    rd := add(rd, 64)
                                }
                                default {
                                    if iszero(lt(pp, mload(add(sb, 0xa0)))) { leave }
                                    mstore(add(sb, 33), calldataload(pp))
                                    pp := add(pp, 32)
                                }
                                if iszero(staticcall(gas(), 0x02, sb, 65, add(sb, 0x60), 0x20)) { revert(0, 0) }
                            }
                        }
                        default {
                            if iszero(lt(pp, mload(add(sb, 0xa0)))) { leave }
                            mstore(add(sb, 1), calldataload(pp))
                            mstore(add(sb, 33), mload(add(rd, 32)))
                            pp := add(pp, 32)
                            if iszero(staticcall(gas(), 0x02, sb, 65, add(sb, 0x60), 0x20)) { revert(0, 0) }
                        }
                        mstore(wr, shr(1, i))
                        mstore(add(wr, 32), mload(add(sb, 0x60)))
                        wr := add(wr, 64)
                        rd := add(rd, 64)
                    }
                    k := shr(6, sub(wr, arr))
                    width := shr(1, add(width, 1))
                }
                h0 := mload(add(arr, 32))
                fin := pp
            }
            let sb := mload(0x40)
            let arr := add(sb, 0xc0)
            mstore(0x40, add(arr, shl(6, n)))
            mstore8(sb, 0x01)
            let po := mload(pr)
            mstore(add(sb, 0x80), po)
            mstore(add(sb, 0xa0), add(po, shl(5, mload(add(pr, 0x20)))))
            let wo := mload(add(pr, 0x80))
            for { let j := 0 } lt(j, n) { j := add(j, 1) } {
                let e := add(wo, mul(j, 36))
                let i := shr(224, calldataload(e))
                mstore(add(arr, shl(6, j)), i)
                mstore(0x01, shl(224, i))
                mstore(0x05, calldataload(add(e, 4)))
                mstore8(0x00, dom)
                if iszero(staticcall(gas(), 0x02, 0x00, 37, 0x00, 0x20)) { revert(0, 0) }
                mstore(add(arr, add(shl(6, j), 32)), mload(0x00))
            }
            let h0, fin := reduce(size, n, arr, sb)
            ok := and(eq(fin, mload(add(sb, 0xa0))), eq(h0, root))
        }
    }
}
