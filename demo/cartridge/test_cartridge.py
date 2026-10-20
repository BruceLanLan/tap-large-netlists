"""Tests for the TAPBOY-8 cartridge demo.  Run:  python3 -m unittest -v test_cartridge   (or pytest)

The gate-level netlist (stepped with assets/tap-draft-large-netlists/tapnet2.py) must match the instruction-level emulator beat by beat:
the pins (OUT, VALID, HALTED) and the whole machine state (PC, ACC, B, HALTED, all 256 RAM bytes)."""
from __future__ import annotations

import itertools
import random
import unittest

from _ref import t2
from assembler import AsmError, assemble
from cpu import bits, build_cartridge, from_bits, pins
from emulator import Machine, printed, run_until_halt
from isa import HALT_WORD, encode
from netlist_builder import NetBuilder
from player import good_player, lazy_player, load, play, random_player


def lockstep(test: unittest.TestCase, rom, buttons, extra_beats: int = 0):
    """Steps netlist and emulator side by side; returns the pins of every beat."""
    data, info = build_cartridge(rom)
    nl = t2.decode(data)
    m = Machine(rom)
    state, mem = [0] * nl.n_latch, {}
    out_pins = []
    for t, btn in enumerate(list(buttons) + [0] * extra_beats):
        _, outs, state, mem, _ = t2.evaluate_full(nl, state, mem, bits(btn))
        got = pins(outs)
        want = m.step(btn)
        test.assertEqual(got, want, f"pins differ at beat {t}")
        test.assertEqual(info.decode_state(state, mem), m.snapshot(), f"state differs after beat {t}")
        out_pins.append(got)
    return out_pins


class BuilderTests(unittest.TestCase):
    def _run(self, b: NetBuilder, outs, inputs):
        nl = t2.decode(b.finish(outs))
        return t2.step(nl, [0] * nl.n_latch, inputs)[0]

    def test_gates_exhaustive(self):
        b = NetBuilder(3)
        x, y, s = b.inputs()
        outs = [b.not_(x), b.and_(x, y), b.or_(x, y), b.xor(x, y), b.xnor(x, y), b.nor(x, y), b.mux(s, x, y)]
        nl = t2.decode(b.finish(outs))
        for vx, vy, vs in itertools.product((0, 1), repeat=3):
            got = t2.step(nl, [], [vx, vy, vs])[0]
            self.assertEqual(got, [1 - vx, vx & vy, vx | vy, vx ^ vy, 1 - (vx ^ vy), 1 - (vx | vy),
                                   vy if vs else vx])

    def test_adder_and_compare_exhaustive(self):
        b = NetBuilder(8)
        i = b.inputs()
        a, c = i[:4], i[4:]
        s, co = b.add(a, c)
        outs = s + [co] + b.inc(a) + [b.eq(a, c), b.is_zero(a)]
        nl = t2.decode(b.finish(outs))
        for va, vc in itertools.product(range(16), repeat=2):
            got = t2.step(nl, [], bits(va, 4) + bits(vc, 4))[0]
            self.assertEqual(from_bits(got[:5]), va + vc)
            self.assertEqual(from_bits(got[5:9]), (va + 1) & 15)
            self.assertEqual(got[9:], [int(va == vc), int(va == 0)])

    def test_decoder_and_mux_tree(self):
        b = NetBuilder(5)
        i = b.inputs()
        lines = b.decoder(i[:3], enable=i[3])
        consts = [b.const(v, 3) for v in (5, 3, 7, 0, 1, 6, 2, 4)]
        outs = lines + b.mux_tree(i[:3], consts)
        nl = t2.decode(b.finish(outs))
        for sel, en in itertools.product(range(8), (0, 1)):
            got = t2.step(nl, [], bits(sel, 3) + [en, 0])[0]
            self.assertEqual(got[:8], [int(en and k == sel) for k in range(8)])
            self.assertEqual(from_bits(got[8:]), (5, 3, 7, 0, 1, 6, 2, 4)[sel])

    def test_register_and_rom(self):
        b = NetBuilder(1)
        (en,) = b.inputs()
        q = b.register(3)                    # a 3-bit counter addressing a ROM
        rom = b.rom(q, 8, [10 * k for k in range(8)])
        b.set_reg(q, b.mux_bus(en, q, b.inc(q)))
        nl = t2.decode(b.finish(rom))
        st, seen = [0, 0, 0], []
        for t in range(10):
            outs, st = t2.step(nl, st, [1])
            seen.append(from_bits(outs))
        self.assertEqual(seen, [10 * (t % 8) for t in range(10)])

    def test_outputs_are_last_signals(self):
        b = NetBuilder(2)
        x, y = b.inputs()
        n = b.not_(x)
        nl = t2.decode(b.finish([n, x, b.and_(x, y)]))    # outputs that already exist earlier
        self.assertEqual(t2.step(nl, [], [1, 1])[0], [0, 1, 1])


