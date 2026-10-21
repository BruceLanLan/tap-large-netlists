"""Tests of the verifiable-inference demo.  Run:  python3 test_inference.py   (or python3 -m unittest -v test_inference)

Standard library only (the committed model/*.json, rtl/mlp_digits.v and build/mlp_digits.tapnet2 are the inputs).
ConverterTests needs yosys on PATH and is skipped without it.
"""
from __future__ import annotations

import os
import shutil
import unittest

from _paths import t2
import mlp_dispute as D
import mlp_model as M
import mlp_verilog as VG

HERE = os.path.dirname(os.path.abspath(__file__))
N_COMPARE = 50


def run_netlist(nl: t2.Netlist, pixels):
    """(outputs per beat, final class) of the netlist on one digit."""
    ins = M.inputs_for(pixels)
    st, mem, outs_all = [0] * nl.n_latch, {}, []
    for t in range(M.T_BEATS):
        _, outs, st, mem, _ = t2.evaluate_full(nl, st, mem, ins[t])
        outs_all.append(outs)
    return outs_all, sum(b << k for k, b in enumerate(outs_all[-1][:4]))


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = M.load_weights()
        cls.d = M.load_test_digits()
        cls.px = [M.pixels_from_hex(s) for s in cls.d["pixels"]]

    def test_weights_file(self):
        w = self.w
        self.assertEqual((w["nIn"], w["nHidden"], w["nOut"], w["lanes"]), (64, 16, 10, 16))
        self.assertEqual((len(w["w1"]), len(w["w1"][0]), len(w["w2"]), len(w["w2"][0])), (16, 64, 10, 16))
        self.assertTrue(all(-127 <= x <= 127 for row in w["w1"] + w["w2"] for x in row))
        self.assertTrue(all(abs(b) < 1 << 19 for b in w["b1"] + w["b2"]))
        self.assertEqual(w["accBits"], M.ACC_BITS)
        self.assertLess(64 * 15 * 127 + max(map(abs, w["b1"])), 1 << 19)   # no 20-bit overflow is possible
        self.assertLess(16 * 15 * 127 + max(map(abs, w["b2"])), 1 << 19)

    def test_model_reproduces_training_predictions(self):
        pred = [M.predict(p, self.w) for p in self.px]
        self.assertEqual(pred, self.d["intPrediction"])
        acc = sum(p == y for p, y in zip(pred, self.d["labels"])) / len(pred)
        self.assertAlmostEqual(acc, self.w["training"]["intTestAccuracy"], places=9)
        self.assertGreater(acc, 0.9)
        self.assertEqual(len(pred), self.w["training"]["split"]["nTest"])

    def test_schedule_matches_arithmetic(self):
        M.check_schedule_matches_arithmetic(self.w, self.px)
        for p in self.px[:20]:
            self.assertEqual(M.circuit_class(p, self.w), M.predict(p, self.w))

    def test_inputs_are_canonical(self):
        ins = M.inputs_for(self.px[0])
        self.assertEqual(len(ins), M.T_BEATS)
        self.assertTrue(all(x == [0, 0, 0, 0] for x in ins[64:]))
        self.assertEqual([sum(b << k for k, b in enumerate(x)) for x in ins[:64]], self.px[0])

    def test_verilog_is_generated_from_weights(self):
        with open(os.path.join(HERE, "rtl", "mlp_digits.v")) as f:
            self.assertEqual(f.read(), VG.generate(self.w))


class NetlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nl = D.load_netlist()
        cls.w = M.load_weights()
        cls.d = M.load_test_digits()
        cls.px = [M.pixels_from_hex(s) for s in cls.d["pixels"]]

    def test_file_valid_and_shape(self):
        nl = self.nl
        m = nl.metrics()
        self.assertEqual((m["nIn"], m["nOut"]), (4, 5))
        self.assertFalse(nl.has_mem)
        self.assertEqual(m["romRecords"], 2)
        roms = [r.raw for r in nl.records if r.raw.op == t2.OP_ROM]
        self.assertEqual(sorted(r.a for r in roms), [4, 6])          # w2 (16 rows) and w1 (64 rows)
        self.assertEqual(m["latch"], 421)                             # 417 register bits + 4 output copies
        self.assertEqual(t2.n_chunks(len(nl.data)), 6)
        self.assertGreater(len(nl.data), 49152)                       # larger than one TAP-02-sized netlist
        bounds = t2.segment_boundaries(nl, t2.DEFAULT_BUDGET)
        self.assertEqual(len(bounds) - 1, 3)
        read = t2._file_reader(nl.data)
        for o, s, l in bounds[:-1]:
            seg = t2.canonical_segment(read, len(nl.data), o, s, l, t2.DEFAULT_BUDGET)
            c = seg.cost.as_json()
            self.assertLessEqual(c["gates"], 6000)
            self.assertLessEqual(c["extWords"], 256)
            self.assertLessEqual(c["chunks"], 16)

    def test_netlist_matches_model_on_50_digits(self):
        match = 0
        for i in range(N_COMPARE):
            regs = M.trace(self.px[i], self.w)
            outs_all, cls = run_netlist(self.nl, self.px[i])
            self.assertEqual(outs_all, [M.outputs_of(regs[t]) for t in range(M.T_BEATS)], f"digit {i}")
            self.assertEqual(cls, self.d["intPrediction"][i], f"digit {i}")
            match += 1
        self.assertEqual(match, N_COMPARE)

    def test_registers_through_latch_map(self):
        """The accumulators after L1, the hidden activations and the class, read from the LATCH state through the
        mapping of mlp_dispute.latch_map, equal the integer model's values."""
        runs = []
        for i in D.signature_digits(self.d, 0):
            runs.append((D.honest_states(self.nl, M.inputs_for(self.px[i])), M.trace(self.px[i], self.w)))
        lm = D.latch_map(self.nl, runs)
        varying = {name for name in runs[0][1][0].bits()
                   if len({r.bits()[name][0] for _, regs in runs for r in regs}) > 1}
        for b in range(4):      # every class bit that changes in these runs is found (a constant bit cannot be)
            if f"cls[{b}]" in varying:
                self.assertIn(f"cls[{b}]", lm)
        self.assertIn("cls[0]", lm)
        for states, regs in runs:
            for name, ords in lm.items():
                for t in (M.BEAT_ACT, M.BEAT_L2, M.BEAT_ARGMAX, M.BEAT_DONE):
                    for l in ords:
                        self.assertEqual(states[t][l], regs[t].bits()[name][0], f"{name} at beat {t}")
        mapped = sum(len(v) for v in lm.values())
        self.assertGreater(mapped, 200)


