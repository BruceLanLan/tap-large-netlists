"""unittest suite for the RAM read port 0x11, the RAM write port 0x12, the ROM read port 0x13 and the memory tree
(spec 1, 4.6, 5.2, 6.1, 6.3, 7.4). Run: python3 -m unittest -v test_ram

test_tapnet2.py is the regression for files without RAM ports (and checks every vector, RAM vectors included)."""
from __future__ import annotations

import itertools
import unittest

import bisect_sim as bs
import examples as ex
import tapnet2 as t2

H = lambda n_in, n_out, flags=0: t2.MAGIC + bytes([2, flags]) + t2.u32(n_in) + t2.u32(n_out)  # noqa: E731


def run(nl, inputs):
    """Runs a file beat by beat from the all-zero state and memory; per beat
    (outputs, new state, new memory, V, events, state at the start, memory at the start)."""
    state, mem, out = [0] * nl.n_latch, {}, []
    for x in inputs:
        V, outs, ns, nm, ev = t2.evaluate_full(nl, state, mem, x)
        out.append((outs, ns, nm, V, ev, state, mem))
        state, mem = ns, nm
    return out


def roots(nl, state, mem, t):
    """(latchRoot_t, memRoot_t) as the game passes them to verify_segment (spec 7.4)."""
    if t == 0:
        return t2.EMPTY, t2.ZERO_MEM
    return t2.state_root(state), t2.mem_root(nl, mem)


class TestFormat(unittest.TestCase):
    def test_metrics_and_flag(self):
        nl = t2.decode(ex.ram16_file())
        m = nl.metrics()
        self.assertTrue(nl.has_mem and nl.data[7] == t2.FLAG_MEM)
        self.assertEqual((m["ramBanks"], m["ramBits"], m["ramReadPorts"], m["ramWritePorts"], m["romPorts"]),
                         (1, 64, 2, 1, 0))
        self.assertEqual(m["latch"], 4)
        self.assertFalse(t2.decode(ex.ram16_file("latch")).has_mem)
        m = t2.check_file(ex.rammix_file())
        self.assertEqual((m["ramBanks"], m["ramBits"], m["ramReadPorts"], m["ramWritePorts"], m["romPorts"]),
                         (2, 64 * 12 + 4, 2, 3, 1))
        m = t2.check_file(ex.romports_file())
        self.assertEqual((m["romRecords"], m["romPorts"], m["ramBanks"]), (1, 2, 0))
        self.assertNotIn("ramBanks", t2.check_file(ex.sbox_file()))   # no port metrics without ports

    def test_minimal_files(self):
        rr = t2.enc_ramr(0, [2], 1)
        rw = t2.enc_ramw(0, [2], [2], 2)
        t2.check_file(H(1, 1, 1) + rw + rr)               # a write port creates no signal
        t2.check_file(H(1, 1, 1) + rw + t2.enc_nand(2, 2))  # write-only memory: valid (and useless)
        with self.assertRaises(t2.Invalid):
            t2.check_file(H(1, 1, 1) + rw)                 # no signal for the output

    def test_no_expansion_with_ram(self):
        with self.assertRaises(t2.Invalid):
            t2.expand_rom_to_tapnet1(ex.ram16_file())
        exp = t2.expand_rom_to_tapnet1(ex.romports_file())  # ROM ports expand like ROMs
        self.assertEqual(t2.decode(exp, (1,)).n_out, 15)


