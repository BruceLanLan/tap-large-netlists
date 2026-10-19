"""Synthetic netlists and verifier calls for the gas suite (test/GasVerify.t.sol), built with the Python
reference. Every case is checked with assets/tap-draft-large-netlists/tapnet2.py's verifiers (they must accept) before it is written.

Files (all valid tapnet/2 files):
  wideA, wideB  files of just under 2^24 signals (wideA: 16,777,024, i.e. 65,536 signal words, the deepest tree the
                format allows): nIn = 254, LATCHes, filler ROMs (a = 1, w = 256), then NAND tails whose references
                are spread over 256 (or 512) words of the whole signal tree. wideA: 320 LATCHes and 384 outputs,
                wideB: 384 LATCHes and 320 outputs (so that beat-end steps of 256, 128 and 64 values exist).
  nandLocal     16 inputs, 40,000 NANDs with local wiring.
  latch         16 inputs, 32,768 LATCHes (random d), 512 NANDs.
  romSmall      400 ROMs a = 8, w = 8 (the S-box shape).
  romWide       30 ROMs a = 8, w = 256.
  romChunk      40 ROMs a = 10, w = 128 (16 KiB of data each: one ROM per chunk).

Usage: python3 gen_gas.py [--quick]     writes gas_cases.bin next to this file (deterministic)
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "assets", "tap-draft-large-netlists"))
sys.path.insert(0, HERE)

import binfmt  # noqa: E402
import tapnet2 as t2  # noqa: E402
from examples import Builder  # noqa: E402

T0 = time.time()
G_LIST = [500, 1000, 2000, 4000, 5000, 6000, 7000, 10000, 14000, 20000, 28000]
BIG = 1 << 30


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def fast_words(V, n):
    """Bit words (spec 4.2) of V[0:n]."""
    out = []
    for k in range(0, n, 256):
        chunk = V[k:min(k + 256, n)]
        out.append(int("".join("1" if b else "0" for b in reversed(chunk)) or "0", 2).to_bytes(32, "little"))
    return out


class Beat:
    """One beat of one file, with cached signal words and leaves."""

    def __init__(self, data, state, inputs, beat0=False):
        self.data = data
        self.nl = t2.decode(data)
        self.state = state
        self.sroot = t2.EMPTY if beat0 else t2.state_root(state)
        self.V, self.outs, self.ns = t2.evaluate(self.nl, state, inputs)
        S = self.nl.n_signals
        self.words = fast_words(self.V, S)
        self.leaves = [t2.word_leaf(t2.DOM_SIGNAL, k, w) for k, w in enumerate(self.words)]
        self.store, self.nh2 = t2.oracle_for(data)
        self._levels = {}

    def word_at(self, k, n):
        """Word k of the prefix of length n (bits >= n cleared)."""
        w = self.words[k]
        used = n - 256 * k
        if used >= 256:
            return w
        x = int.from_bytes(w, "little") & ((1 << used) - 1)
        return x.to_bytes(32, "little")

    def acc(self, n):
        acc = t2.Acc(t2.DOM_SIGNAL, t2.KIND_BITS)
        full = n // 256
        for k in range(full):
            acc._push(self.leaves[k])
        acc.full = full
        for i in range(full * 256, n):
            acc.append(self.V[i])
        return acc

    def p_root(self, n):
        return self.acc(n).root()

    def levels(self, n):
        if n not in self._levels:
            m = (n + 255) // 256
            lv = self.leaves[:m]
            if n % 256:
                lv[-1] = t2.word_leaf(t2.DOM_SIGNAL, m - 1, self.word_at(m - 1, n))
            self._levels = {n: t2.merkle_levels(lv)}
        return self._levels[n]

    def d_acc(self, l):
        return t2.Acc.from_values(t2.DOM_LATCHD, t2.KIND_U32, self.nl.latch_d, l)

    def tuple_at(self, rec_index):
        r = self.nl.records[rec_index]
        return t2.SegTuple(r.raw.off, r.first_out, r.latch_ord, self.p_root(r.first_out), self.d_acc(r.latch_ord).root())

    def seg(self, rec_index, budget, name):
        """Segment case from the boundary before record rec_index (mirrors t2.build_seg_witness)."""
        nl, data = self.nl, self.data
        a = self.tuple_at(rec_index)
        read, flen = t2._file_reader(data), len(data)
        seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, budget)
        p_idx, s_idx = set(), set()
        for raw, _, l in seg.records:
            if raw.op == t2.OP_LATCH:
                s_idx.add(l >> 8)
            else:
                p_idx |= {r >> 8 for r in raw.refs if 2 <= r < a.first_signal}
        fs = a.first_signal
        accp = self.acc(fs)
        p_peaks, p_partial = accp.frontier()
        if p_partial is not None:
            p_idx.discard(fs >> 8)
        p_words, p_proof = {}, []
        if p_idx:
            lv = self.levels(fs)
            p_words = {k: self.word_at(k, fs) for k in sorted(p_idx)}
            p_proof = t2.multiproof(lv, sorted(p_idx))
        d_peaks, d_partial = self.d_acc(a.latch_ordinal).frontier()
        s_words, s_proof, s_size = {}, [], 0
        if nl.n_latch and self.sroot != t2.EMPTY:
            sw = t2.prefix_words(self.state, nl.n_latch, t2.KIND_BITS)
            s_size = len(sw)
            if s_idx:
                lv = t2.merkle_levels([t2.word_leaf(t2.DOM_STATE, k, w) for k, w in enumerate(sw)])
                s_words = {k: sw[k] for k in sorted(s_idx)}
                s_proof = t2.multiproof(lv, sorted(s_idx))
        w = t2.SegWitness(p_peaks, p_partial, p_words, p_proof, d_peaks, d_partial, s_size, s_words, s_proof)
        wb = t2.serialize_seg_witness(w)
        b = t2.SegTuple(seg.end_off, seg.end_signal, seg.end_latch, self.p_root(seg.end_signal),
                        self.d_acc(seg.end_latch).root())
        assert t2.verify_segment(self.store, self.nh2, self.sroot, a, b, wb, budget), name
        reads = t2.LAST_READ["chunks"]
        c = seg.cost
        label = (f"{name} | gates={c.gates} ext={len(c.ext)} chunkCost={c.chunks} chunksRead={reads} "
                 f"records={len(seg.records)} pWords={len(p_words)} pProof={len(p_proof)} sWords={len(s_words)} "
                 f"sProof={len(s_proof)} witness={len(wb)}")
        log("  " + label)
        return a, b, wb, reads, label

    def end(self, kind, k, name):
        nl = self.nl
        S, n_out = nl.n_signals, nl.n_out
        lo = k * 256
        if kind == "out":
            hi = min(lo + 256, n_out)
            idx = sorted({(S - n_out + i) >> 8 for i in range(lo, hi)})
            dw, dp = {}, []
            vals = self.outs
            dom = t2.DOM_SIGNAL
        else:
            L = nl.n_latch
            hi = min(lo + 256, L)
            idx = sorted({nl.latch_d[l] >> 8 for l in range(lo, hi) if nl.latch_d[l] >= 2})
            dwords = t2.prefix_words(nl.latch_d, L, t2.KIND_U32)
            dlv = t2.merkle_levels([t2.word_leaf(t2.DOM_LATCHD, j, x) for j, x in enumerate(dwords)])
            didx = sorted({l >> 3 for l in range(lo, hi)})
            dw, dp = {j: dwords[j] for j in didx}, t2.multiproof(dlv, didx)
            vals = self.ns
            dom = t2.DOM_STATE
        lv = self.levels(S)
        pw = {j: self.word_at(j, S) for j in idx}
        pp = t2.multiproof(lv, idx) if idx else []
        acc = t2.Acc.from_values(dom, t2.KIND_BITS, vals, lo)
        w = t2.EndWitness(kind, pw, pp, dw, dp, acc.frontier()[0])
        wb = t2.serialize_end_witness(w)
        a = t2.EndTuple(lo, acc.root())
        for i in range(lo, hi):
            acc.append(vals[i])
        b = t2.EndTuple(hi, acc.root())
        seg_m = t2.SegTuple(len(self.data), S, nl.n_latch, self.p_root(S), self.d_acc(nl.n_latch).root())
        assert t2.verify_beat_end(self.store, self.nh2, n_out, seg_m, a, b, wb), name
        label = (f"{name} | values={hi - lo} pWords={len(pw)} pProof={len(pp)} dWords={len(dw)} dProof={len(dp)} "
                 f"peaks={len(w.r_peaks)} witness={len(wb)}")
        log("  " + label)
        return n_out, seg_m, a, b, wb, label


def rec_index_at_signal(nl, sig):
    for i, r in enumerate(nl.records):
        if r.first_out == sig:
            return i
    raise ValueError(sig)


def wide_file(n_latch, n_out, scatter, sweep, seed):
    rng = random.Random(seed)
    n_in = 254
    b = Builder(n_in)
    latches = [b.latch() for _ in range(n_latch)]
    blobs = [t2.enc_rom([2], 256, bytes(rng.getrandbits(8) for _ in range(64))) for _ in range(8)]
    tail = scatter + sweep
    r = (t2.MAX_SIGNALS - b.next - tail) // 256
    f0 = b.next
    body = bytearray()
    for i in range(r):
        body += blobs[i % 8]
    b.buf += body
    b.next += 256 * r
    wlo, whi = (f0 + 255) // 256, (f0 + 256 * r) // 256        # whole filler words [wlo, whi)
    tail_start = b.next
    W = [wlo + j * (whi - wlo) // 256 for j in range(256)]
    for i in range(scatter):
        b.nand(W[i % 256] * 256 + rng.randrange(256), W[(7 * i + 3) % 256] * 256 + rng.randrange(256))
    sweep_start = b.next
    W2 = [wlo + j * (whi - wlo) // 512 for j in range(512)]
    for i in range(sweep):
        b.nand(W2[i % 512] * 256 + 5, W2[i % 512] * 256 + 9)
    W3 = [wlo + j * (whi - wlo) // n_latch for j in range(n_latch)]
    for l, (_, pos) in enumerate(latches):
        b.set_d(pos, W3[l] * 256 + 17)
    data = t2.make_file(bytes(b.buf), n_in, n_out)
    return data, tail_start, sweep_start


def main(argv):
    quick = "--quick" in argv
    files, cases = [], []

    def add_file(data):
        files.append(data)
        return len(files) - 1

    def seg_cases(beat, fi, rec_index, budgets, name):
        for bud in budgets:
            a, b, wb, reads, label = beat.seg(rec_index, bud, f"{name} budget=({bud.gates},{bud.ext_words},{bud.chunks})")
            cases.append(binfmt.seg_case(fi, a, b, beat.sroot, bud, wb, True, label, info=reads))

    def end_case(beat, fi, kind, k, name):
        n_out, seg_m, a, b, wb, label = beat.end(kind, k, name)
        cases.append(binfmt.end_case(fi, n_out, seg_m, a, b, wb, True, label))

    rng = random.Random(0x6A5)

    # ---- nandLocal: pure evaluation cost
    bl = Builder(16)
    for _ in range(40000):
        hi = bl.next
        bl.nand(hi - 1 - rng.randrange(min(64, hi - 2)), hi - 1 - rng.randrange(min(64, hi - 2)))
    data = bl.file(16)
    beat = Beat(data, [], [rng.getrandbits(1) for _ in range(16)], beat0=True)
    fi = add_file(data)
    log(f"nandLocal: {len(data)} bytes")
    seg_cases(beat, fi, 0, [t2.Budget(1, 256, 8)], "nandLocal single record")
    seg_cases(beat, fi, 0, [t2.Budget(g, 256, 64) for g in G_LIST], "nandLocal")
    seg_cases(beat, fi, 0, [t2.DEFAULT_BUDGET], "nandLocal default budget")

    # ---- latch: LATCH-only segments (state words, D accumulator)
    bl = Builder(16)
    lat = [bl.latch() for _ in range(32768)]
    for _ in range(512):
        hi = bl.next
        bl.nand(hi - 1, hi - 2)
    for _, pos in lat:
        bl.set_d(pos, rng.randrange(bl.next))
    data = bl.file(16)
    st = [rng.getrandbits(1) for _ in range(32768)]
    beat = Beat(data, st, [rng.getrandbits(1) for _ in range(16)])
    fi = add_file(data)
    log(f"latch: {len(data)} bytes")
    seg_cases(beat, fi, 0, [t2.Budget(g, 256, 64) for g in G_LIST], "latch")
    seg_cases(beat, fi, 0, [t2.DEFAULT_BUDGET], "latch default budget")
    beat0 = Beat(data, [0] * 32768, [0] * 16, beat0=True)
    seg_cases(beat0, fi, 0, [t2.Budget(7000, 256, 64)], "latch beat 0 (stateRoot EMPTY)")

    # ---- ROM-heavy
    bl = Builder(16)
    for _ in range(400):
        bl.rom([2 + rng.randrange(16) for _ in range(8)], 8, [rng.getrandbits(8) for _ in range(256)])
    data = bl.file(8)
    beat = Beat(data, [], [rng.getrandbits(1) for _ in range(16)], beat0=True)
    fi = add_file(data)
    log(f"romSmall: {len(data)} bytes")
    seg_cases(beat, fi, 0, [t2.Budget(7000, 256, 1024), t2.DEFAULT_BUDGET], "romSmall a=8 w=8")

    bl = Builder(16)
    for _ in range(30):
        bl.rom([2 + rng.randrange(16) for _ in range(8)], 256, [rng.getrandbits(256) for _ in range(256)])
    data = bl.file(8)
    beat = Beat(data, [], [rng.getrandbits(1) for _ in range(16)], beat0=True)
    fi = add_file(data)
    log(f"romWide: {len(data)} bytes")
    seg_cases(beat, fi, 0, [t2.Budget(7000, 256, 1024), t2.Budget(28000, 256, 1024), t2.DEFAULT_BUDGET],
              "romWide a=8 w=256")

    bl = Builder(16)
    for _ in range(40):
        bl.rom([2 + rng.randrange(16) for _ in range(10)], 128, [rng.getrandbits(128) for _ in range(1024)])
    data = bl.file(8)
    beat = Beat(data, [], [rng.getrandbits(1) for _ in range(16)], beat0=True)
    fi = add_file(data)
    log(f"romChunk: {len(data)} bytes")
    seg_cases(beat, fi, 0, [t2.Budget(BIG, 256, ch) for ch in (1, 4, 7, 10, 13, 16, 22, 31, 46, 61, 91, 121)],
              "romChunk a=10 w=128")

    if not quick:
        # ---- wideA / wideB: deep signal tree, scattered external reads
        data, tail_start, sweep_start = wide_file(320, 384, 32768, 512, 0xA)
        log(f"wideA: {len(data)} bytes, building beat")
        nl = t2.decode(data)
        beat = Beat(data, [rng.getrandbits(1) for _ in range(320)], [rng.getrandbits(1) for _ in range(254)])
        fi = add_file(data)
        log(f"wideA: S={beat.nl.n_signals}, words={len(beat.words)}")
        ri = rec_index_at_signal(beat.nl, tail_start)
        seg_cases(beat, fi, ri, [t2.Budget(g, 256, 64) for g in G_LIST], "wide scatter (2 refs/NAND over 256 words)")
        seg_cases(beat, fi, ri, [t2.DEFAULT_BUDGET], "wide scatter default budget")
        ri2 = rec_index_at_signal(beat.nl, sweep_start)
        seg_cases(beat, fi, ri2, [t2.Budget(k, 256, 64) for k in (16, 64, 128, 256)], "wide ext sweep (1 word/NAND)")
        end_case(beat, fi, "out", 0, "wideA beat-end outputs 0..255")
        end_case(beat, fi, "out", 1, "wideA beat-end outputs 256..383")
        end_case(beat, fi, "state", 0, "wideA beat-end state 0..255 (256 scattered P words)")
        end_case(beat, fi, "state", 1, "wideA beat-end state 256..319")
        del beat
        data, tail_start, sweep_start = wide_file(384, 320, 0, 512, 0xB)
        log(f"wideB: {len(data)} bytes, building beat")
        beat = Beat(data, [rng.getrandbits(1) for _ in range(384)], [rng.getrandbits(1) for _ in range(254)])
        fi = add_file(data)
        end_case(beat, fi, "out", 1, "wideB beat-end outputs 256..319")
        end_case(beat, fi, "state", 1, "wideB beat-end state 256..383")

    out = os.path.join(HERE, "gas_cases.bin")
    binfmt.write(out, files, cases)
    log(f"{len(files)} files, {len(cases)} cases; wrote {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main(sys.argv[1:])
