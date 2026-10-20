"""Differential corpus: random valid tapnet/2 files, random beats and budgets, honest witnesses and random
mutations, each with the verdict of the Python reference (assets/tap-draft-large-netlists/tapnet2.py). Replayed by test/Differential.t.sol.

Two parts: nFiles files of NANDs, LATCHes and ROMs (the corpus of v0.1, unchanged), then nRam files with RAM read
and write ports in one to three banks and ROM read ports (some without RAM ports, so without MEM), run for a few
beats from the all-zero state and memory, with the mutations of the first part plus mutations of the memory
proofs, of the memRoot argument, of the tuples' M and of the memory encoding (spec 6.1, 6.3).

Usage: python3 gen_diff.py [nFiles [nRam]]     (deterministic; writes diff_cases.bin next to this file)
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "assets", "tap-draft-large-netlists"))
sys.path.insert(0, HERE)

import binfmt  # noqa: E402
import tapnet2 as t2  # noqa: E402
from examples import Builder  # noqa: E402


def random_file(rng: random.Random) -> bytes:
    n_in = rng.randint(1, 40)
    b = Builder(n_in)
    latches = [b.latch() for _ in range(rng.randint(0, 40))]
    big = rng.random() < 0.4                  # multi-chunk files: large ROM data regions
    for _ in range(rng.randint(2, 12)):
        for _ in range(rng.randint(0, 400)):
            r = rng.random()
            hi = b.next
            if r < 0.08:
                lp = b.latch()
                latches.append(lp)
            else:
                def pick():
                    q = rng.random()
                    if q < 0.05:
                        return rng.randint(0, 1)
                    if q < 0.7:
                        return hi - 1 - rng.randrange(min(48, hi - 2))
                    return 2 + rng.randrange(hi - 2)
                b.nand(pick(), pick())
        if rng.random() < 0.7:
            if big and rng.random() < 0.5:
                a, w = rng.randint(10, 13), rng.choice([8, 16, 24, 40])
            else:
                a, w = rng.randint(1, 7), rng.randint(1, 64)
            addr = [rng.randrange(b.next) for _ in range(a)]
            b.rom(addr, w, [rng.getrandbits(w) for _ in range(1 << a)])
    for _, pos in latches:
        b.set_d(pos, rng.randrange(b.next))
    n_out = rng.randint(1, min(300, b.next - 2 - n_in))
    return b.file(n_out)


def flip32(h: bytes) -> bytes:
    return bytes([h[0] ^ 1]) + h[1:]


def random_ram_file(rng: random.Random, with_ram: bool = True) -> bytes:
    """NAND blocks, LATCHes, ROMs with ROM read ports, and (with_ram) RAM read and write ports in 1 to 3 banks.
    Every reference points backwards; banks keep their (a, w)."""
    n_in = rng.randint(1, 24)
    b = Builder(n_in)
    latches = [b.latch() for _ in range(rng.randint(0, 24))]
    banks = {}
    if with_ram:
        for bank in rng.sample(range(256), rng.randint(1, 3)):
            banks[bank] = (rng.randint(1, 8), rng.choice([1, 3, 4, 8, 13, 16, 32, 64]))
    roms = []

    def ref():
        hi = b.next
        q = rng.random()
        if q < 0.05:
            return rng.randint(0, 1)
        if q < 0.6:
            return hi - 1 - rng.randrange(min(32, hi - 2))
        return 2 + rng.randrange(hi - 2)

    for _ in range(rng.randint(3, 14)):
        for _ in range(rng.randint(0, 120)):
            if rng.random() < 0.06:
                latches.append(b.latch())
            else:
                b.nand(ref(), ref())
        r = rng.random()
        if r < 0.2:
            a, w = rng.randint(1, 6), rng.randint(1, 40)
            b.rom([ref() for _ in range(a)], w, [rng.getrandbits(w) for _ in range(1 << a)])
            roms.append((b.last_rom, a, w))
        elif r < 0.35 and roms:
            off, a, w = rng.choice(roms)
            b.rom_port(off, [ref() for _ in range(a)], w)
        elif banks and r < 0.7:
            bank = rng.choice(sorted(banks))
            a, w = banks[bank]
            b.ram_read(bank, [ref() for _ in range(a)], w)
        elif banks:
            bank = rng.choice(sorted(banks))
            a, w = banks[bank]
            we = 1 if rng.random() < 0.2 else ref()
            b.ram_write(bank, [ref() for _ in range(a)], [ref() for _ in range(w)], we)
    for _, pos in latches:
        b.set_d(pos, rng.randrange(b.next))
    n_out = rng.randint(1, min(64, b.next - 2 - n_in))
    return b.file(n_out)


def mask_witness(w, enc: bytes, rng: random.Random):
    """Mutations of the memory part of an encoded witness that must fail (spec 6.3); (name, bytes) or None."""
    if not (w.mem_reads or w.mem_writes):
        return ("empty memory extension", enc + bytes(4)) if rng.random() < 0.5 else None
    base = len(t2.serialize_seg_witness(t2.SegWitness(**{**w.__dict__, "mem_reads": [], "mem_writes": []})))
    q = base + 2                                   # first access (of the reads, or of the writes if none)
    if not w.mem_reads:
        q += 2
    m = rng.randrange(4)
    if m == 0:                                     # mask bit 24
        return "zero-sibling mask bit 24 set", enc[:q + 32] + bytes([enc[q + 32] | 1]) + enc[q + 33:]
    if m == 1:                                     # a zero sibling spelled out
        word, sibs = (w.mem_reads or w.mem_writes)[0]
        zs = [h for h in range(t2.MEM_HEIGHT) if sibs[h] == t2.ZERO_H[h]]
        if not zs:
            return None
        h = zs[0]
        mask = sum(1 << l for l in range(t2.MEM_HEIGHT) if sibs[l] == t2.ZERO_H[l] and l != h)
        acc = word + t2.u32(mask) + b"".join(sibs[l] for l in range(t2.MEM_HEIGHT) if not (mask >> l) & 1)
        old = len(word) + 4 + 32 * (t2.MEM_HEIGHT - bin(mask | (1 << h)).count("1"))
        return "zero sibling not omitted", enc[:q] + acc + enc[q + old:]
    if m == 2:
        return "trailing byte after the memory part", enc + bytes([rng.randrange(256)])
    return "memory part truncated", enc[:-rng.randint(1, 31)]


def main(argv):
    n_files = int(argv[0]) if argv else 40
    n_ram = int(argv[1]) if len(argv) > 1 else 40
    rng = random.Random(0x7A9D1FF)
    files, cases = [], []
    stats = {"seg": [0, 0], "end": [0, 0]}

    def add_seg(fi, store, nh2, a, b, sr, wb, bud, name, mr=t2.ZERO_MEM):
        ok = t2.verify_segment(store, nh2, sr, a, b, wb, bud, mr)
        cases.append(binfmt.seg_case(fi, a, b, sr, bud, wb, ok, name, mem_root=mr))
        stats["seg"][ok] += 1

    def add_end(fi, store, nh2, n_out, seg_m, a, b, wb, name):
        ok = t2.verify_beat_end(store, nh2, n_out, seg_m, a, b, wb)
        cases.append(binfmt.end_case(fi, n_out, seg_m, a, b, wb, ok, name))
        stats["end"][ok] += 1

    for fi in range(n_files):
        data = random_file(rng)
        nl = t2.decode(data)
        files.append(data)
        store, nh2 = t2.oracle_for(data)
        beat0 = rng.random() < 0.25
        state = [0] * nl.n_latch if beat0 else [rng.getrandbits(1) for _ in range(nl.n_latch)]
        sr = t2.EMPTY if beat0 else t2.state_root(state)
        inputs = [rng.getrandbits(1) for _ in range(nl.n_in)]
        V, outs, ns = t2.evaluate(nl, state, inputs)
        if rng.random() < 0.5:
            bud = t2.Budget(rng.randint(1, 400), rng.randint(1, 24), rng.randint(1, 6))
        else:
            bud = t2.DEFAULT_BUDGET
        tuples = t2.segment_tuples(nl, V, bud)
        for _ in range(4):
            j = rng.randint(1, len(tuples) - 1)
            a, b = tuples[j - 1], tuples[j]
            w = t2.build_seg_witness(nl, V, state, a, b.byte_offset, sr)
            wb = t2.serialize_seg_witness(w)
            add_seg(fi, store, nh2, a, b, sr, wb, bud, "honest")
            for _ in range(6):
                m = rng.randrange(14)
                a2, b2, sr2, w2, bud2, name = a, b, sr, wb, bud, ""
                if m == 0:
                    pos = rng.randrange(len(wb))
                    w2 = wb[:pos] + bytes([wb[pos] ^ (1 << rng.randrange(8))]) + wb[pos + 1:]
                    name = f"witness bit flip at {pos}"
                elif m == 1:
                    w2 = wb[:-rng.randint(1, min(8, len(wb)))]
                    name = "witness truncated"
                elif m == 2:
                    w2 = wb + bytes([rng.randrange(256)])
                    name = "trailing byte"
                elif m == 3:
                    b2 = b._replace(byte_offset=b.byte_offset + rng.choice([-7, -4, -1, 1, 4, 7]))
                    name = "b.byteOffset changed"
                elif m == 4:
                    b2 = b._replace(first_signal=max(0, b.first_signal + rng.choice([-1, 1])))
                    name = "b.firstSignal changed"
                elif m == 5:
                    b2 = b._replace(p=flip32(b.p)) if rng.random() < 0.5 else b._replace(d=flip32(b.d))
                    name = "b root changed"
                elif m == 6:
                    a2 = a._replace(p=flip32(a.p))
                    name = "a.P changed"
                elif m == 7:
                    sr2 = t2.EMPTY if sr != t2.EMPTY else t2.state_root(state) if nl.n_latch else flip32(t2.EMPTY)
                    name = "stateRoot changed"
                elif m == 8:
                    k = rng.randrange(3)
                    d = rng.choice([-1, 1, 2, 50])
                    vals = list(bud)
                    vals[k] = max(0, vals[k] + d)
                    bud2 = t2.Budget(*vals)
                    name = f"budget changed {tuple(bud2)}"
                elif m == 9 and len(tuples) > 2:
                    j2 = rng.randint(1, len(tuples) - 1)
                    w2 = t2.serialize_seg_witness(t2.build_seg_witness(nl, V, state, tuples[j2 - 1],
                                                                       tuples[j2].byte_offset, sr))
                    name = f"witness of segment {j2}"
                elif m == 10 and w.s_size:
                    w3 = t2.SegWitness(**{**w.__dict__, "s_size": max(1, w.s_size + rng.choice([-1, 1, 2, 7]))})
                    w2 = t2.serialize_seg_witness(w3)
                    name = "state tree size changed"
                elif m == 11 and len(w.p_words) >= 1:
                    k = rng.choice(sorted(w.p_words))
                    pw = dict(w.p_words)
                    pw[k] = flip32(pw[k])
                    w2 = t2.serialize_seg_witness(t2.SegWitness(**{**w.__dict__, "p_words": pw}))
                    name = "P word changed"
                elif m == 12:
                    a2 = a._replace(latch_ordinal=a.latch_ordinal + 1)
                    name = "a.latchOrdinal changed"
                elif m == 13:
                    tup = tuples[min(j + 1, len(tuples) - 1)]
                    b2 = tup
                    name = "b is a later tuple"
                else:
                    continue
                add_seg(fi, store, nh2, a2, b2, sr2, w2, bud2, name)
        # beat-end steps
        seg_m = tuples[-1]
        for kind in ("out", "state"):
            vals = outs if kind == "out" else ns
            if not vals:
                continue
            lists = t2.out_tuples(outs) if kind == "out" else t2.state_tuples(ns)
            for _ in range(2):
                k = rng.randint(1, len(lists) - 1)
                a, b = lists[k - 1], lists[k]
                w = (t2.build_out_witness if kind == "out" else t2.build_state_witness)(nl, V, vals, k - 1)
                wb = t2.serialize_end_witness(w)
                add_end(fi, store, nh2, nl.n_out, seg_m, a, b, wb, f"honest {kind}")
                for _ in range(5):
                    m = rng.randrange(7)
                    n2, sm2, a2, b2, w2 = nl.n_out, seg_m, a, b, wb
                    if m == 0:
                        pos = rng.randrange(len(wb))
                        w2 = wb[:pos] + bytes([wb[pos] ^ (1 << rng.randrange(8))]) + wb[pos + 1:]
                    elif m == 1:
                        w2 = wb[:-1]
                    elif m == 2:
                        b2 = b._replace(root=flip32(b.root))
                    elif m == 3:
                        b2 = b._replace(ordinal=b.ordinal + rng.choice([-1, 1]))
                    elif m == 4:
                        n2 = max(0, nl.n_out + rng.choice([-1, 1]))
                    elif m == 5:
                        sm2 = seg_m._replace(p=flip32(seg_m.p))
                    else:
                        w2 = bytes([3 - wb[0]]) + wb[1:]
                    add_end(fi, store, nh2, n2, sm2, a2, b2, w2, f"{kind} mutation {m}")
    ram_part(files, cases, add_seg, add_end, n_ram)
    out = os.path.join(HERE, "diff_cases.bin")
    binfmt.write(out, files, cases)
    print(f"{len(files)} files, {len(cases)} cases; segment true/false {stats['seg'][1]}/{stats['seg'][0]}, "
          f"beat-end true/false {stats['end'][1]}/{stats['end'][0]}; wrote {out} ({os.path.getsize(out)} bytes)")


def ram_part(files, cases, add_seg, add_end, n_ram):
    """Files with RAM ports and ROM read ports (spec 1.1), several beats each."""
    rng = random.Random(0x4A3_0011)
    for k in range(n_ram):
        data = random_ram_file(rng, with_ram=k % 5 != 4)      # every fifth file: ROM read ports only, no MEM
        nl = t2.decode(data)
        fi = len(files)
        files.append(data)
        store, nh2 = t2.oracle_for(data)
        state, mem = [0] * nl.n_latch, {}
        trace = []
        for t in range(rng.randint(2, 6)):
            x = [rng.getrandbits(1) for _ in range(nl.n_in)]
            V, outs, ns, nm, ev = t2.evaluate_full(nl, state, mem, x)
            trace.append((t, state, mem, V, outs, ns, nm, ev))
            state, mem = ns, nm
        for _ in range(2):
            t, st, mem, V, outs, ns, nm, ev = rng.choice(trace)
            lr = t2.EMPTY if t == 0 else t2.state_root(st)
            mr = t2.ZERO_MEM if t == 0 or not nl.has_mem else t2.mem_root(nl, mem)
            if rng.random() < 0.5:
                bud = t2.Budget(rng.randint(1, 300), rng.randint(1, 24), rng.randint(1, 6))
            else:
                bud = t2.DEFAULT_BUDGET
            tuples = t2.segment_tuples(nl, V, bud, mem, ev)
            if len(tuples) < 2:
                continue
            for _ in range(3):
                j = rng.randint(1, len(tuples) - 1)
                a, b = tuples[j - 1], tuples[j]
                w = t2.build_seg_witness(nl, V, st, a, b.byte_offset, lr, mem, ev)
                wb = t2.serialize_seg_witness(w)
                add_seg(fi, store, nh2, a, b, lr, wb, bud,
                        f"honest ram (t={t}, {len(w.mem_reads)} reads, {len(w.mem_writes)} writes)", mr)
                for _ in range(6):
                    m = rng.randrange(14)
                    a2, b2, lr2, mr2, w2, name = a, b, lr, mr, wb, ""
                    W = w.__dict__
                    if m == 0:
                        pos = rng.randrange(len(wb))
                        w2 = wb[:pos] + bytes([wb[pos] ^ (1 << rng.randrange(8))]) + wb[pos + 1:]
                        name = f"witness bit flip at {pos}"
                    elif m == 1 and w.mem_reads:
                        i = rng.randrange(len(w.mem_reads))
                        word, sibs = w.mem_reads[i]
                        if rng.random() < 0.5:
                            word = flip32(word)
                        else:
                            sibs = list(sibs)
                            sibs[rng.randrange(24)] = t2.sha256(sibs[0])
                        w2 = t2.serialize_seg_witness(t2.SegWitness(**{**W, "mem_reads": w.mem_reads[:i] + [(word, sibs)]
                                                                       + w.mem_reads[i + 1:]}))
                        name = "read proof changed"
                    elif m == 2 and w.mem_writes:
                        i = rng.randrange(len(w.mem_writes))
                        word, sibs = w.mem_writes[i]
                        w2 = t2.serialize_seg_witness(t2.SegWitness(**{**W, "mem_writes": w.mem_writes[:i]
                                                                       + [(flip32(word), sibs)] + w.mem_writes[i + 1:]}))
                        name = "write proof old word changed"
                    elif m == 3 and (w.mem_reads or w.mem_writes):
                        key = "mem_reads" if w.mem_reads and (not w.mem_writes or rng.random() < 0.5) else "mem_writes"
                        lst = W[key]
                        lst2 = lst[:-1] if rng.random() < 0.5 else lst + [lst[0]]
                        w2 = t2.serialize_seg_witness(t2.SegWitness(**{**W, key: lst2}))
                        name = f"{key} missing or extra proof"
                    elif m == 4:
                        mr2 = t2.ZERO_MEM if mr != t2.ZERO_MEM else flip32(mr)
                        name = "memRoot changed"
                    elif m == 5:
                        b2 = b._replace(m=flip32(b.m))
                        name = "b.M changed"
                    elif m == 6:
                        a2 = a._replace(m=flip32(a.m))
                        name = "a.M changed"
                    elif m == 7 and nl.has_mem and t > 0:
                        lr2, mr2 = t2.combine_state(lr, mr), t2.ZERO_MEM
                        name = "combined stateRoot as latchRoot, memRoot ZERO_MEM"
                    elif m == 8:
                        mw = mask_witness(w, wb, rng)
                        if mw is None:
                            continue
                        name, w2 = mw
                    elif m == 9:
                        b2 = b._replace(p=flip32(b.p))
                        name = "b.P changed"
                    elif m == 10:
                        w2 = wb[:-rng.randint(1, min(8, len(wb)))]
                        name = "witness truncated"
                    elif m == 11:
                        b2 = b._replace(byte_offset=b.byte_offset + rng.choice([-5, -3, 3, 5, 8]))
                        name = "b.byteOffset changed"
                    elif m == 12 and len(tuples) > 2:
                        j2 = rng.randint(1, len(tuples) - 1)
                        w2 = t2.serialize_seg_witness(t2.build_seg_witness(nl, V, st, tuples[j2 - 1],
                                                                           tuples[j2].byte_offset, lr, mem, ev))
                        name = f"witness of segment {j2}"
                    elif m == 13:
                        k = rng.randrange(3)
                        vals = list(bud)
                        vals[k] = max(0, vals[k] + rng.choice([-1, 1, 3]))
                        bud2 = t2.Budget(*vals)
                        add_seg(fi, store, nh2, a, b, lr, wb, bud2, f"budget changed {tuple(bud2)}", mr)
                        continue
                    else:
                        continue
                    add_seg(fi, store, nh2, a2, b2, lr2, w2, bud, name, mr2)
            seg_m = tuples[-1]
            stl = t2.state_tuples(ns)
            if len(stl) > 1:
                wst = t2.serialize_end_witness(t2.build_state_witness(nl, V, ns, 0))
                add_end(fi, store, nh2, nl.n_out, seg_m, stl[0], stl[1], wst, "honest state (ram file)")
            otl = t2.out_tuples(outs)
            wo = t2.serialize_end_witness(t2.build_out_witness(nl, V, outs, 0))
            add_end(fi, store, nh2, nl.n_out, seg_m, otl[0], otl[1], wo, "honest out (ram file)")


if __name__ == "__main__":
    main(sys.argv[1:])
