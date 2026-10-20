"""Binary case files read by the Solidity tests (test/utils/Cases.sol). Big-endian throughout.

file  = "TDC2" ‖ u32(nFiles) ‖ { u32(len) ‖ bytes }* ‖ u32(nCases) ‖ case*
case  = u8(type: 0 segment, 1 beat-end) ‖ u32(fileIdx) ‖ u8(oracle: 0 file) ‖ u8(expect: 0, 1, 2 = not asserted)
        ‖ u32(info) ‖ body ‖ u32(wlen) ‖ witness ‖ u16(nameLen) ‖ name
segment body  = latchRoot ‖ memRoot ‖ tuple(a) ‖ tuple(b) ‖ u32(gates) ‖ u32(extWords) ‖ u32(chunks)
beat-end body = u32(nOut) ‖ tuple(segM) ‖ u32(a.ordinal) ‖ a.root ‖ u32(b.ordinal) ‖ b.root
tuple = u64(byteOffset) ‖ u32(firstSignal) ‖ u32(latchOrdinal) ‖ P ‖ D ‖ M   (M = ZERO_MEM for files without MEM)
"""
import struct

ZERO_MEM = bytes.fromhex("073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff")


def _tuple(t) -> bytes:
    return struct.pack(">QII", t.byte_offset, t.first_signal, t.latch_ordinal) + t.p + t.d + t.m


def seg_case(file_idx, a, b, latch_root, budget, witness: bytes, expect, name: str, info: int = 0, oracle: int = 0,
             mem_root: bytes = None) -> bytes:
    """mem_root: the memRoot argument of verify_segment (default ZERO_MEM)."""
    e = 2 if expect is None else int(bool(expect))
    mr = ZERO_MEM if mem_root is None else mem_root
    out = struct.pack(">BIBBI", 0, file_idx, oracle, e, info) + latch_root + mr + _tuple(a) + _tuple(b)
    out += struct.pack(">III", budget.gates, budget.ext_words, budget.chunks)
    nb = name.encode()
    return out + struct.pack(">I", len(witness)) + witness + struct.pack(">H", len(nb)) + nb


def end_case(file_idx, n_out, seg_m, a, b, witness: bytes, expect, name: str, info: int = 0, oracle: int = 0) -> bytes:
    e = 2 if expect is None else int(bool(expect))
    out = struct.pack(">BIBBI", 1, file_idx, oracle, e, info) + struct.pack(">I", n_out) + _tuple(seg_m)
    out += struct.pack(">I", a.ordinal) + a.root + struct.pack(">I", b.ordinal) + b.root
    nb = name.encode()
    return out + struct.pack(">I", len(witness)) + witness + struct.pack(">H", len(nb)) + nb


def write(path: str, files, cases) -> None:
    with open(path, "wb") as f:
        f.write(b"TDC2" + struct.pack(">I", len(files)))
        for d in files:
            f.write(struct.pack(">I", len(d)) + d)
        f.write(struct.pack(">I", len(cases)))
        for c in cases:
            f.write(c)