class AssemblerTests(unittest.TestCase):
    def test_forms(self):
        words, names, _ = assemble("""
            .equ V 0x10
        top: LD #'A'
            ADD [V]
            SUB [V+1+B]
            ST [V-1+B]
            LDB #-1
            JNZ top
            IN
            HALT
        """)
        self.assertEqual(words[:8], [encode("LD", 65), encode("ADD", 0x10, m=True),
                                     encode("SUB", 0x11, m=True, x=True), encode("ST", 0x0F, x=True),
                                     encode("LDB", 0xFF), encode("JNZ", 0), encode("IN"), HALT_WORD])
        self.assertEqual(words[8:], [HALT_WORD] * 248)
        self.assertEqual(names["top"], 0)

    def test_print_macro(self):
        words, _, _ = assemble('.print "a;b"\nHALT')
        self.assertEqual(words[:7], [encode("LD", 97), encode("OUT"), encode("LD", 59), encode("OUT"),
                                     encode("LD", 98), encode("OUT"), HALT_WORD])

    def test_errors(self):
        for src in ("LD", "ST #1", "FOO", "JMP nowhere", "LD #300", "OUT 1", "x:\nx:"):
            with self.assertRaises(AsmError, msg=src):
                assemble(src)


class CartridgeTests(unittest.TestCase):
    def test_file_is_large_and_valid(self):
        words, _ = load("dodge")
        data, info = build_cartridge(words)
        met = t2.check_file(data)
        self.assertEqual(len(data), 5781)                     # one chunk (the LATCH-RAM build: 104,076 bytes, 7)
        self.assertEqual(met["nand"], info.gates["nand"])
        self.assertEqual(met["latch"], 25)                    # was 2,073 (2,048 RAM latches are now a bank)
        self.assertEqual((met["romRecords"], met["romBits"]), (1, 256 * 16))
        self.assertEqual((met["ramBanks"], met["ramBits"], met["ramReadPorts"], met["ramWritePorts"]),
                         (1, 2048, 1, 1))
        self.assertEqual((met["nIn"], met["nOut"]), (8, 10))
        self.assertEqual(t2.n_chunks(len(data)), 1)
        bounds = t2.segment_boundaries(data, t2.DEFAULT_BUDGET)
        self.assertEqual(bounds[-1], (len(data), met["signals"], 25))
        self.assertEqual(len(bounds) - 1, 1)                  # one canonical segment (was 3)

    def test_hello(self):
        rom, _ = load("hello")
        buttons, emu_pins = run_until_halt(rom)
        got = lockstep(self, rom, buttons, extra_beats=4)
        self.assertEqual(printed(got), b"Hello, tapnet/2!\n")
        self.assertEqual(got[-1][2], 1)

    def test_dodge_good_and_lazy_player(self):
        rom, names = load("dodge")
        for who, score in ((good_player(names), 34), (lazy_player, 22)):
            buttons = play(rom, names, who)
            got = lockstep(self, rom, buttons, extra_beats=2)
            self.assertEqual(got[-1], (score, 0, 1))
            self.assertEqual(printed(got)[-1], score)

    def test_dodge_random_buttons(self):
        """Random bytes on every beat (also on beats where the CPU is not at IN)."""
        rom, names = load("dodge")
        for seed in (1, 2, 3):
            buttons = play(rom, names, random_player(seed))
            got = lockstep(self, rom, buttons, extra_beats=1)
            self.assertEqual(got[-1][2], 1)

    def test_random_programs(self):
        """Random instruction words (every opcode, M/X modes, wild jumps) with random buttons."""
        rng = random.Random(721)
        for _ in range(3):
            rom = [rng.randrange(1 << 16) for _ in range(256)]
            rom = [w if (w >> 12) != 0xF or rng.random() < 0.05 else w ^ 0x1000 for w in rom]
            lockstep(self, rom, [rng.randrange(256) for _ in range(250)])