class TestSemantics(unittest.TestCase):
    def test_latch_ram_equivalence(self):
        """The LATCH-built memory and the RAM ports give identical outputs, ACC and memory contents on every
        beat (the state roots differ by design)."""
        nl_l, nl_r = t2.decode(ex.ram16_file("latch")), t2.decode(ex.ram16_file("ram"))
        self.assertLess(len(nl_r.data), len(nl_l.data) / 5)
        for seed in (1, 2, 3):
            ins = ex.ram16_inputs(300, seed)
            for t, (a, b) in enumerate(zip(run(nl_l, ins), run(nl_r, ins))):
                self.assertEqual(a[0], b[0], f"outputs differ at beat {t}")
                self.assertEqual(ex.ram16_memory("latch", a[1], a[2]), ex.ram16_memory("ram", b[1], b[2]))
                self.assertEqual(a[1][:4], b[1])

    def test_rammix_model(self):
        """rammix against a direct model of its documented behaviour (two banks, two writes to one bank in a
        beat, the later winning, a ROM port addressed by memory contents)."""
        nl = t2.decode(ex.rammix_file())
        rom = ex.rammix_rom()
        bank0, bank7, acc = [0] * 64, [0] * 4, 0
        val = lambda bits_: sum(b << i for i, b in enumerate(bits_))  # noqa: E731
        hits = 0
        for t, (outs, ns, nm, *_rest) in enumerate(run(nl, ex.rammix_inputs(400, 77))):
            x_in = ex.rammix_inputs(400, 77)[t]
            A, D, we, sel = val(x_in[0:6]), val(x_in[6:10]), x_in[10], x_in[11]
            q, x = bank0[A], bank7[D & 3]
            p = rom[q & 63]
            want = [(q >> j) & 1 for j in range(12)] + [(p >> j) & 1 for j in range(12)] + [x] \
                + [(acc >> j) & 1 for j in range(4)]
            self.assertEqual(outs, want, f"beat {t}")
            if we:
                bank0[A] = q ^ rom[A]
            if sel:
                hits += we and A == D
                bank0[D] = p
            if we:
                bank7[D & 3] = 1 - x
            acc ^= q & 15
            self.assertEqual(nm.get(0, [0] * 64), bank0, f"bank 0 after beat {t}")
            self.assertEqual(nm.get(7, [0] * 4), bank7, f"bank 7 after beat {t}")
            self.assertEqual(val(ns), acc)
        self.assertGreater(hits, 0)          # the later write port won on the same entry at least once

    def test_write_order_and_read_before_write(self):
        """Two write ports to one address in one beat: the later in file order wins; a read port sees the memory
        at the start of the beat, wherever it stands in the file."""
        recs = (t2.enc_ramw(0, [0], [2], 1) + t2.enc_ramw(0, [0], [3], 1) + t2.enc_ramr(0, [0], 1)
                + t2.enc_nand(4, 4))
        nl = t2.decode(H(2, 2, 1) + recs)
        r = run(nl, [[1, 0], [0, 1], [1, 1], [0, 0]])
        self.assertEqual([x[0][0] for x in r], [0, 0, 1, 1])     # read port: previous beat's second write
        self.assertEqual([x[2][0][0] for x in r], [0, 1, 1, 0])  # memory after the beat

    def test_rom_port(self):
        a, b = t2.decode(ex.romports_file()), t2.decode(ex.romdup_file())
        self.assertLess(len(a.data), len(b.data))
        for x in itertools.product((0, 1), repeat=6):
            self.assertEqual(t2.step(a, [], list(x)), t2.step(b, [], list(x)))

    def test_memory_tree(self):
        """Entry positions (bank << 16 | word, stride = w rounded up to a power of two) and ZERO_MEM."""
        self.assertEqual(t2.mem_pos(7, 2, 1, 3), ((7 << 16), 3))
        self.assertEqual(t2.mem_pos(0, 6, 12, 17), (1, 16))          # stride 16: entry 17 at bit 272
        self.assertEqual(t2.mem_pos(255, 20, 16, (1 << 20) - 1), ((255 << 16) | 0xFFFF, 240))
        z = t2.mem_leaf(bytes(32))
        for _ in range(24):
            z = t2.node_hash(z, z)
        self.assertEqual(z, t2.ZERO_MEM)
        self.assertEqual(t2.mem_root(t2.decode(ex.ram16_file()), {}), t2.ZERO_MEM)
        tree = t2.MemTree()
        tree.set_entry(0, 6, 12, 17, 0xABC)
        self.assertEqual(t2.mem_path_root(tree.word(1), 1, tree.proof(1)), tree.root())


def _honest_steps(nl, trace, budget, beats):
    for t in beats:
        outs, ns, nm, V, ev, st, mem = trace[t]
        lr, mr = roots(nl, st, mem, t)
        tuples = t2.segment_tuples(nl, V, budget, mem, ev)
        for j in range(1, len(tuples)):
            w = t2.build_seg_witness(nl, V, st, tuples[j - 1], tuples[j].byte_offset, lr, mem, ev)
            yield t, lr, mr, tuples, j, w


class TestVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for name, fn, inputs in (("ram16", ex.ram16_file, ex.ram16_inputs(40, 11)),
                                 ("rammix", ex.rammix_file, ex.rammix_inputs(40, 12))):
            data = fn()
            nl = t2.decode(data)
            store, nh2 = t2.oracle_for(data)
            cls.cases[name] = (nl, store, nh2, run(nl, inputs))

    def test_honest_steps_and_encoding(self):
        n = 0
        for name, (nl, store, nh2, trace) in self.cases.items():
            for budget in (t2.TINY_BUDGET, t2.DEFAULT_BUDGET, t2.Budget(64, 12, 4)):
                for t, lr, mr, tuples, j, w in _honest_steps(nl, trace, budget, range(0, 40, 3)):
                    enc = t2.serialize_seg_witness(w)
                    self.assertEqual(t2.serialize_seg_witness(t2.parse_seg_witness(enc)), enc)
                    self.assertTrue(t2.verify_segment(store, nh2, lr, tuples[j - 1], tuples[j], enc, budget, mr),
                                    (name, budget, t, j))
                    n += 1
        self.assertGreater(n, 200)

    def test_end_of_beat_binding(self):
        """seg_m.M is the memory root after the beat, stateRoot_{t+1} = SHA-256(0x0C || state list root ||
        seg_m.M), and the beat-end state steps (LATCHes only) are unchanged."""
        for name, (nl, store, nh2, trace) in self.cases.items():
            for t in (0, 5, 20):
                outs, ns, nm, V, ev, st, mem = trace[t]
                seg = t2.segment_tuples(nl, V, t2.DEFAULT_BUDGET, mem, ev)
                self.assertEqual(seg[0].m, t2.mem_root(nl, mem))
                self.assertEqual(seg[-1].m, t2.mem_root(nl, nm))
                stl = t2.state_tuples(ns)
                self.assertEqual(t2.combine_state(stl[-1].root, seg[-1].m), t2.full_state_root(nl, ns, nm))
                w = t2.build_state_witness(nl, V, ns, 0)
                self.assertTrue(t2.verify_beat_end(store, nh2, nl.n_out, seg[-1], stl[0], stl[1], w))

    def test_mutations_fail(self):
        seen = {"read": 0, "write": 0, "zeroRead": 0}
        flip = lambda h: bytes([h[0] ^ 1]) + h[1:]  # noqa: E731
        for name, (nl, store, nh2, trace) in self.cases.items():
            budget = t2.TINY_BUDGET
            for t, lr, mr, tuples, j, w in _honest_steps(nl, trace, budget, range(1, 40, 2)):
                a, b = tuples[j - 1], tuples[j]

                def ok(ww, bb=b, l_=lr, m_=mr):
                    return t2.verify_segment(store, nh2, l_, a, bb, ww, budget, m_)

                self.assertTrue(ok(w))
                muts = []
                for i, (word, sibs) in enumerate(w.mem_reads):
                    seen["read"] += 1
                    muts.append(("read word", dict(mem_reads=w.mem_reads[:i] + [(flip(word), sibs)]
                                                   + w.mem_reads[i + 1:])))
                    s2 = list(sibs)
                    s2[5] = t2.sha256(s2[5])
                    muts.append(("read sibling", dict(mem_reads=w.mem_reads[:i] + [(word, s2)] + w.mem_reads[i + 1:])))
                if w.mem_reads:
                    muts.append(("extra read proof", dict(mem_reads=w.mem_reads + [w.mem_reads[0]])))
                    muts.append(("missing read proof", dict(mem_reads=w.mem_reads[:-1])))
                for i, (word, sibs) in enumerate(w.mem_writes):
                    seen["write"] += 1
                    muts.append(("write old word", dict(mem_writes=w.mem_writes[:i] + [(flip(word), sibs)]
                                                        + w.mem_writes[i + 1:])))
                    muts.append(("dropped write proof", dict(mem_writes=w.mem_writes[:i] + w.mem_writes[i + 1:])))
                for nm_, ch in muts:
                    self.assertFalse(ok(t2.SegWitness(**{**w.__dict__, **ch})), (name, nm_, t, j))
                if w.mem_writes:
                    self.assertFalse(ok(w, b._replace(m=flip(b.m))), ("claimed M", name, t, j))
                    if b.m != a.m:
                        self.assertFalse(ok(w, b._replace(m=a.m)), ("claimed M unchanged", name, t, j))
                if w.mem_reads:
                    self.assertFalse(ok(w, m_=flip(mr)), ("wrong memRoot argument", name, t, j))
                    # the trap the two-root design removes: a combined root passed as the latch root, with
                    # memRoot = ZERO_MEM, would read the memory as zero; here it fails on the read proofs left over
                    self.assertFalse(ok(w, l_=t2.combine_state(lr, mr), m_=t2.ZERO_MEM), (name, t, j))
                    seen["zeroRead"] += 1
                if w.s_words:
                    self.assertFalse(ok(w, l_=t2.combine_state(lr, mr)), ("combined root as latchRoot", name, t, j))
        self.assertTrue(all(seen.values()), seen)

    def test_zero_memory_reads(self):
        """memRoot = ZERO_MEM (beat 0, or memory never written): RAM reads give 0 and consume no proof; a read
        proof in the witness is then left over and makes the call fail. Writes still prove against a.M."""
        nl, store, nh2, _ = self.cases["ram16"]
        x = [0] * ex.RAM16_N_IN
        x[12] = 1                                            # WE = 1 in beat 0
        outs, ns, nm, V, ev, st, mem = run(nl, [x])[0]
        tuples = t2.segment_tuples(nl, V, t2.TINY_BUDGET, mem, ev)
        writes = 0
        for j in range(1, len(tuples)):
            w = t2.build_seg_witness(nl, V, st, tuples[j - 1], tuples[j].byte_offset, t2.EMPTY, mem, ev)
            self.assertFalse(w.mem_reads)
            writes += len(w.mem_writes)
            self.assertTrue(t2.verify_segment(store, nh2, t2.EMPTY, tuples[j - 1], tuples[j], w, t2.TINY_BUDGET))
            pos, _ = t2.mem_pos(ex.RAM16_BANK, 4, 4, 0)
            w2 = t2.SegWitness(**{**w.__dict__, "mem_reads": [(bytes(32), t2.MemTree().proof(pos))]})
            self.assertFalse(t2.verify_segment(store, nh2, t2.EMPTY, tuples[j - 1], tuples[j], w2,
                                               t2.TINY_BUDGET))
        self.assertGreater(writes, 0)

    def test_witness_encoding_rules(self):
        """Zero siblings must be omitted, mask bits 24-31 are zero, an empty extension is malformed, and a
        witness without RAM accesses has no extension (the encoding of files without RAM ports)."""
        nl, store, nh2, trace = self.cases["rammix"]
        found = None
        for t, lr, mr, tuples, j, w in _honest_steps(nl, trace, t2.DEFAULT_BUDGET, range(3, 40)):
            if w.mem_reads:
                found = (t, lr, mr, tuples, j, w)
                break
        t, lr, mr, tuples, j, w = found
        enc = t2.serialize_seg_witness(w)
        base = t2.serialize_seg_witness(t2.SegWitness(**{**w.__dict__, "mem_reads": [], "mem_writes": []}))
        self.assertTrue(enc.startswith(base))
        ext = enc[len(base):]
        nr = int.from_bytes(ext[0:2], "big")
        self.assertEqual(nr, len(w.mem_reads))
        mask = int.from_bytes(ext[2 + 32:2 + 36], "big")
        self.assertTrue(mask & (1 << 23))                    # sparse memory: the top sibling (other banks) is zero
        # spell out a zero sibling with its mask bit cleared: non-canonical
        h = (mask & -mask).bit_length() - 1                  # lowest omitted sibling
        sibs_before = bin(~mask & ((1 << h) - 1)).count("1")
        cut = 2 + 36 + 32 * sibs_before
        bad = ext[:2 + 32] + (mask & ~(1 << h)).to_bytes(4, "big") + ext[2 + 36:cut] + t2.ZERO_H[h] + ext[cut:]
        with self.assertRaises(t2.Invalid):
            t2.parse_seg_witness(base + bad)
        self.assertFalse(t2.verify_segment(store, nh2, lr, tuples[j - 1], tuples[j], base + bad,
                                           t2.DEFAULT_BUDGET, mr))
        bad = ext[:2 + 32] + (mask | 1 << 24).to_bytes(4, "big") + ext[2 + 36:]
        with self.assertRaises(t2.Invalid):
            t2.parse_seg_witness(base + bad)
        with self.assertRaises(t2.Invalid):
            t2.parse_seg_witness(base + b"\x00\x00\x00\x00")
        with self.assertRaises(t2.Invalid):
            t2.parse_seg_witness(enc + b"\x00")

    def test_budget_weight(self):
        """A RAM port costs MEM_WEIGHT extWords, never shared; a ROM port costs like a ROM."""
        data = ex.rammix_file()
        nl = t2.decode(data)
        read = t2._file_reader(data)
        bud = t2.Budget(6000, 10, 16)
        for o, s, l in t2.segment_boundaries(nl, bud)[:-1]:
            seg = t2.canonical_segment(read, len(data), o, s, l, bud)
            ports = sum(r[0].op in (t2.OP_RAMR, t2.OP_RAMW) for r in seg.records)
            self.assertLessEqual(len(seg.cost.ext), 10)
            self.assertGreaterEqual(len(seg.cost.ext), ports * t2.MEM_WEIGHT)
        rr = [r.raw for r in nl.records if r.raw.op == t2.OP_RAMR][0]
        rw = [r.raw for r in nl.records if r.raw.op == t2.OP_RAMW][0]
        rp = [r.raw for r in nl.records if r.raw.op == t2.OP_ROMP][0]
        self.assertEqual((t2.record_gates(rr), t2.record_gates(rw), t2.record_gates(rp)), (12 + 6, 1 + 6 + 12, 12 + 6))

    def test_witness_bound(self):
        self.assertEqual(t2.seg_witness_bound(), 84208)
        self.assertEqual(t2.seg_witness_bound_ram(), 84208)     # the memory part displaces more than it adds
        self.assertLessEqual(t2.seg_witness_bound_ram(), t2.WITNESS_CAP)


