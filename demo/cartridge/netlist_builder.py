"""A small Python DSL for NAND / LATCH / ROM netlists, emitting tapnet/2 bytes through assets/tap-draft-large-netlists/tapnet2.py.

Signals are plain ints (tapnet signal indices). A bus is a list of signals, least significant bit first.

    b = NetBuilder(n_in=8)
    x = b.inputs()                     # [2, 3, ..., 9]
    with b.section("adder"):
        s, c = b.add(x[:4], x[4:])
    data = b.finish(outputs=s + [c])   # valid tapnet/2 bytes (checked by tapnet2.check_file)

Rules of the format the builder takes care of:
  * NAND and ROM address references must point backwards; LATCH d may point forwards. Create latches
    first with latch()/register(), use their outputs, and set their d inputs later with set_d().
  * The outputs must be the last nOut signals. finish() appends two fresh inverters per output
    (bypassing the memo below), so the outputs are contiguous and last.
  * Constant folding (NAND(0, y) = 1, NAND(1, y) = NOT y, NOT NOT x = x) and structural sharing of equal
    NAND gates keep the netlist small; both preserve behaviour exactly.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from typing import Dict, List, Optional, Sequence, Tuple

from _ref import t2

Bus = List[int]

ZERO, ONE = 0, 1


class NetBuilder:
    def __init__(self, n_in: int):
        self.n_in = n_in
        self.next = 2 + n_in
        self.recs: list = []                  # ("N", x, y) | ("L", d) | ("R", addr, w, entries)
        self.memo: Dict[Tuple[int, int], int] = {}
        self.inv: Dict[int, int] = {}         # s -> NOT s when known (both directions)
        self.latch_sig: List[int] = []        # signal of latch ordinal l
        self.latch_d: List[Optional[int]] = []
        self.sig_to_latch: Dict[int, int] = {}
        self.counts: Counter = Counter()      # section -> NAND count
        self.section_name = "misc"
        self.n_out = 0

    # ------------------------------------------------------------------ basics
    def inputs(self) -> Bus:
        return [2 + i for i in range(self.n_in)]

    @contextmanager
    def section(self, name: str):
        prev, self.section_name = self.section_name, name
        try:
            yield
        finally:
            self.section_name = prev

    def _emit_nand(self, x: int, y: int) -> int:
        assert 0 <= x < self.next and 0 <= y < self.next, "NAND references must point backwards"
        self.recs.append(("N", x, y))
        self.counts[self.section_name] += 1
        s = self.next
        self.next += 1
        return s

    def nand(self, x: int, y: int) -> int:
        if x == ZERO or y == ZERO:
            return ONE
        if x == ONE and y == ONE:
            return ZERO
        if x == ONE:
            return self.not_(y)
        if y == ONE:
            return self.not_(x)
        if self.inv.get(x) == y:              # NAND(x, NOT x) = 1
            return ONE
        key = (x, y) if x <= y else (y, x)
        s = self.memo.get(key)
        if s is None:
            s = self._emit_nand(*key)
            self.memo[key] = s
            if x == y:                        # s = NOT x: remember both directions
                self.inv[s] = x
                self.inv.setdefault(x, s)
        return s

    def not_(self, x: int) -> int:
        if x == ZERO:
            return ONE
        if x == ONE:
            return ZERO
        if x in self.inv:
            return self.inv[x]
        return self.nand(x, x)

    def and_(self, x: int, y: int) -> int:
        return self.not_(self.nand(x, y))

    def or_(self, x: int, y: int) -> int:
        return self.nand(self.not_(x), self.not_(y))

    def nor(self, x: int, y: int) -> int:
        return self.not_(self.or_(x, y))

    def xor(self, x: int, y: int) -> int:
        if x == ZERO:
            return y
        if y == ZERO:
            return x
        if x == ONE:
            return self.not_(y)
        if y == ONE:
            return self.not_(x)
        if x == y:
            return ZERO
        n1 = self.nand(x, y)
        return self.nand(self.nand(x, n1), self.nand(y, n1))

    def xnor(self, x: int, y: int) -> int:
        return self.not_(self.xor(x, y))

    def mux(self, s: int, a: int, b: int) -> int:
        """s ? b : a (3 NAND plus a shared NOT s)."""
        if s == ZERO or a == b:
            return a
        if s == ONE:
            return b
        if a == ZERO:
            return self.and_(s, b)
        if b == ZERO:
            return self.and_(self.not_(s), a)
        return self.nand(self.nand(a, self.not_(s)), self.nand(b, s))

    def and_many(self, xs: Sequence[int]) -> int:
        xs = list(xs)
        if not xs:
            return ONE
        while len(xs) > 1:
            nxt = [self.and_(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
            if len(xs) % 2:
                nxt.append(xs[-1])
            xs = nxt
        return xs[0]

    def or_many(self, xs: Sequence[int]) -> int:
        return self.not_(self.and_many([self.not_(x) for x in xs]))

    def or_of_ands(self, pairs: Sequence[Tuple[int, int]]) -> int:
        """OR over (s AND v): NAND of the NANDs (an AND-OR plane, 1 NAND per term plus the tree)."""
        return self.not_(self.and_many([self.nand(s, v) for s, v in pairs]))

    # ------------------------------------------------------------------ state and memory
    def latch(self) -> int:
        """A LATCH whose d input is set later with set_d(). Its value is the previous beat's d (0 at first)."""
        s = self.next
        self.recs.append(("L", None))
        self.next += 1
        self.sig_to_latch[s] = len(self.latch_sig)
        self.latch_sig.append(s)
        self.latch_d.append(None)
        return s

    def register(self, n: int) -> Bus:
        return [self.latch() for _ in range(n)]

    def set_d(self, latch_signal: int, d: int) -> None:
        l = self.sig_to_latch[latch_signal]
        if self.latch_d[l] is not None:
            raise ValueError("latch d already set")
        self.latch_d[l] = d

    def set_reg(self, reg: Bus, d: Bus) -> None:
        assert len(reg) == len(d)
        for q, x in zip(reg, d):
            self.set_d(q, x)

    def rom(self, addr: Bus, w: int, entries: Sequence[int]) -> Bus:
        """A mask-ROM record: 2^len(addr) entries of w bits, addr LSB first. Returns the w output signals."""
        for r in addr:
            assert 0 <= r < self.next, "ROM address references must point backwards"
        assert len(entries) == 1 << len(addr)
        self.recs.append(("R", list(addr), w, list(entries)))
        outs = list(range(self.next, self.next + w))
        self.next += w
        return outs

    # ------------------------------------------------------------------ buses
    @staticmethod
    def const(value: int, n: int) -> Bus:
        return [ONE if (value >> i) & 1 else ZERO for i in range(n)]

    def full_add(self, a: int, b: int, c: int) -> Tuple[int, int]:
        """Sum and carry: 9 NAND for three live inputs (fewer with constants)."""
        if c == ZERO:
            return self.xor(a, b), self.and_(a, b)
        if b == ZERO:
            return self.xor(a, c), self.and_(a, c)
        if a == ZERO:
            return self.xor(b, c), self.and_(b, c)
        n1 = self.nand(a, b)
        x1 = self.nand(self.nand(a, n1), self.nand(b, n1))  # a ^ b
        n2 = self.nand(x1, c)
        s = self.nand(self.nand(x1, n2), self.nand(c, n2))
        cout = self.nand(n1, n2)
        return s, cout

    def add(self, a: Bus, b: Bus, cin: int = ZERO) -> Tuple[Bus, int]:
        assert len(a) == len(b)
        out, c = [], cin
        for x, y in zip(a, b):
            s, c = self.full_add(x, y, c)
            out.append(s)
        return out, c

    def inc(self, a: Bus) -> Bus:
        return self.add(a, [ZERO] * len(a), ONE)[0]

    def eq(self, a: Bus, b: Bus) -> int:
        return self.and_many([self.xnor(x, y) for x, y in zip(a, b)])

    def is_zero(self, a: Bus) -> int:
        return self.and_many([self.not_(x) for x in a])

    def mux_bus(self, s: int, a: Bus, b: Bus) -> Bus:
        return [self.mux(s, x, y) for x, y in zip(a, b)]

    def and_bus(self, a: Bus, b: Bus) -> Bus:
        return [self.and_(x, y) for x, y in zip(a, b)]

    def or_bus(self, a: Bus, b: Bus) -> Bus:
        return [self.or_(x, y) for x, y in zip(a, b)]

    def xor_bus(self, a: Bus, b: Bus) -> Bus:
        return [self.xor(x, y) for x, y in zip(a, b)]

    def decoder(self, sel: Bus, enable: int = ONE) -> Bus:
        """One-hot decoder: line k is 1 iff sel == k (and enable). Built from 2-bit pre-decoders."""
        n = len(sel)
        if n == 0:
            return [enable]
        if n == 1:
            return [self.and_(enable, self.not_(sel[0])), self.and_(enable, sel[0])]
        lo = self.decoder(sel[: n // 2])
        hi = self.decoder(sel[n // 2:], enable)
        return [self.and_(h, l) for h in hi for l in lo]

    def mux_tree(self, sel: Bus, buses: Sequence[Bus]) -> Bus:
        """buses[sel] (sel LSB first, len(buses) == 2^len(sel)): a tree of 2:1 multiplexers."""
        assert len(buses) == 1 << len(sel)
        level = [list(b) for b in buses]
        for s in sel:
            level = [self.mux_bus(s, level[i], level[i + 1]) for i in range(0, len(level), 2)]
        return level[0]

    # ------------------------------------------------------------------ output
    def finish(self, outputs: Bus) -> bytes:
        """Appends the output buffers, encodes with tapnet2's encoder and checks the file."""
        for l, d in enumerate(self.latch_d):
            if d is None:
                raise ValueError(f"latch {l} has no d input")
        with self.section("output buffers"):
            invs = [self._emit_nand(x, x) for x in outputs]
            for n in invs:
                self._emit_nand(n, n)
        self.n_out = len(outputs)
        buf = bytearray()
        li = 0
        for r in self.recs:
            if r[0] == "N":
                buf += t2.enc_nand(r[1], r[2])
            elif r[0] == "L":
                buf += t2.enc_latch(self.latch_d[li])
                li += 1
            else:
                _, addr, w, entries = r
                buf += t2.enc_rom(addr, w, t2.pack_rom_data(entries, len(addr), w))
        data = t2.make_file(bytes(buf), self.n_in, self.n_out)
        t2.check_file(data)
        return data

    # ------------------------------------------------------------------ metrics
    def gate_counts(self) -> dict:
        nand = sum(1 for r in self.recs if r[0] == "N")
        roms = [r for r in self.recs if r[0] == "R"]
        return {
            "nand": nand,
            "latch": len(self.latch_sig),
            "romRecords": len(roms),
            "romBits": sum((1 << len(r[1])) * r[2] for r in roms),
            "signals": self.next,
            "nandBySection": {k: v for k, v in self.counts.items() if v},
        }