class DisputeTests(unittest.TestCase):
    def test_dispute_scenarios(self):
        s, res = D.run_all(verbose=False)
        D.check(res)
        a = res[sorted(res)[0]]
        self.assertEqual(a["claimedClass"], s["label"])
        for name, r in res.items():
            if r["verifyCalls"]:          # checkpoint 1 + beats 3-4 + tuples 1 + list 2-3 + verify 1
                self.assertIn(r["totalRounds"], (9, 10))
        finals = {r["final"] for r in res.values()}
        for j in range(3):                      # every segment of the beat is reached by some dispute
            self.assertIn(f"verify_segment(seg_{j} -> seg_{j + 1})", finals)

    def test_open_class_rejects_other_class(self):
        s = D.setup()
        nl = s["nl"]
        store, nh2 = t2.oracle_for(nl.data)
        honest = D.parties(s)["honest"]
        published = honest.claim_data(nh2)[4]
        T = honest.T
        _, outs, _ = honest._eval(T - 1)
        _, sr, _ = honest.leaf(T)
        self.assertEqual(D.open_class(published, T, sr, honest.io[T - 1], outs), (s["honest_cls"], 1))
        other = list(outs)
        other[0] ^= 1
        with self.assertRaises(ValueError):
            D.open_class(published, T, sr, honest.io[T - 1], other)

    def test_honest_witnesses_verify(self):
        s = D.setup()
        nl, inputs = s["nl"], s["inputs"]
        store, nh2 = t2.oracle_for(nl.data)
        state, mem = [0] * nl.n_latch, {}
        for t in range(45):
            _, _, state, mem, _ = t2.evaluate_full(nl, state, mem, inputs[t])
        lroot = t2.state_root(state)
        V, _, _, _, ev = t2.evaluate_full(nl, state, mem, inputs[45])
        tuples = t2.segment_tuples(nl, V, t2.DEFAULT_BUDGET, mem, ev)
        self.assertEqual(len(tuples), 4)
        for j in range(1, 4):
            w = t2.build_seg_witness(nl, V, state, tuples[j - 1], tuples[j].byte_offset, lroot, mem, ev)
            enc = t2.serialize_seg_witness(w)
            self.assertTrue(t2.verify_segment(store, nh2, lroot, tuples[j - 1], tuples[j], enc, t2.DEFAULT_BUDGET,
                                              t2.ZERO_MEM))
            self.assertLessEqual(len(enc), 84208)


@unittest.skipUnless(shutil.which("yosys"), "needs yosys on PATH")
class ConverterTests(unittest.TestCase):
    """A fresh conversion of rtl/mlp_digits.v behaves like the committed file (bytes may differ between Yosys versions)."""

    def test_rebuild_behaves_like_the_model(self):
        import mlp_build
        res = mlp_build.build(best=False)
        nl = t2.decode(res.data)
        self.assertEqual((nl.n_in, nl.n_out, nl.rom_records, nl.has_mem), (4, 5, 2, False))
        w = M.load_weights()
        d = M.load_test_digits()
        for i in range(3):
            px = M.pixels_from_hex(d["pixels"][i])
            regs = M.trace(px, w)
            outs_all, cls = run_netlist(nl, px)
            self.assertEqual(outs_all, [M.outputs_of(regs[t]) for t in range(M.T_BEATS)])
            self.assertEqual(cls, d["intPrediction"][i])


if __name__ == "__main__":
    unittest.main(verbosity=2)
