"""Binary case files read by the Solidity tests (test/utils/Cases.sol). Big-endian throughout.

file  = "TDC1" ‖ u32(nFiles) ‖ { u32(len) ‖ bytes }* ‖ u32(nCases) ‖ case*
case  = u8(type: 0 segment, 1 beat-end) ‖ u32(fileIdx) ‖ u8(oracle: 0 file) ‖ u8(expect: 0, 1, 2 = not asserted)
        ‖ u32(info) ‖ body ‖ u32(wlen) ‖ witness ‖ u16(nameLen) ‖ name
segment body  = stateRoot ‖ tuple(a) ‖ tuple(b) ‖ u32(gates) ‖ u32(extWords) ‖ u32(chunks)
beat-end body = u32(nOut) ‖ tuple(segM) ‖ u32(a.ordinal) ‖ a.root ‖ u32(b.ordinal) ‖ b.root
tuple = u64(byteOffset) ‖ u32(firstSignal) ‖ u32(latchOrdinal) ‖ P ‖ D
"""
import struct


def _tuple(t) -> bytes:
    return struct.pack(">QII", t.byte_offset, t.first_signal, t.latch_ordinal) + t.p + t.d


def seg_case(file_idx, a, b, sroot, budget, witness: bytes, expect, name: str, info: int = 0, oracle: int = 0) -> bytes:
    e = 2 if expect is None else int(bool(expect))
    out = struct.pack(">BIBBI", 0, file_idx, oracle, e, info) + sroot + _tuple(a) + _tuple(b)
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
        f.write(b"TDC1" + struct.pack(">I", len(files)))
        for d in files:
            f.write(struct.pack(">I", len(d)) + d)
        f.write(struct.pack(">I", len(cases)))
        for c in cases:
            f.write(c)
