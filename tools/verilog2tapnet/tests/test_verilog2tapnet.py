"""Tests for verilog2tapnet: every design converts, passes the reference validity checks, and matches Icarus
Verilog beat by beat (outputs, and memory contents for RAM banks); RAM and ROM read ports; output tail;
rejection of unsupported constructs.

    python3 -m unittest discover -s tests -v       (needs yosys and iverilog on PATH)

Gate counts depend on the Yosys/ABC version, so the tests assert behaviour, record kinds and bounds, not
exact sizes (the README gives the sizes of one local run). test_same_structure_same_records needs Yosys >=
verilog2tapnet.MIN_YOSYS and is skipped (with the versions in the message) under an older one, whose ABC
maps structurally identical designs to equivalent but not identical records; the beat-by-beat checks of
those designs run under every version.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import validate as V  # noqa: E402
import verilog2tapnet as v2t  # noqa: E402
from verilog2tapnet import tapnet2  # noqa: E402


def spec(name: str):
    return next(d for d in V.DESIGNS if d[0] == name)


class DesignSuite(unittest.TestCase):
    cache: dict = {}

    @classmethod
    def res(cls, name: str, **extra) -> v2t.Result:
        key = (name, tuple(sorted(extra.items())))
        if key not in cls.cache:
            if name in V.LATCH_BUILDS:
                srcs, top, opts = V.LATCH_BUILDS[name]
            else:
                _, srcs, top, opts = spec(name)
            cls.cache[key] = v2t.convert(srcs, top, **opts, **extra)
        return cls.cache[key]

    def test_all_designs_match_iverilog(self):
        for name, srcs, top, opts in V.DESIGNS:
            with self.subTest(design=name):
                r = self.res(name)
                v2t.check_file(r.data)
                self.assertEqual(r.data[6], r.version)
                val = V.validate(srcs, r, beats=300, seed=7)
                self.assertGreater(val["beats"], 0)
                if r.version == 2 and not r.data[7] & tapnet2.FLAG_MEM:
                    self.assertTrue(val.get("expansion_checked"))

    def test_second_seed_longer_runs(self):
        for name in ("counter4", "lfsr8", "traffic", "acc_cpu", "acc_cpu.v2rom", "rom_wave.v2rom", "rom_dual.v2rom",
                     "regfile16x8.v2ram", "ram_bytes.v2ram", "stack_cpu.v2ram", "acc_cpu.v2ram", "acc_ram.v2ram"):
            with self.subTest(design=name):
                V.validate(spec(name)[1], self.res(name), beats=1000, seed=99)

    def test_latch_builds_match_iverilog(self):
        for name, (srcs, top, opts) in V.LATCH_BUILDS.items():
            with self.subTest(design=name):
                r = self.res(name)
                self.assertFalse(r.data[7] & tapnet2.FLAG_MEM)
                V.validate(srcs, r, beats=300, seed=5)

    def test_memory_contents_compared(self):
        # every RAM design dumps its whole memory from the testbench on every beat
        for name, words in (("regfile16x8.v2ram", 16), ("ram_bytes.v2ram", 4), ("stack_cpu.v2ram", 16),
                            ("acc_cpu.v2ram", 4), ("acc_ram.v2ram", 16)):
            with self.subTest(design=name):
                val = V.validate(spec(name)[1], self.res(name), beats=200, seed=3)
                mc = val["memory_checked"]
                self.assertEqual(len(mc), 1)
                self.assertEqual(mc[0]["beats"], 200)
                self.assertEqual(mc[0]["bits"], 8 * words if name != "ram_bytes.v2ram" else 16)

    def test_memory_check_catches_a_flipped_bit(self):
        # a memory bit flipped in the tapnet run is reported at the next beat, before any read sees it
        name = "stack_cpu.v2ram"
        orig = tapnet2.evaluate_full
        calls = [0]

        def faulty(nl, state, mem, inputs, *a, **k):
            calls[0] += 1
            return orig(nl, state, mem, inputs, flip_mem={(0, 9, 3)} if calls[0] == 6 else frozenset())
        tapnet2.evaluate_full = faulty
        try:
            with self.assertRaises(V.Mismatch) as cm:
                V.validate(spec(name)[1], self.res(name), beats=100, seed=1)
        finally:
            tapnet2.evaluate_full = orig
        self.assertIn("beat 6: memory stk[9] bit 3", str(cm.exception))

    def test_regfile_is_ram_ports_only(self):
        r = self.res("regfile16x8.v2ram")
        i = r.info
        self.assertEqual(r.data[7], tapnet2.FLAG_MEM)
        self.assertEqual((i["ramBanks"], i["ramBits"], i["ramReadPorts"], i["ramWritePorts"]), (1, 128, 2, 1))
        self.assertEqual((i["nand"], i["latch"]), (0, 0))
        self.assertEqual(i["tail"]["port_move"], 2)          # both read ports are the outputs: no buffers
        self.assertEqual(i["bytes"], 16 + 2 * (5 + 3 * 4) + (8 + 3 * 4 + 3 * 8))

    def test_byte_enables_split_banks_and_keep_priority(self):
        i = self.res("ram_bytes.v2ram").info
        self.assertEqual(i["ramBits"], 16)
        # Yosys 0.69: two banks (bits 1:0 with both write ports, bits 3:2 with port 0 only), three write ports
        self.assertGreaterEqual(i["ramBanks"], 2)
        self.assertGreaterEqual(i["ramWritePorts"], 3)

    def test_stack_cpu_uses_rom_port_and_two_write_ports(self):
        i = self.res("stack_cpu.v2ram").info
        self.assertEqual(i["romRecords"], 1)
        self.assertGreaterEqual(i["romPorts"], 1)             # prog[pc] and prog[pc + 1]
        self.assertEqual(i["ramBanks"], 1)
        self.assertGreaterEqual(i["ramWritePorts"], 2)        # SWAP writes two entries (2 with Yosys 0.69)
        self.assertGreaterEqual(i["ramReadPorts"], 2)
        # the run must wrap the stack around, so that all 16 entries are written
        rows = V.simulate_tapnet(self.res("stack_cpu.v2ram").data, V.make_vectors(8, True, 1000, 7))
        self.assertEqual({sum(r[8 + b] << b for b in range(4)) for r in rows}, set(range(16)))
        self.assertTrue(any(r[16] for r in rows))

    def test_rom_read_port(self):
        r = self.res("rom_dual.v2rom")
        self.assertEqual((r.info["romRecords"], r.info["romPorts"], r.info["romBits"]), (1, 1, 512))
        self.assertFalse(r.data[7] & tapnet2.FLAG_MEM)
        nl = tapnet2.decode(r.data)
        ops = [rec.raw.op for rec in nl.records]
        self.assertLess(ops.index(tapnet2.OP_ROM), ops.index(tapnet2.OP_ROMP))

    def test_ram_smaller_than_latch_memory(self):
        for a, b in V.RAM_COMPARE + V.ROM_COMPARE:
            with self.subTest(pair=(a, b)):
                ra, rb = self.res(a), self.res(b)
                self.assertLess(ra.info["bytes"], rb.info["bytes"])
                self.assertLessEqual(ra.info["latch"], rb.info["latch"])

    def test_ram_with_initial_contents_stays_flip_flops(self):
        r = self.res("ram_init.v2ram")
        self.assertIn("nonzero initial contents", r.info["memory_decisions"]["m"])
        self.assertFalse(r.data[7] & tapnet2.FLAG_MEM)

    def test_xor_is_small(self):
        r = self.res("xor2")
        self.assertEqual(r.info["latch"], 0)
        self.assertLessEqual(r.info["nand"], 5)              # 4 with Yosys 0.69 (the optimum)

    def test_same_structure_same_records(self):
        # Renaming, swapped operands and port order (with --ports) give the same NAND records, possibly
        # in another order. This is a property of the Yosys/ABC release, not only of the tool: Yosys 0.33
        # (Ubuntu 24.04) maps adder8_swapped to other, equivalent records, so the check needs the
        # documented minimum (an unknown version runs the check, so a probe failure cannot hide a change).
        ver = v2t.yosys_version()
        if ver is not None and ver < v2t.MIN_YOSYS:
            self.skipTest(f"Yosys {ver[0]}.{ver[1]} < {v2t.MIN_YOSYS[0]}.{v2t.MIN_YOSYS[1]} (documented "
                          "minimum): older ABC releases map structurally identical designs to equivalent "
                          "but not identical NAND records; behaviour is still checked beat by beat")
        for a, b in V.MUST_MATCH:
            with self.subTest(pair=(a, b)):
                self.assertEqual(V.records_multiset(self.res(a).data), V.records_multiset(self.res(b).data))

    def test_tapnet1_files_are_checked_as_tapnet1(self):
        r = self.res("adder8")
        self.assertEqual(r.data[6], 1)
        tapnet2.decode(r.data, (1,))
        with self.assertRaises(tapnet2.Invalid):
            tapnet2.decode(r.data, (2,))

    def test_cpu_coverage(self):
        # the validated runs (seeds 7 and 99) must reach OUT and HALT, so the sticky halt logic is checked
        for name in ("acc_cpu", "acc_cpu.v2rom", "acc_cpu.v2ram"):
            for seed in (7, 99):
                rows = V.simulate_tapnet(self.res(name).data, V.make_vectors(8, True, 1000, seed))
                self.assertTrue(any(r[12] for r in rows), "valid never 1")
                self.assertTrue(any(r[13] for r in rows), "halted never 1")
        rows, mems = V.simulate_tapnet(self.res("acc_ram.v2ram").data, V.make_vectors(8, True, 1000, 2), True)
        self.assertTrue(any(r[12] for r in rows) and any(r[13] for r in rows))
        self.assertGreaterEqual(sum(1 for e in mems[-1].get(0, []) if e), 12)

    def test_lfsr_nonzero_init(self):
        # seed 0x01 must appear on the outputs in beat 0 although TAP-02 state starts at zero
        out = V.simulate_tapnet(self.res("lfsr8").data, [[]])[0]
        self.assertEqual(out, [1, 0, 0, 0, 0, 0, 0, 0])

    def test_output_tail(self):
        # register outputs: a second LATCH (default) or two inverters (--no-latch-copy), same behaviour
        a = self.res("counter4")
        b = self.res("counter4", latch_copy=False)
        self.assertEqual((a.info["tail"]["latch_copy"], a.info["tail"]["buf"]), (4, 0))
        self.assertEqual((b.info["tail"]["latch_copy"], b.info["tail"]["buf"]), (0, 4))
        self.assertEqual(a.info["latch"], b.info["latch"] + 4)
        self.assertGreaterEqual(b.info["nand"], a.info["nand"] + 4)     # + 8 with Yosys 0.69
        self.assertGreater(b.info["bytes"], a.info["bytes"])
        V.validate(spec("counter4")[1], b, beats=300)
        # a ROM whose outputs are the first output bits is moved to the end of the file whole
        self.assertEqual(self.res("rom_wave.v2rom").info["tail"]["port_move"], 1)

    def test_ports_option(self):
        with self.assertRaises(v2t.ConversionError):
            v2t.convert([V.D("adder8.v")], "adder8", ports=["a", "b"])
        r = v2t.convert([V.D("adder8.v")], "adder8", ports=["b", "a", "cin", "cout", "s"])
        self.assertEqual(r.info["inputs"][0], "b[0]")
        self.assertEqual(r.info["outputs"][0], "cout[0]")
        V.validate([V.D("adder8.v")], r, beats=100)

    def test_rom_and_ram_need_tapnet2(self):
        for kw in ({"rom": True}, {"ram": True}):
            with self.assertRaises(v2t.ConversionError):
                v2t.convert([V.D("rom_wave.v")], "rom_wave", version=1, **kw)

    def test_unoptimized_still_correct(self):
        for name in ("full_adder", "traffic", "stack_cpu.v2ram"):
            _, srcs, top, opts = spec(name)
            r = v2t.convert(srcs, top, opt=0, **opts)
            V.validate(srcs, r, beats=200)
            self.assertGreaterEqual(r.info["nand"], self.res(name).info["nand"])


class Unsupported(unittest.TestCase):
    CASES = [
        ("async_reset.v", "async_reset", "asynchronous reset", {}),
        ("negedge.v", "negedge_ff", "negative-edge", {}),
        ("transparent_latch.v", "transparent_latch", "transparent", {}),
        ("two_clocks.v", "two_clocks", "more than one clock", {}),
        ("gated_clock.v", "gated_clock", "internal net", {}),
        ("tristate.v", "tristate", "tri-state", {}),
        ("comb_loop.v", "comb_loop", "combinational loop", {}),
        ("ram_two_clocks.v", "ram_two_clocks", "more than one clock", V.V2MEM),
        ("ram_negedge.v", "ram_negedge", "negative-edge", V.V2MEM),
    ]

    def test_rejected_with_clear_errors(self):
        for f, top, msg, opts in self.CASES:
            with self.subTest(design=top):
                with self.assertRaises(v2t.ConversionError) as cm:
                    v2t.convert([os.path.join(ROOT, "designs", "unsupported", f)], top, **opts)
                self.assertIn(msg, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