class TestVectors(unittest.TestCase):
    def test_ram_steps_and_disputes(self):
        """The RAM step vectors are reproduced from the file hex alone (outputs, new state, new memory and the
        three roots), and every RAM dispute vector is won by the honest party."""
        import json
        import make_vectors
        with open(make_vectors.OUT) as f:
            v = json.load(f)
        for name in ("ram16", "rammix"):
            nl = t2.decode(bytes.fromhex(v["files"][name]["hex"]))
            state, mem = [0] * nl.n_latch, {}
            for row in v["step"][name]:
                bits_ = [int(c) for c in row["inputs"]]
                self.assertEqual(row["memory"], {str(k): list(mem.get(k, [0] * (1 << a)))
                                                 for k, (a, _) in sorted(nl.banks.items())})
                _, outs, state, mem, _ = t2.evaluate_full(nl, state, mem, bits_)
                self.assertEqual(row["outputs"], "".join(map(str, outs)))
                self.assertEqual(row["newState"], "".join(map(str, state)))
                self.assertEqual(row["stateRootNext"], t2.hx(t2.combine_state(bytes.fromhex(row["latchRootNext"][2:]),
                                                                                bytes.fromhex(row["memRootNext"][2:]))))
                self.assertEqual(row["memRootNext"], t2.hx(t2.mem_root(nl, mem)))
        ram_disputes = {k: r for k, r in v["disputes"].items() if k.split("/")[0] in ("ram16", "rammix")}
        self.assertEqual(len(ram_disputes), 28)
        for k, r in ram_disputes.items():
            self.assertEqual(r["winner"], "honest", k)
            self.assertEqual(r["verifyCalls"], 0 if r["final"].startswith("none") else 1, k)


