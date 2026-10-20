"""unittest suite for the tapnet/2 reference implementation. Run: python3 -m unittest -v test_tapnet2"""
from __future__ import annotations

import importlib.util
import copy
import json
import os
import sys
import unittest

import bisect_sim
import examples
import make_vectors
import tapnet2 as t2

HERE = os.path.dirname(os.path.abspath(__file__))
# The independent TAP-02 evaluator: assets/tap-02/reference.py of the TAPs repository (MIT). It is found next to
# this folder in the TAPs repository and in the reference repository; TAP02_REFERENCE may point to another copy.
# Without it the equivalence tests compare tapnet2.py with itself only, and test_independent_evaluator_available
# is skipped.
TAP02_CANDIDATES = [os.environ.get("TAP02_REFERENCE", ""),
                    os.path.normpath(os.path.join(HERE, "..", "tap-02", "reference.py"))]


def _load_tap02():
    for path in TAP02_CANDIDATES:
        if path and os.path.exists(path):
            spec = importlib.util.spec_from_file_location("tap02_reference", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["tap02_reference"] = mod
            spec.loader.exec_module(mod)
            return mod
    return None


tap02 = _load_tap02()      # independent TAP-02 evaluator (beat returns (new_state, outputs))


def check_tapnet1(data: bytes) -> None:
    """Checks a ROM-free expansion as a tapnet/1 file: the 16-byte header with version byte 0x01, then
    non-empty, REF-free TAP-02 records that TAP-02's own decoder and loader accept."""
    assert len(data) >= 16 and data[:6] == b"TAPNET" and data[6] == 0x01 and data[7] == 0, "tapnet/1 header"
    n_in, n_out, records = int.from_bytes(data[8:12], "big"), int.from_bytes(data[12:16], "big"), data[16:]
    assert records, "no records"
    els = tap02.decode(records, n_in)
    assert not any(e.op == tap02.REF for e in els), "REF record"
    tap02.load(records, n_in, n_out)


class TestVectors(unittest.TestCase):
    def test_byte_identical_regeneration(self):
        blob = make_vectors.render(make_vectors.build_vectors())
        with open(make_vectors.OUT, "rb") as f:
            self.assertEqual(f.read(), blob, "vectors.json differs from a fresh regeneration")


class TestFormat(unittest.TestCase):
    def test_examples_valid(self):
        for name, fn in examples.EXAMPLES.items():
            m = t2.check_file(fn())
            self.assertGreater(m["romRecords"], 0, name)
        self.assertGreater(len(examples.large_file()), 49152)
        self.assertGreaterEqual(t2.n_chunks(len(examples.large_file())), 4)

    def test_invalid_samples(self):
        for name, data, reason in examples.invalid_files():
            with self.assertRaises(t2.Invalid, msg=name) as cm:
                t2.check_file(data)
            self.assertIn(reason, str(cm.exception), name)

    def test_tapnet2_without_rom_is_valid(self):
        data = t2.make_file(t2.enc_nand(2, 2), 1, 1)
        self.assertEqual(t2.check_file(data)["romRecords"], 0)

    def test_tapnet1_not_accepted_as_tapnet2(self):
        data = t2.make_file(t2.enc_nand(2, 2), 1, 1, t2.VERSION1)
        with self.assertRaises(t2.Invalid):
            t2.check_file(data)
        t2.decode(data, (t2.VERSION1,))

    def test_rom_dimension_limits(self):
        for a, w in ((1, 1), (1, 256), (20, 1), (3, 7)):
            ents = [k % (1 << w) for k in range(1 << a)] if w < 30 else [0] * (1 << a)
            data = t2.make_file(t2.enc_rom([2] * a, w, t2.pack_rom_data(ents, a, w)), 1, 1)
            m = t2.check_file(data)
            self.assertEqual(m["romBits"], (1 << a) * w)

    def test_file_size_limit(self):
        hdr = t2.MAGIC + bytes([2, 0]) + t2.u32(1) + t2.u32(1)
        rec = t2.enc_nand(2, 2)
        n = (t2.MAX_FILE - len(hdr)) // 7
        ok = hdr + rec * n
        self.assertLessEqual(len(ok), t2.MAX_FILE)
        big = ok + b"\x00" * (t2.MAX_FILE + 1 - len(ok))
        with self.assertRaises(t2.Invalid) as cm:
            t2.check_file(big)
        self.assertIn("64 MiB", str(cm.exception))

    def test_signal_limit(self):
        rec = t2.enc_rom([2], 256, bytes(64))     # 256 signals each
        n_in = 254                                 # 2 + 254 + 65535 * 256 = 2^24 exactly
        at_limit = t2.make_file(rec * 65535, n_in, 1)
        self.assertEqual(t2.check_file(at_limit)["signals"], 1 << 24)
        with self.assertRaises(t2.Invalid) as cm:
            t2.check_file(at_limit + t2.enc_nand(2, 2))
        self.assertIn("2^24", str(cm.exception))
        with self.assertRaises(t2.Invalid):
            t2.check_file(t2.make_file(rec * 65536, 1, 1))


class TestStep(unittest.TestCase):
    def test_sbox_all(self):
        f, s = examples.sbox_file(), examples.aes_sbox()
        self.assertEqual(s[0x00], 0x63)
        self.assertEqual(s[0x53], 0xED)
        for x in range(256):
            o, ns = t2.step(f, [], [(x >> i) & 1 for i in range(8)])
            self.assertEqual(sum(b << i for i, b in enumerate(o)), s[x])
            self.assertEqual(ns, [])

    def test_mul4_all(self):
        f = examples.mul4_file()
        for k in range(256):
            o, _ = t2.step(f, [], [(k >> i) & 1 for i in range(8)])
            self.assertEqual(sum(b << i for i, b in enumerate(o)), (k & 15) * (k >> 4))

    def test_message(self):
        nl = t2.decode(examples.message_file())
        st, chars = [0] * nl.n_latch, []
        for _ in range(64):
            o, st = t2.step(nl, st, [1])
            chars.append(sum(b << i for i, b in enumerate(o)))
        self.assertEqual(chars[:examples.MESSAGE_DELAY], [0] * examples.MESSAGE_DELAY)
        self.assertEqual(bytes(chars[examples.MESSAGE_DELAY:examples.MESSAGE_DELAY + len(examples.MESSAGE)]),
                         examples.MESSAGE)
        o, st2 = t2.step(nl, st, [0])           # enable = 0 holds the counter
        self.assertEqual(st2, st)


class TestExpansion(unittest.TestCase):
    def _equiv(self, data: bytes, rng: examples.XorShift, beats: int):
        nl2 = t2.decode(data)
        exp = t2.expand_rom_to_tapnet1(data)
        nl1 = t2.decode(exp, (t2.VERSION1,))
        self.assertEqual((nl1.n_in, nl1.n_out, nl1.n_latch, nl1.rom_records), (nl2.n_in, nl2.n_out, nl2.n_latch, 0))
        if tap02:
            check_tapnet1(exp)
            c = tap02.load(exp[16:], nl1.n_in, nl1.n_out)
        st = rng.bits(nl2.n_latch)
        for _ in range(beats):
            ins = rng.bits(nl2.n_in)
            o2, n2 = t2.step(nl2, st, ins)
            o1, n1 = t2.step(nl1, st, ins)
            self.assertEqual((o1, n1), (o2, n2))
            if tap02:
                rn, ro = tap02.beat(c, list(st), list(ins))   # TAP-02 order: (new_state, outputs)
                self.assertEqual((ro, rn), (o2, n2))
            st = n2 if rng.below(3) else rng.bits(nl2.n_latch)

    def test_examples_equivalent(self):
        rng = examples.XorShift(2024)
        for name in ("sbox", "mul4", "message"):
            self._equiv(examples.EXAMPLES[name](), rng, 64)

    def test_random_rom_netlists(self):
        rng = examples.XorShift(77)
        for _ in range(60):
            self._equiv(examples.random_rom_file(rng), rng, 24)

    def test_independent_evaluator_available(self):
        if tap02 is None:
            self.skipTest("assets/tap-02/reference.py not found; equivalence checked against tapnet2 only")
        self.assertTrue(hasattr(tap02, "beat"))


class TestMerkle(unittest.TestCase):
    def test_prefix_accumulator(self):
        rng = examples.XorShift(5)
        for n in list(range(0, 700, 7)) + [1023, 1024, 1025, 4097]:
            vals = rng.bits(n)
            acc = t2.Acc(t2.DOM_SIGNAL, t2.KIND_BITS)
            for v in vals:
                acc.append(v)
            want = t2.prefix_root(t2.DOM_SIGNAL, vals, n)
            self.assertEqual(acc.root(), want)
            self.assertEqual(t2.Acc.from_values(t2.DOM_SIGNAL, t2.KIND_BITS, vals, n).root(), want)
            pk, pa = acc.frontier()
            self.assertIsNotNone(t2.Acc.from_frontier(t2.DOM_SIGNAL, t2.KIND_BITS, n, pk, pa, want))
            if pa is not None:
                bad = bytearray(pa)
                bad[31] |= 0x80 if n % 256 <= 255 else 0
                self.assertIsNone(t2.Acc.from_frontier(t2.DOM_SIGNAL, t2.KIND_BITS, n, pk, bytes(bad), want))
        d = [rng.below(1 << 24) for _ in range(77)]
        acc = t2.Acc(t2.DOM_LATCHD, t2.KIND_U32)
        for x in d:
            acc.append(x)
        self.assertEqual(acc.root(), t2.prefix_root(t2.DOM_LATCHD, d, 77, t2.KIND_U32))

    def test_multiproof(self):
        rng = examples.XorShift(9)
        for size in range(1, 70):
            leaves = [t2.sha256(t2.u32(i)) for i in range(size)]
            lv = t2.merkle_levels(leaves)
            for _ in range(8):
                idx = sorted({rng.below(size) for _ in range(1 + rng.below(size))})
                pr = t2.multiproof(lv, idx)
                self.assertTrue(t2.multiverify(lv[-1][0], size, {i: leaves[i] for i in idx}, pr))
                bad = {i: leaves[i] for i in idx}
                bad[idx[-1]] = t2.sha256(b"x")
                self.assertFalse(t2.multiverify(lv[-1][0], size, bad, pr))
                if pr:
                    self.assertFalse(t2.multiverify(lv[-1][0], size, {i: leaves[i] for i in idx}, pr[:-1]))

    def test_chunks_and_netlist_hash2(self):
        data = examples.large_file()
        lv = t2.chunk_levels(data)
        root = lv[-1][0]
        nh2 = t2.netlist_hash2(data)
        self.assertEqual(nh2, t2.sha256(b"\x03" + t2.u64(len(data)) + root))
        for i, c in enumerate(t2.split_chunks(data)):
            pr = t2.chunk_proof(data, i, lv)
            self.assertTrue(t2.verify_chunk(nh2, len(data), root, i, c, pr))
            self.assertFalse(t2.verify_chunk(nh2, len(data), root, i, c[:-1] + bytes([c[-1] ^ 1]), pr))
            self.assertFalse(t2.verify_chunk(nh2, len(data) + 1, root, i, c, pr))


class TestSegments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = {c[0]: c for c in make_vectors.contexts()}

    def _ctx(self, name):
        _, data, nl, state, ins, _ = self.ctx[name]
        V, outs, ns = t2.evaluate(nl, state, ins)
        return data, nl, state, V, outs, ns

    def test_segmentation_is_maximal_and_covers_file(self):
        for name in ("message", "large"):
            data, nl, *_ = self._ctx(name)
            for budget in (t2.DEFAULT_BUDGET, t2.TINY_BUDGET):
                b = t2.segment_boundaries(nl, budget)
                self.assertEqual(b[0], (16, 2 + nl.n_in, 0))
                self.assertEqual(b[-1], (len(data), nl.n_signals, nl.n_latch))
                read = t2._file_reader(data)
                for (o, s, l), nxt in zip(b, b[1:]):
                    seg = t2.canonical_segment(read, len(data), o, s, l, budget)
                    self.assertEqual((seg.end_off, seg.end_signal, seg.end_latch), nxt)
                    self.assertTrue(seg.cost.within(budget) or len(seg.records) == 1)
                    if seg.next_record is not None:
                        more = t2.seg_cost_add(seg.cost, seg.next_record, s, seg.end_latch)
                        self.assertFalse(more.within(budget))

    def _verify_all(self, name, budget, every=1):
        data, nl, state, V, *_ = self._ctx(name)
        store, nh2 = t2.oracle_for(data)
        sr = t2.state_root(state)
        tuples = t2.segment_tuples(nl, V, budget)
        js = sorted(set(range(1, len(tuples), every)) | {len(tuples) - 1})
        for j in js:
            w = t2.build_seg_witness(nl, V, state, tuples[j - 1], tuples[j].byte_offset)
            self.assertTrue(t2.verify_segment(store, nh2, sr, tuples[j - 1], tuples[j], w, budget), (name, j))
            self.assertLessEqual(t2.LAST_READ["chunks"], budget.chunks + 2)
            ser = t2.serialize_seg_witness(w)
            self.assertEqual(t2.parse_seg_witness(ser), w)
            self.assertTrue(t2.verify_segment(store, nh2, sr, tuples[j - 1], tuples[j], ser, budget))
            self.assertFalse(t2.verify_segment(store, t2.sha256(nh2), sr, tuples[j - 1], tuples[j], w, budget))

    def test_verify_positive(self):
        self._verify_all("sbox", t2.DEFAULT_BUDGET)
        self._verify_all("mul4", t2.TINY_BUDGET)
        self._verify_all("message", t2.DEFAULT_BUDGET)
        self._verify_all("message", t2.TINY_BUDGET)
        self._verify_all("large", t2.DEFAULT_BUDGET)
        self._verify_all("large", t2.TINY_BUDGET, every=29)

    def test_verify_negative_mutations(self):
        for name in ("message", "large"):
            data, nl, state, V, *_ = self._ctx(name)
            store, nh2 = t2.oracle_for(data)
            for budget in (t2.DEFAULT_BUDGET, t2.TINY_BUDGET):
                tuples = t2.segment_tuples(nl, V, budget)
                for j in sorted({1, len(tuples) - 1, min(3, len(tuples) - 1)}):
                    cases = make_vectors.seg_mutations(nl, V, state, tuples, j, budget)
                    self.assertGreaterEqual(len(cases), 5)
                    for case, a, b, w, bud, sroot in cases:
                        self.assertFalse(t2.verify_segment(store, nh2, sroot, a, b, w, bud), (name, budget, j, case))

    def test_next_record_straddling_read_through_oracle(self):
        data = examples.nand_only_file()
        nl = t2.decode(data)
        store, nh2 = t2.oracle_for(data)
        ins = examples.XorShift(0xBEEF).bits(nl.n_in)
        V, _, _ = t2.evaluate(nl, [], ins)
        unit = t2.Budget(1, 256, 8)
        tuples = t2.segment_tuples(nl, V, unit)
        found = make_vectors.straddling_next_record_case(nl, V, tuples, unit)
        self.assertIsNotNone(found)
        j, a, b = found
        w = t2.build_seg_witness(nl, V, [], a, b.byte_offset)
        self.assertTrue(t2.verify_segment(store, nh2, t2.EMPTY, a, b, w, unit))
        self.assertEqual(t2.LAST_READ["chunks"], len(t2.segment_chunks(nl, V, a, b.byte_offset)))
        self.assertGreaterEqual(t2.LAST_READ["chunks"], 2)

    def test_rom_data_only_chunk_read_through_oracle(self):
        data = examples.large_file()
        nl = t2.decode(data)
        store, nh2 = t2.oracle_for(data)
        for budget in (t2.DEFAULT_BUDGET, t2.TINY_BUDGET):
            seed, st, ins, (j, a, b) = make_vectors.rom_search_context(nl, budget)
            V, _, _ = t2.evaluate(nl, st, ins)
            w = t2.build_seg_witness(nl, V, st, a, b.byte_offset)
            self.assertTrue(t2.verify_segment(store, nh2, t2.state_root(st), a, b, w, budget))
            self.assertEqual(t2.LAST_READ["chunks"], len(t2.segment_chunks(nl, V, a, b.byte_offset)))

    def test_oracle_binding(self):
        """The verifier reads chunks only for the netlistHash2 it is given, and only from a complete upload."""
        data, nl, state, V, *_ = self._ctx("large")
        sr = t2.state_root(state)
        tuples = t2.segment_tuples(nl, V)
        a, b = tuples[0], tuples[1]
        w = t2.build_seg_witness(nl, V, state, a, b.byte_offset)
        store, nh2 = t2.oracle_for(data)
        self.assertTrue(t2.verify_segment(store, nh2, sr, a, b, w))
        self.assertFalse(t2.verify_segment(t2.LargeStore([nh2]), nh2, sr, a, b, w))       # never uploaded
        part = t2.LargeStore([nh2])
        lv = t2.chunk_levels(data)
        part.beginLarge(nh2, len(data), lv[-1][0])
        for i in range(t2.n_chunks(len(data)) - 1):
            part.putChunk(nh2, i, t2.split_chunks(data)[i], t2.chunk_proof(data, i, lv))
        self.assertFalse(part.largeStored(nh2))
        self.assertFalse(t2.verify_segment(part, nh2, sr, a, b, w))                        # one chunk missing
        other, oh = t2.oracle_for(examples.message_file())
        self.assertFalse(t2.verify_segment(other, nh2, sr, a, b, w))                       # store of another file
        self.assertFalse(t2.verify_segment(other, oh, sr, a, b, w))                        # another file

    def test_initial_state_empty(self):
        """stateRoot = EMPTY means every LATCH reads 0, with no state words; a real root needs its words."""
        data = examples.message_file()
        nl = t2.decode(data)
        store, nh2 = t2.oracle_for(data)
        zero = [0] * nl.n_latch
        V, _, _ = t2.evaluate(nl, zero, [1])
        zr = t2.state_root(zero)
        self.assertNotEqual(zr, t2.EMPTY)
        for budget in (t2.DEFAULT_BUDGET, t2.TINY_BUDGET):
            tuples = t2.segment_tuples(nl, V, budget)
            w0 = t2.build_seg_witness(nl, V, zero, tuples[0], tuples[1].byte_offset, t2.EMPTY)
            wz = t2.build_seg_witness(nl, V, zero, tuples[0], tuples[1].byte_offset, zr)
            self.assertEqual((w0.s_size, w0.s_words, w0.s_proof), (0, {}, []))
            self.assertTrue(t2.verify_segment(store, nh2, t2.EMPTY, tuples[0], tuples[1], w0, budget))
            self.assertTrue(t2.verify_segment(store, nh2, zr, tuples[0], tuples[1], wz, budget))
            self.assertFalse(t2.verify_segment(store, nh2, t2.EMPTY, tuples[0], tuples[1], wz, budget))
            self.assertFalse(t2.verify_segment(store, nh2, zr, tuples[0], tuples[1], w0, budget))
            m = copy.deepcopy(w0)
            m.s_size = 1
            self.assertFalse(t2.verify_segment(store, nh2, t2.EMPTY, tuples[0], tuples[1], m, budget))
        # with a nonzero state, EMPTY must not verify the honest tuples (LATCH values differ)
        st = [1] * nl.n_latch
        V1, _, _ = t2.evaluate(nl, st, [1])
        tuples = t2.segment_tuples(nl, V1, t2.TINY_BUDGET)
        w = t2.build_seg_witness(nl, V1, st, tuples[0], tuples[1].byte_offset, t2.EMPTY)
        self.assertFalse(t2.verify_segment(store, nh2, t2.EMPTY, tuples[0], tuples[1], w, t2.TINY_BUDGET))

    def test_witness_size_default_budget(self):
        data = examples.nand_only_file()
        nl = t2.decode(data)
        V, _, _ = t2.evaluate(nl, [], [0] * nl.n_in)
        tuples = t2.segment_tuples(nl, V)
        w = t2.build_seg_witness(nl, V, [], tuples[1], tuples[2].byte_offset)
        size = len(t2.serialize_seg_witness(w))
        self.assertLess(size, 4096)                 # tree proofs only; no chunk bytes
        self.assertLess(t2.seg_witness_bound(), t2.WITNESS_CAP)
        # spec 6.3: below 20 external words the first record (a ROM with a = 20) sets the bound
        self.assertEqual(t2.seg_witness_bound(t2.Budget(16, 4, 2)), t2.seg_witness_bound(t2.Budget(16, 20, 2)))
        self.assertGreater(t2.seg_witness_bound(t2.Budget(16, 21, 2)), t2.seg_witness_bound(t2.Budget(16, 4, 2)))
        self.assertLess(t2.end_witness_bound(), t2.WITNESS_CAP)


class TestWitnessFormat(unittest.TestCase):
    def test_vectors_parse_and_verify(self):
        """Every witness in vectors.json parses (spec 6.3 encoding), re-serializes identically, and gives the expected
        result when verified from its hex and the data of the vector alone."""
        with open(make_vectors.OUT) as f:
            v = json.load(f)
        files = {k: bytes.fromhex(e["hex"]) for k, e in v["files"].items()}
        stores = {k: t2.oracle_for(d) for k, d in files.items()}
        hx = lambda x: bytes.fromhex(x[2:])  # noqa: E731
        tup = lambda x: t2.SegTuple(x[0], x[1], x[2], hx(x[3]), hx(x[4]), *([hx(x[5])] if len(x) > 5 else []))  # noqa
        for c in v["verifySegment"]:
            ser = bytes.fromhex(c["witnessHex"])
            try:
                self.assertEqual(t2.serialize_seg_witness(t2.parse_seg_witness(ser)), ser)
            except t2.Invalid:
                self.assertFalse(c["expect"], c["case"])      # a malformed encoding (memory extension cases)
            store, nh2 = stores[c["example"]]
            if c["oracle"] == "none":
                store = t2.LargeStore([nh2])
            elif c["oracle"] == "chunk0":
                data = files[c["example"]]
                lv = t2.chunk_levels(data)
                store = t2.LargeStore([nh2])
                store.beginLarge(nh2, len(data), lv[-1][0])
                store.putChunk(nh2, 0, t2.split_chunks(data)[0], t2.chunk_proof(data, 0, lv))
            bud = t2.Budget(**c["budgetUsed"])
            self.assertEqual(len(c["a"]), 6 if files[c["example"]][7] & t2.FLAG_MEM else 5)
            got = t2.verify_segment(store, nh2, hx(c["latchRoot"]), tup(c["a"]), tup(c["b"]), ser, bud,
                                    hx(c["memRoot"]))
            self.assertEqual(got, c["expect"], c["case"])
        for c in v["verifyBeatEnd"]:
            ser = bytes.fromhex(c["witnessHex"])
            self.assertEqual(t2.serialize_end_witness(t2.parse_end_witness(ser)), ser)
            store, nh2 = stores[c["example"]]
            seg_m = tup(c["segM"])
            et = lambda x: t2.EndTuple(x[0], bytes.fromhex(x[1][2:]))  # noqa
            got = t2.verify_beat_end(store, nh2, c["nOut"], seg_m, et(c["a"]), et(c["b"]), ser)
            self.assertEqual(got, c["expect"], c["case"])

    def test_malformed_witness_fails(self):
        data = examples.large_file()
        nl = t2.decode(data)
        store, nh2 = t2.oracle_for(data)
        rng = examples.XorShift(3)
        st, ins = rng.bits(nl.n_latch), rng.bits(nl.n_in)
        V, _, _ = t2.evaluate(nl, st, ins)
        tuples = t2.segment_tuples(nl, V, t2.TINY_BUDGET)
        sr = t2.state_root(st)
        j = next(j for j in range(1, len(tuples))
                 if len(t2.build_seg_witness(nl, V, st, tuples[j - 1], tuples[j].byte_offset).p_words) >= 2)
        w = t2.build_seg_witness(nl, V, st, tuples[j - 1], tuples[j].byte_offset)
        ser = t2.serialize_seg_witness(w)
        args = (store, nh2, sr, tuples[j - 1], tuples[j])
        self.assertTrue(t2.verify_segment(*args, ser, t2.TINY_BUDGET))
        self.assertFalse(t2.verify_segment(*args, ser[:-1], t2.TINY_BUDGET))            # truncated
        self.assertFalse(t2.verify_segment(*args, ser + b"\x00", t2.TINY_BUDGET))       # trailing byte
        # swap the first two P words: indices no longer strictly increasing
        off = 1 + 32 * len(w.p_peaks) + 1 + (32 if w.p_partial is not None else 0) + 2
        swapped = ser[:off] + ser[off + 36:off + 72] + ser[off:off + 36] + ser[off + 72:]
        self.assertFalse(t2.verify_segment(*args, swapped, t2.TINY_BUDGET))
        flag = 1 + 32 * len(w.p_peaks)
        bad = ser[:flag] + b"\x02" + ser[flag + 1:]
        self.assertFalse(t2.verify_segment(*args, bad, t2.TINY_BUDGET))                  # bad partial flag

    def test_multiproof_bound_is_tight(self):
        rng = examples.XorShift(11)
        for depth in (3, 4, 5, 6):
            n = 1 << depth
            lv = t2.merkle_levels([t2.sha256(t2.u32(i)) for i in range(n)])
            for k in (1 << e for e in range(depth + 1)):          # evenly spread leaves reach the bound
                self.assertEqual(len(t2.multiproof(lv, list(range(0, n, n // k)))), t2.multiproof_bound(k, depth))
            for _ in range(300):
                idx = sorted({rng.below(n) for _ in range(1 + rng.below(n))})
                self.assertLessEqual(len(t2.multiproof(lv, idx)), t2.multiproof_bound(len(idx), depth))


class TestStore(unittest.TestCase):
    def test_begin_large_bounds(self):
        root = t2.sha256(b"r")
        for length, want in ((16, "BadLength"), (17, None), (t2.MAX_FILE, None), (t2.MAX_FILE + 1, "BadLength")):
            h = t2.nh2_from(length, root)
            st = t2.LargeStore([h])
            if want:
                with self.assertRaises(t2.StoreError) as cm:
                    st.beginLarge(h, length, root)
                self.assertEqual(cm.exception.name, want)
            else:
                st.beginLarge(h, length, root)
                self.assertEqual(st.largeInfo(h), (length, 0, 0, 0, False))
        h = t2.nh2_from(17, root)
        with self.assertRaises(t2.StoreError) as cm:
            t2.LargeStore([]).beginLarge(h, 17, root)
        self.assertEqual(cm.exception.name, "UnknownNetlist")
        with self.assertRaises(t2.StoreError) as cm:
            t2.LargeStore([h]).beginLarge(h, 18, root)
        self.assertEqual(cm.exception.name, "HashMismatch")

    def test_upload_any_order_idempotent(self):
        data = examples.large_file()
        lv = t2.chunk_levels(data)
        h = t2.netlist_hash2(data)
        chunks = t2.split_chunks(data)
        st = t2.LargeStore([h])
        with self.assertRaises(t2.StoreError) as cm:
            st.putChunk(h, 0, chunks[0], t2.chunk_proof(data, 0, lv))
        self.assertEqual(cm.exception.name, "NotBegun")
        st.beginLarge(h, len(data), lv[-1][0])
        st.beginLarge(h, len(data), lv[-1][0])                      # repeated: no effect, no event
        with self.assertRaises(t2.StoreError) as cm:
            st.largeChunk(h, 2)
        self.assertEqual(cm.exception.name, "ChunkNotStored")
        for i in (5, 2, 0, 0, 4, 1):
            st.putChunk(h, i, chunks[i], t2.chunk_proof(data, i, lv))
            self.assertFalse(st.largeStored(h))
        self.assertEqual(st.largeInfo(h), (len(data), 16, 16, 5, False))
        with self.assertRaises(t2.StoreError) as cm:
            st.putChunk(h, 3, chunks[3][:-1] + bytes([chunks[3][-1] ^ 1]), t2.chunk_proof(data, 3, lv))
        self.assertEqual(cm.exception.name, "BadChunk")
        st.putChunk(h, 3, chunks[3], t2.chunk_proof(data, 3, lv))
        self.assertTrue(st.largeStored(h))
        self.assertEqual(st.largeInfo(h), (len(data), 16, 16, 6, True))
        self.assertEqual([e[0] for e in st.events], ["LargeBegun"] + ["ChunkStored"] * 6 + ["LargeStored"])
        self.assertEqual(b"".join(st.largeChunk(h, i) for i in range(6)), data)


class TestBeatEnd(unittest.TestCase):
    def test_positive_and_negative(self):
        for c in make_vectors.contexts():
            name, data, nl, state, ins, _ = c
            V, outs, ns = t2.evaluate(nl, state, ins)
            store, nh2 = t2.oracle_for(data)
            seg_m = t2.segment_tuples(nl, V)[-1]
            ot, st = t2.out_tuples(outs), t2.state_tuples(ns)
            self.assertEqual(ot[-1].root, t2.out_root(outs))
            self.assertEqual(st[-1].root, t2.state_root(ns))
            for kind, lst in (("out", ot), ("state", st)):
                for k in range(1, len(lst)):
                    w = (t2.build_out_witness(nl, V, outs, k - 1) if kind == "out"
                         else t2.build_state_witness(nl, V, ns, k - 1))
                    self.assertTrue(t2.verify_beat_end(store, nh2, nl.n_out, seg_m, lst[k - 1], lst[k], w),
                                    (name, kind, k))
                    ser = t2.serialize_end_witness(w)
                    self.assertTrue(t2.verify_beat_end(store, nh2, nl.n_out, seg_m, lst[k - 1], lst[k], ser))
                    self.assertFalse(t2.verify_beat_end(t2.LargeStore([nh2]), nh2, nl.n_out, seg_m,
                                                        lst[k - 1], lst[k], w))
                    for case, b, ww, sm, n_out in make_vectors.end_mutations(kind, lst[k - 1], lst[k], w, seg_m,
                                                                             nl.n_out):
                        self.assertFalse(t2.verify_beat_end(store, nh2, n_out, sm, lst[k - 1], b, ww),
                                         (name, kind, case))

    def test_state_lie_detected(self):
        _, data, nl, state, ins, _ = [c for c in make_vectors.contexts() if c[0] == "large"][0]
        V, outs, ns = t2.evaluate(nl, state, ins)
        store, nh2 = t2.oracle_for(data)
        seg_m = t2.segment_tuples(nl, V)[-1]
        bad = list(ns)
        bad[7] ^= 1
        st = t2.state_tuples(bad)
        w = t2.build_state_witness(nl, V, bad, 0)
        self.assertFalse(t2.verify_beat_end(store, nh2, nl.n_out, seg_m, st[0], st[1], w))
        # claiming the new state root EMPTY (the initial-state sentinel) after a beat fails as well
        w = t2.build_state_witness(nl, V, ns, 0)
        good = t2.state_tuples(ns)
        self.assertTrue(t2.verify_beat_end(store, nh2, nl.n_out, seg_m, good[0], good[1], w))
        self.assertFalse(t2.verify_beat_end(store, nh2, nl.n_out, seg_m, good[0], good[1]._replace(root=t2.EMPTY), w))


class TestDisputes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = bisect_sim.run_scenarios()
        cls.pos = bisect_sim.checkpoint_position_scenarios()
        cls.idx = bisect_sim.wrong_index_scenarios()

    def test_honest_always_wins(self):
        for k, r in list(self.res.items()) + list(self.pos.items()):
            self.assertTrue(r["winner"].startswith("honest"), (k, r))

    def test_exactly_one_verifier_call(self):
        for k, r in list(self.res.items()) + list(self.pos.items()):
            if k.endswith("honest_vs_honest"):
                self.assertEqual((r["final"], r["verifyCalls"], r["totalRounds"]), ("none", 0, 0))
                continue
            if r["final"] == "none (reveal check)":
                self.assertEqual(r["verifyCalls"], 0, k)
                continue
            self.assertEqual(r["verifyCalls"], 1, k)
            self.assertTrue(r["final"].startswith(("verify_segment", "verify_beat_end")), k)
            self.assertEqual(r["rounds"]["checkpoint"], 1, k)
            self.assertLess(r["witnessBytes"], 4096, k)

    def test_cheater_classes_covered(self):
        for ex in ("message", "large"):
            for cls in ("a_short", "a_long", "b_state", "c_rom", "d_checkpoint", "e_eof_extra", "f_beat0",
                        "g_empty_state"):
                for role in ("claimant", "challenger"):
                    r = self.res[f"{ex}/{cls}_as_{role}"]
                    self.assertTrue(r["loser"].startswith("cheater_"), (ex, cls, role))
            # each class ends where it should
            self.assertIn("seg_0 -> seg_1", self.res[f"{ex}/a_short_as_claimant"]["final"])
            self.assertIn("seg_0 -> seg_1", self.res[f"{ex}/a_long_as_claimant"]["final"])
            self.assertIn("[state]", self.res[f"{ex}/b_state_as_claimant"]["final"])
            self.assertIn("verify_segment", self.res[f"{ex}/c_rom_as_claimant"]["final"])
            self.assertIn("[state]", self.res[f"{ex}/d_checkpoint_as_claimant"]["final"])
            self.assertIn("verify_segment", self.res[f"{ex}/e_eof_extra_as_challenger"]["final"])
            self.assertIn("for the challenger", self.res[f"{ex}/e_eof_extra_as_challenger"]["reason"])
            self.assertEqual(self.res[f"{ex}/f_beat0_as_claimant"]["beat"], 0)      # decided with stateRoot EMPTY
            self.assertIn("verify_segment", self.res[f"{ex}/f_beat0_as_claimant"]["final"])
            self.assertIn("[state]", self.res[f"{ex}/g_empty_state_as_claimant"]["final"])
            self.assertIn("returned False", self.res[f"{ex}/g_empty_state_as_claimant"]["reason"])
            self.assertEqual(self.res[f"{ex}/h_inputs_withhold_as_claimant"]["reason"],
                             "inputs of the disputed beat not revealed")
            self.assertEqual(self.res[f"{ex}/h_inputs_wrong_as_claimant"]["reason"], "bad Merkle proof in a reveal")

    def test_hidden_middle_checkpoint_is_challengeable(self):
        """Reviewer's attack 3: a forged middle checkpoint with a correct last leaf; the challenger now wins."""
        self.assertEqual(len(self.pos), 4)
        for k, r in self.pos.items():
            self.assertTrue(r["winner"].startswith("honest"), k)
            self.assertEqual(r["verifyCalls"], 1, k)

    def test_wrong_index_challenger_loses(self):
        for k in ("message/index_1_vs_honest_claimant", "message/index_5_vs_honest_claimant",
                  "message/index_2_vs_forger_t24", "message/index_4_vs_forger_t24"):
            r = self.idx[k]
            self.assertTrue(r["loser"].startswith("challenger_index_"), k)
            self.assertEqual(r["reason"], "the named leaves are equal", k)
            self.assertEqual(r["verifyCalls"], 0, k)
        r = self.idx["message/index_3_vs_forger_t24"]
        self.assertTrue(r["winner"].startswith("challenger_index_3"))

    def test_claim_acceptance(self):
        ref, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("message")
        p = bisect_sim.Party("honest", nl, inputs, K, budget)
        h, T, K_, ir, leaves = p.claim_data(nh2)
        claim = ref.accept_claim(h, T, K_, ir, leaves)
        self.assertEqual(claim.checkpoint_root, t2.merkle_root(leaves))
        self.assertEqual(leaves[0], t2.INITIAL_LEAF)
        self.assertEqual(t2.INITIAL_LEAF, t2.checkpoint_leaf(0, t2.EMPTY, t2.EMPTY))
        bad_first = [t2.checkpoint_leaf(0, t2.state_root([0] * nl.n_latch), t2.EMPTY)] + leaves[1:]
        for args, why in (((h, T, K_, ir, bad_first), "initial"), ((h, T, K_, ir, leaves[:-1]), "number"),
                          ((t2.sha256(h), T, K_, ir, leaves), "stored"), ((h, 0, K_, ir, leaves), "at least")):
            with self.assertRaises(bisect_sim.ClaimRejected) as cm:
                ref.accept_claim(*args)
            self.assertIn(why, str(cm.exception))
        partial = t2.LargeStore([nh2])
        lv = t2.chunk_levels(data)
        partial.beginLarge(nh2, len(data), lv[-1][0])
        with self.assertRaises(bisect_sim.ClaimRejected):
            bisect_sim.Referee(partial, budget).accept_claim(h, T, K_, ir, leaves)

    def test_level2_size_fixed_by_referee(self):
        """A party whose interval tree has another size than t_hi - t_lo + 1 cannot open its leaves."""
        ref, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("message")
        honest = bisect_sim.Party("honest", nl, inputs, K, budget)

        class Padded(bisect_sim.Party):
            def commit_interval(self, t_lo, t_hi):      # one extra leaf: a tree of size n + 1
                leaves = [t2.checkpoint_leaf(*self.leaf(t)) for t in range(t_lo, t_hi + 1)]
                self._iv = (t_lo, t2.merkle_levels(leaves + [leaves[-1]]))
                return self._iv[1][-1][0]

        liar = Padded("cheater_padded", nl, inputs, K, budget, {"kind": "checkpoint", "t": 24})
        r = bisect_sim.run_one(ref, nh2, liar, honest)
        self.assertTrue(r["winner"].startswith("honest"))
        self.assertEqual(r["reason"], "bad Merkle proof in a reveal")

    def test_inputs_commitment(self):
        ins = [[1, 0, 1], [0, 0, 0], [1, 1, 1]]
        self.assertEqual(t2.input_leaf(1, ins[1]), t2.sha256(b"\x0a" + t2.u64(1) + t2.sha256(b"\x00")))
        root = t2.input_root(ins)
        lv = t2.input_levels(ins)
        for t, x in enumerate(ins):
            self.assertTrue(t2.merkle_verify(root, 3, t, t2.input_leaf(t, x), t2.merkle_proof(lv, t)))
        self.assertEqual(t2.unpack_inputs(3, b"\x05"), [1, 0, 1])
        for packed in (b"\x0d", b"\x05\x00", b""):
            with self.assertRaises(t2.Invalid):
                t2.unpack_inputs(3, packed)

    def test_checkpoint_times(self):
        self.assertEqual(t2.checkpoint_times(40, 8), [0, 8, 16, 24, 32, 40])
        self.assertEqual(t2.checkpoint_times(6, 4), [0, 4, 6])


class TestClaimRules(unittest.TestCase):
    """Spec 7.1: several challengers, bounds on T and K, the two input cases."""

    @classmethod
    def setUpClass(cls):
        cls.multi = bisect_sim.multi_challenger_scenarios()

    def test_sock_puppet_does_not_finalise(self):
        for sock in ("index0", "stall"):
            r = self.multi[f"message/sock_{sock}_then_honest"]
            self.assertEqual(r["sock"]["winner"], "cheater_claimant")     # the sock puppet loses its dispute
            self.assertEqual(r["statusAfterSock"], "open")                 # ... which does not accept the claim
            self.assertEqual(r["honest"]["winner"], "honest_challenger")   # the honest challenger still can
            self.assertEqual(r["status"], "rejected")
            self.assertEqual(r["credit"], {"cheater_claimant": 1, "honest_challenger": 2})

    def test_two_honest_challengers(self):
        r = self.multi["message/two_honest_challengers"]
        self.assertEqual(r["A"]["winner"], "honest_A")
        self.assertIsNone(r["B"]["winner"])
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(r["credit"], {"honest_A": 2, "honest_B": 1})

    def test_honest_claimant_two_cheaters(self):
        r = self.multi["message/honest_claimant_two_cheaters"]
        self.assertEqual((r["A"]["winner"], r["B"]["winner"]), ("honest_claimant", "honest_claimant"))
        self.assertEqual(r["statusBeforeWindow"], "open")
        self.assertEqual(r["status"], "accepted")
        self.assertEqual(r["credit"], {"honest_claimant": 3})

    def test_no_challenge_after_rejection_or_window(self):
        ref, nh2, data, nl, inputs, K, budget, cheats = bisect_sim.setup_game("message")
        g = bisect_sim.ClaimGame(ref, bisect_sim.Party("liar", nl, inputs, K, budget, cheats["c_rom"]), nh2)
        g.challenge(bisect_sim.Party("honest", nl, inputs, K, budget))
        with self.assertRaises(bisect_sim.ClaimRejected):
            g.challenge(bisect_sim.Party("late", nl, inputs, K, budget))
        g = bisect_sim.ClaimGame(ref, bisect_sim.Party("honest_cl", nl, inputs, K, budget), nh2)
        self.assertEqual(g.end_window(), "accepted")
        with self.assertRaises(bisect_sim.ClaimRejected):
            g.challenge(bisect_sim.Party("late", nl, inputs, K, budget))

    def test_bounds_on_T_and_K(self):
        _, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("message")
        store, _ = t2.oracle_for(data)
        h, T, K_, ir, leaves = bisect_sim.Party("p", nl, inputs, K, budget).claim_data(nh2)
        bisect_sim.Referee(store, budget, max_K=8, max_T=40).accept_claim(h, T, K_, ir, leaves)
        for kw in ({"max_K": 4, "max_T": 40}, {"max_K": 8, "max_T": 39}):
            with self.assertRaises(bisect_sim.ClaimRejected) as cm:
                bisect_sim.Referee(store, budget, **kw).accept_claim(h, T, K_, ir, leaves)
            self.assertIn("bounds", str(cm.exception))

    def test_input_case_a_rejects_inconsistent_inputs(self):
        """The second review's input-inconsistency reproduction: inputRoot commits Y, the published inputs are
        X (one bit differs). In case (a) the referee computes inputRoot from X and refuses the claim."""
        _, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("large")
        store, _ = t2.oracle_for(data)
        X = [list(x) for x in inputs]
        Y = [list(x) for x in X]
        Y[1][0] ^= 1
        claimant = bisect_sim.Party("claimant", nl, Y, K, budget)
        h, T, K_, ir, leaves = claimant.claim_data(nh2)
        ref = bisect_sim.Referee(store, budget, input_case="a")
        with self.assertRaises(bisect_sim.ClaimRejected) as cm:
            ref.accept_claim(h, T, K_, ir, leaves, input_data=[t2.pack_bits(x) for x in X])
        self.assertIn("inputRoot", str(cm.exception))
        claim = ref.accept_claim(h, T, K_, ir, leaves, input_data=[t2.pack_bits(x) for x in Y])
        self.assertEqual(claim.input_root, t2.input_root(Y))

    def test_input_case_b_provider_rules(self):
        _, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("large")
        store, _ = t2.oracle_for(data)
        p = bisect_sim.Party("claimant", nl, inputs, K, budget)
        h, T, K_, ir, leaves = p.claim_data(nh2)
        packed = [t2.pack_bits(x) for x in inputs]
        ref = bisect_sim.Referee(store, budget, input_case="b", provider="provider")
        with self.assertRaises(bisect_sim.ClaimRejected):
            ref.publish_inputs("claimant", h, T, ir, packed)          # only the provider publishes
        with self.assertRaises(bisect_sim.ClaimRejected):
            ref.accept_claim(h, T, K_, ir, leaves, claimant="claimant")   # not published yet
        with self.assertRaises(bisect_sim.ClaimRejected):
            ref.publish_inputs("provider", h, T, ir, packed[:-1])     # wrong length
        ref.publish_inputs("provider", h, T, ir, packed)
        with self.assertRaises(bisect_sim.ClaimRejected) as cm:
            ref.accept_claim(h, T, K_, ir, leaves, claimant="provider")
        self.assertIn("provider cannot be the claimant", str(cm.exception))
        ref.accept_claim(h, T, K_, ir, leaves, claimant="claimant")

    def test_input_case_b_residual_risk(self):
        """What case (b) still leaves to the application (spec 7.1, Security Considerations): a provider that
        publishes X while inputRoot commits Y. A checker that computes on X and challenges loses at reveal
        check 1, so a challenger must check the published inputs against inputRoot before challenging."""
        _, nh2, data, nl, inputs, K, budget, _ = bisect_sim.setup_game("large")
        store, _ = t2.oracle_for(data)
        X = [list(x) for x in inputs]
        Y = [list(x) for x in X]
        Y[1][0] ^= 1
        claimant = bisect_sim.Party("claimant", nl, Y, K, budget)
        checker = bisect_sim.Party("checker_on_X", nl, X, K, budget)
        ref = bisect_sim.Referee(store, budget)
        h, T, K_, ir, leaves = claimant.claim_data(nh2)
        self.assertNotEqual(ir, t2.input_root(X))                       # detectable off chain
        r = ref.dispute(ref.accept_claim(h, T, K_, ir, leaves), leaves, claimant, checker).as_json()
        self.assertEqual(r["winner"], "claimant")
        self.assertIn("seg_0", r["reason"])


if __name__ == "__main__":
    unittest.main()
