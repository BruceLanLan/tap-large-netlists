"""Deterministic example circuits and invalid samples for tapnet/2 (generated in code)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

import tapnet2 as t2


class Builder:
    def __init__(self, n_in: int):
        self.n_in = n_in
        self.next = 2 + n_in
        self.buf = bytearray()

    def inp(self, i: int) -> int:
        return 2 + i

    def nand(self, x: int, y: int) -> int:
        self.buf += t2.enc_nand(x, y)
        self.next += 1
        return self.next - 1

    def not_(self, x: int) -> int:
        return self.nand(x, x)

    def and_(self, x: int, y: int) -> int:
        return self.not_(self.nand(x, y))

    def xor(self, x: int, y: int) -> Tuple[int, int]:
        """Returns (x XOR y, NAND(x, y))."""
        n1 = self.nand(x, y)
        return self.nand(self.nand(x, n1), self.nand(y, n1)), n1

    def latch(self) -> Tuple[int, int]:
        """Returns (signal, position) — set the d reference later with set_d."""
        pos = len(self.buf)
        self.buf += t2.enc_latch(0)
        self.next += 1
        return self.next - 1, pos

    def set_d(self, pos: int, d: int) -> None:
        self.buf[pos + 1:pos + 4] = t2.u24(d)

    def rom(self, addr: Sequence[int], w: int, entries: Sequence[int]) -> List[int]:
        a = len(addr)
        self.buf += t2.enc_rom(addr, w, t2.pack_rom_data(entries, a, w))
        outs = list(range(self.next, self.next + w))
        self.next += w
        return outs

    def file(self, n_out: int) -> bytes:
        data = t2.make_file(bytes(self.buf), self.n_in, n_out)
        t2.check_file(data)
        return data


class XorShift:
    """xorshift64*: a fixed PRNG so examples do not depend on Python's random module."""

    def __init__(self, seed: int):
        self.s = seed or 1

    def next(self) -> int:
        s = self.s
        s ^= (s >> 12) & 0xFFFFFFFFFFFFFFFF
        s ^= (s << 25) & 0xFFFFFFFFFFFFFFFF
        s ^= (s >> 27) & 0xFFFFFFFFFFFFFFFF
        self.s = s & 0xFFFFFFFFFFFFFFFF
        return (self.s * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF

    def below(self, n: int) -> int:
        return (self.next() >> 11) % n

    def bits(self, n: int) -> List[int]:
        return [self.below(2) for _ in range(n)]


def aes_sbox() -> List[int]:
    def gmul(a: int, b: int) -> int:
        p = 0
        while b:
            if b & 1:
                p ^= a
            a = ((a << 1) ^ 0x11B) if a & 0x80 else (a << 1)
            b >>= 1
        return p

    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if gmul(a, b) == 1:
                inv[a] = b
                break
    rotl = lambda x, k: ((x << k) | (x >> (8 - k))) & 0xFF  # noqa: E731
    return [inv[x] ^ rotl(inv[x], 1) ^ rotl(inv[x], 2) ^ rotl(inv[x], 3) ^ rotl(inv[x], 4) ^ 0x63 for x in range(256)]


def sbox_file() -> bytes:
    """8-bit AES S-box as one ROM (a = 8, w = 8). Inputs x[0..7] LSB first, outputs S(x) LSB first."""
    b = Builder(8)
    b.rom([b.inp(i) for i in range(8)], 8, aes_sbox())
    return b.file(8)


def mul4_file() -> bytes:
    """4x4-bit multiplication table ROM: inputs x (bits 0..3) and y (bits 4..7), output x*y (8 bits)."""
    b = Builder(8)
    b.rom([b.inp(i) for i in range(8)], 8, [(k & 15) * (k >> 4) for k in range(256)])
    return b.file(8)


MESSAGE = b"Hello from tapnet/2 ROM!\n"
MESSAGE_DELAY = 8   # beats of silence before the message


def message_file() -> bytes:
    """Sequential: a 6-bit counter (LATCHes, enable input) addresses a ROM (a = 6, w = 8) holding zeros
    for MESSAGE_DELAY beats and then MESSAGE. Output = the character for the current beat."""
    b = Builder(1)
    qs = [b.latch() for _ in range(6)]
    carry = b.inp(0)
    for q, pos in qs:
        x, n1 = b.xor(q, carry)
        b.set_d(pos, x)
        carry = b.not_(n1)                 # q AND carry
    entries = [0] * 64
    for i, ch in enumerate(MESSAGE):
        entries[MESSAGE_DELAY + i] = ch
    b.rom([q for q, _ in qs], 8, entries)
    return b.file(8)


def large_file() -> bytes:
    """Synthetic file larger than 49,152 bytes (6 chunks): 64 LATCHes, ROM A (S-box), 4,000 NANDs, ROM B
    (a = 15, w = 8, 32 KiB of data spanning 3 chunks), 4,000 NANDs, ROM C (a = 4, w = 4), 16 output NANDs."""
    rng = XorShift(0x7A9E7A9E)
    b = Builder(16)
    latches = [b.latch() for _ in range(64)]
    rom_a = b.rom([b.inp(i) for i in range(8)], 8, aes_sbox())

    def nand_block(n: int) -> None:
        for _ in range(n):
            hi = b.next
            def pick() -> int:
                if rng.below(4):
                    return hi - 1 - rng.below(min(32, hi - 2))
                return 2 + rng.below(hi - 2)
            b.nand(pick(), pick())

    nand_block(4000)
    rom_b = b.rom([2 + rng.below(b.next - 2) for _ in range(15)], 8, [rng.below(256) for _ in range(1 << 15)])
    nand_block(4000)
    rom_c = b.rom([b.next - 1 - i for i in range(4)], 4, [rng.below(16) for _ in range(16)])
    for i, (_, pos) in enumerate(latches):
        src = (rom_a, rom_b)[i % 2]
        b.set_d(pos, src[i % 8] if i < 16 else 2 + rng.below(b.next - 2))
    for i in range(16):
        b.nand(rom_c[i % 4], rom_b[i % 8])
    return b.file(16)


def nand_only_file(n: int = 21000) -> bytes:
    """Measurement only: n NANDs with mostly local wiring, 16 inputs, 16 outputs."""
    rng = XorShift(0x5EED)
    b = Builder(16)
    for _ in range(n):
        hi = b.next
        b.nand(hi - 1 - rng.below(min(64, hi - 2)), hi - 1 - rng.below(min(64, hi - 2)))
    return b.file(16)


def random_rom_file(rng: XorShift) -> bytes:
    """Small random netlist with ROMs and LATCHes (for the expansion equivalence test)."""
    n_in = 1 + rng.below(6)
    b = Builder(n_in)
    latches = [b.latch() for _ in range(rng.below(5))]
    for _ in range(2 + rng.below(4)):
        for _ in range(rng.below(6)):
            b.nand(2 + rng.below(b.next - 2), rng.below(b.next))
        a = 1 + rng.below(5)
        w = 1 + rng.below(10)
        b.rom([rng.below(b.next) for _ in range(a)], w, [rng.below(1 << w) for _ in range(1 << a)])
    for _ in range(1 + rng.below(6)):
        b.nand(rng.below(b.next), rng.below(b.next))
    for _, pos in latches:
        b.set_d(pos, rng.below(b.next))
    n_out = 1 + rng.below(min(8, b.next - 2 - n_in))
    return b.file(n_out)


EXAMPLES = {
    "sbox": sbox_file,
    "mul4": mul4_file,
    "message": message_file,
    "large": large_file,
}


def invalid_files() -> List[Tuple[str, bytes, str]]:
    """(name, file, reason fragment). Each must be rejected by check_file."""
    H = lambda n_in, n_out, v=2, r=0: t2.MAGIC + bytes([v, r]) + t2.u32(n_in) + t2.u32(n_out)  # noqa: E731
    nand = t2.enc_nand
    rom_hdr = lambda a, w: bytes([t2.OP_ROM, a]) + t2.u16(w)  # noqa: E731
    ok_rom = t2.enc_rom([2], 1, bytes([0b10]))                # a=1, w=1: 2 bits, 6 padding bits
    return [
        ("short_header", t2.MAGIC + b"\x02\x00\x00", "shorter than the 16-byte header"),
        ("bad_magic", b"TAPNEX" + H(1, 1)[6:] + nand(2, 2), "bad magic"),
        ("version_1", H(1, 1, v=1) + nand(2, 2), "unsupported version"),
        ("version_3", H(1, 1, v=3) + nand(2, 2), "unsupported version"),
        ("reserved_nonzero", H(1, 1, r=1) + nand(2, 2), "reserved byte"),
        ("no_records", H(1, 1), "no records"),
        ("n_out_zero", H(1, 0) + nand(2, 2), "nOut out of range"),
        ("n_in_too_large", H(65537, 1) + nand(2, 2), "nIn out of range"),
        ("ref_record", H(1, 1) + nand(2, 2) + bytes([2]) + b"\x00" * 30 + b"\x00\x01", "REF record"),
        ("unknown_opcode_03", H(1, 1) + bytes([3]) + b"\x00" * 6, "unknown opcode"),
        ("unknown_opcode_11", H(1, 1) + bytes([0x11, 1]) + t2.u16(1) + t2.u24(2) + b"\x00", "unknown opcode"),
        ("truncated_nand", H(1, 1) + nand(2, 2)[:5], "truncated NAND"),
        ("truncated_latch", H(1, 1) + nand(2, 2) + t2.enc_latch(3)[:2], "truncated LATCH"),
        ("nand_forward_ref", H(1, 1) + nand(2, 3), "not available"),
        ("latch_ref_beyond_end", H(1, 1) + t2.enc_latch(4), "beyond the last signal"),
        ("too_few_signals_for_outputs", H(1, 2) + nand(2, 2), "too few signals"),
        ("rom_a_zero", H(1, 1) + rom_hdr(0, 1) + b"\x00", "address width"),
        ("rom_a_21", H(1, 1) + rom_hdr(21, 1) + t2.u24(2) * 21, "address width"),
        ("rom_w_zero", H(1, 1) + rom_hdr(1, 0) + t2.u24(2), "word width"),
        ("rom_w_257", H(1, 1) + rom_hdr(1, 257) + t2.u24(2) + b"\x00" * 65, "word width"),
        ("rom_truncated_header", H(1, 1) + rom_hdr(2, 1)[:3], "truncated ROM header"),
        ("rom_truncated_refs", H(1, 1) + rom_hdr(2, 1) + t2.u24(2), "truncated ROM"),
        ("rom_truncated_data", H(1, 1) + rom_hdr(3, 8) + t2.u24(2) * 3 + b"\x00" * 7, "truncated ROM"),
        ("rom_huge_declared_data", H(1, 1) + rom_hdr(20, 256) + t2.u24(2) * 20, "truncated ROM"),
        ("rom_forward_ref", H(1, 1) + t2.enc_rom([3], 1, b"\x00"), "not available"),
        ("rom_ref_own_output", H(1, 1) + t2.enc_rom([2, 3], 1, b"\x00"), "not available"),
        ("rom_nonzero_padding", H(1, 1) + ok_rom[:-1] + bytes([0b110]), "padding"),
        ("rom_nonzero_padding_w3", H(1, 3) + t2.enc_rom([2], 3, b"\x00")[:-1] + bytes([0x40]), "padding"),
    ]