def _observable(nl, inputs, K, budget, cands):
    honest = bs.Party("h", nl, inputs, K, budget)
    for c in cands:
        if bs.Party("x", nl, inputs, K, budget, c).cp_leaves != honest.cp_leaves:
            return c
    raise AssertionError("no observable lie")


def ram_cheats(nl, inputs, K, budget, bank):
    """Cheats about a RAM read, a forged memory write, the write data and a LATCH (spec Test Cases)."""
    ramr = [r.first_out for r in nl.records if r.raw.op == t2.OP_RAMR]
    ramw = [r for r in nl.records if r.raw.op == t2.OP_RAMW][0]
    wdata_sig = ramw.raw.refs[1 + ramw.raw.a]
    a, w = nl.banks[bank]
    return {
        "ram_read": _observable(nl, inputs, K, budget, [{"kind": "gate", "t": t, "signal": ramr[p] + j}
                                                        for t in (13, 21, 30) for p in range(len(ramr))
                                                        for j in range(4)]),
        "ram_write": _observable(nl, inputs, K, budget, [{"kind": "mem", "t": t, "bank": bank, "addr": k, "bit": 1}
                                                         for t in (17, 26) for k in range(1 << a)]),
        "write_data": _observable(nl, inputs, K, budget, [{"kind": "gate", "t": t, "signal": wdata_sig}
                                                          for t in range(9, 40)]),
        "latch": {"kind": "state", "t": 22, "latch": 2},
    }