class DisputeTests(unittest.TestCase):
    def test_dispute_demo(self):
        import dispute_demo
        s, results = dispute_demo.run_all(verbose=False)
        dispute_demo.check(results)
        for k in ("B inflated score (RAM write) vs honest checker", "E inflated score (RAM read) vs honest checker"):
            self.assertGreater(results[k]["claimedPins"][0], s["final_score"])

    def test_score_opening_rejects_other_score(self):
        import bisect_sim as bs
        import dispute_demo
        s = dispute_demo.setup()
        p = bs.Party("p", s["nl"], s["inputs"], dispute_demo.K, t2.DEFAULT_BUDGET)
        pub = p.cp_leaves
        T = p.T
        outs = p._eval(T - 1)[1]
        self.assertEqual(dispute_demo.open_final_score(pub, T, p.sroots[T], p.io[T - 1], outs)[0], 34)
        fake = bits(98) + outs[8:]
        with self.assertRaises(ValueError):
            dispute_demo.open_final_score(pub, T, p.sroots[T], p.io[T - 1], fake)


class LegacyComparisonTests(unittest.TestCase):
    """The LATCH-RAM build (build_cartridge(rom, ram="latch"); build/legacy/*.tapnet2) and the RAM-port build give
    identical pins on every beat, and the LATCH RAM holds the same 256 bytes as bank 0."""

    def test_legacy_files_reproducible(self):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        for prog in ("hello", "dodge"):
            rom, _ = load(prog)
            with open(os.path.join(here, "build", "legacy", prog + ".tapnet2"), "rb") as f:
                self.assertEqual(f.read(), build_cartridge(rom, ram="latch")[0])
            with open(os.path.join(here, "build", prog + ".tapnet2"), "rb") as f:
                self.assertEqual(f.read(), build_cartridge(rom)[0])

    def test_identical_pins(self):
        runs = []
        hello, _ = load("hello")
        runs.append((hello, run_until_halt(hello)[0] + [0] * 4))
        dodge, names = load("dodge")
        for who in (good_player(names), lazy_player, random_player(9)):
            runs.append((dodge, play(dodge, names, who) + [0] * 2))
        for rom, buttons in runs:
            old = t2.decode(build_cartridge(rom, ram="latch")[0])
            new = t2.decode(build_cartridge(rom)[0])
            self.assertEqual((len(old.data), old.n_latch, len(new.data), new.n_latch), (104076, 2073, 5781, 25))
            so, sn, mem = [0] * old.n_latch, [0] * new.n_latch, {}
            for t, btn in enumerate(buttons):
                oo, so = t2.step(old, so, bits(btn))
                _, on, sn, mem, _ = t2.evaluate_full(new, sn, mem, bits(btn))
                self.assertEqual(oo, on, f"pins differ at beat {t}")
            self.assertEqual([so[o] for o in range(25)], sn)           # PC, ACC, B, HALTED latches
            self.assertEqual([sum(so[25 + 8 * k + i] << i for i in range(8)) for k in range(256)], mem[0])


if __name__ == "__main__":
    unittest.main()