class TestDispute(unittest.TestCase):
    """Cheaters about a RAM read, a RAM write (forged memory), the write data and a LATCH, as claimant and as
    challenger, with several segments per beat (tiny budget) and with one: the honest party wins with exactly one
    verifier call, and every memory lie is decided by verify_segment."""

    def test_disputes(self):
        for name, fn, inputs, bank in (("ram16", ex.ram16_file, ex.ram16_inputs(40, 5), ex.RAM16_BANK),
                                       ("rammix", ex.rammix_file, ex.rammix_inputs(40, 6), 0)):
            data = fn()
            nl = t2.decode(data)
            store, nh2 = t2.oracle_for(data)
            K = 4
            for budget in (t2.TINY_BUDGET, t2.DEFAULT_BUDGET):
                ref = bs.Referee(store, budget)
                honest = bs.Party("honest", nl, inputs, K, budget)
                for cname, c in ram_cheats(nl, inputs, K, budget, bank).items():
                    liar = bs.Party(f"cheater_{cname}", nl, inputs, K, budget, c)
                    for role, (cl, ch) in (("claimant", (liar, honest)), ("challenger", (honest, liar))):
                        r = bs.run_one(ref, nh2, cl, ch)
                        self.assertEqual(r["winner"], "honest", (name, budget, cname, role, r))
                        self.assertEqual(r["verifyCalls"], 1, (name, budget, cname, role, r))
                        if cname != "latch":
                            self.assertTrue(r["final"].startswith("verify_segment"), (name, cname, r["final"]))
                self.assertEqual(bs.run_one(ref, nh2, honest, bs.Party("h2", nl, inputs, K, budget))["verifyCalls"], 0)

    def test_claimant_without_opening_loses(self):
        """At level 3 of a MEM file the claimant must open the agreed stateRoot_t."""
        data = ex.ram16_file()
        nl = t2.decode(data)
        store, nh2 = t2.oracle_for(data)
        inputs = ex.ram16_inputs(12, 5)
        honest = bs.Party("honest", nl, inputs, 4, t2.TINY_BUDGET)
        liar = bs.Party("cheater", nl, inputs, 4, t2.TINY_BUDGET,
                        _observable(nl, inputs, 4, t2.TINY_BUDGET,
                                    [{"kind": "mem", "t": t, "bank": ex.RAM16_BANK, "addr": k, "bit": 0}
                                     for t in (6, 7, 8) for k in range(16)]))
        liar.state_opening = lambda t: None
        r = bs.run_one(bs.Referee(store, t2.TINY_BUDGET), nh2, liar, honest)
        self.assertEqual((r["winner"], r["verifyCalls"]), ("honest", 0))


if __name__ == "__main__":
    unittest.main()
