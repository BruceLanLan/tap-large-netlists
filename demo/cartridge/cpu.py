"""TAPBOY-8 as a gate-level tapnet/2 netlist: a single-cycle 8-bit accumulator CPU built from NAND and LATCH,
with its program in one ROM record (a = 8, w = 16) and 256 bytes of RAM made of LATCHes.

ISA: see isa.py. Pins: nIn = 8 (BTN[0..7]); nOut = 10 (OUT[0..7], VALID, HALTED).

    data, info = build_cartridge(rom_words)
    nl = t2.decode(data)
    outs, new_state = t2.step(nl, state, buttons_bits)
    info.decode_state(new_state)  ->  {"pc", "acc", "b", "halted", "ram"}

Latch order (= latch ordinals, = state bit order): PC[0..7], ACC[0..7], B[0..7], HALTED, RAM[0][0..7], ...,
RAM[255][0..7]. Signal order: constants, 8 inputs, the latches, the ROM's 16 outputs, logic, output buffers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from _ref import t2
from isa import OPS, ROM_WORDS
from netlist_builder import ONE, ZERO, NetBuilder

N_IN, N_OUT = 8, 10
RAM_BYTES = 256


@dataclass
class CartInfo:
    latch: Dict[str, List[int]]          # register name -> latch ordinals (LSB first)
    ram_latch: List[List[int]]           # RAM byte k -> latch ordinals of its bits
    signal: Dict[str, List[int]]         # named internal signals (LSB first)
    gates: dict = field(default_factory=dict)

    def decode_state(self, state: Sequence[int]) -> dict:
        def val(ords):
            return sum(state[o] << i for i, o in enumerate(ords))
        return {"pc": val(self.latch["pc"]), "acc": val(self.latch["acc"]), "b": val(self.latch["b"]),
                "halted": state[self.latch["halted"][0]], "ram": [val(r) for r in self.ram_latch]}


def bits(value: int, n: int = 8) -> List[int]:
    return [(value >> i) & 1 for i in range(n)]


def from_bits(bs: Sequence[int]) -> int:
    return sum(b << i for i, b in enumerate(bs))


def pins(outputs: Sequence[int]):
    """(OUT byte, VALID, HALTED) from the 10 output bits."""
    return from_bits(outputs[:8]), outputs[8], outputs[9]


def build_cartridge(rom_words: Sequence[int]):
    assert len(rom_words) == ROM_WORDS
    b = NetBuilder(N_IN)
    btn = b.inputs()

    # ---- state: every LATCH first, so that the ROM and the logic can read them
    with b.section("latches"):
        pc, acc, breg = b.register(8), b.register(8), b.register(8)
        halted = b.latch()
        ram = [b.register(8) for _ in range(RAM_BYTES)]

    # ---- program memory: one ROM record addressed by PC (combinational in the same beat)
    instr = b.rom(pc, 16, rom_words)
    imm, m_bit, x_bit, op = instr[0:8], instr[11], instr[10], instr[12:16]

    with b.section("decode"):
        running = b.not_(halted)
        dec = b.decoder(op, enable=running)          # dec[k] = 1 iff op == k and not halted
        d = {name: dec[code] for name, code in OPS.items()}

    with b.section("address (imm + B)"):
        idx, _ = b.add(imm, breg)
        addr = b.mux_bus(x_bit, imm, idx)

    with b.section("RAM read (256:1 mux x 8)"):
        ram_q = b.mux_tree(addr, ram)
    with b.section("operand mux"):
        opnd = b.mux_bus(m_bit, imm, ram_q)

    with b.section("ALU"):
        sub = d["SUB"]
        addend = [b.xor(x, sub) for x in opnd]
        total, _ = b.add(acc, addend, sub)
        r_and = b.and_bus(acc, opnd)
        r_or = b.or_bus(acc, opnd)
        r_xor = b.xor_bus(acc, opnd)
        sel_sum = b.or_(d["ADD"], d["SUB"])
        acc_we = b.or_many([d["LD"], d["ADD"], d["SUB"], d["AND"], d["OR"], d["XOR"], d["IN"], d["SWAP"]])
        keep = b.not_(acc_we)
        acc_next = [b.or_of_ands([(d["LD"], opnd[i]), (sel_sum, total[i]), (d["AND"], r_and[i]),
                                  (d["OR"], r_or[i]), (d["XOR"], r_xor[i]), (d["IN"], btn[i]),
                                  (d["SWAP"], breg[i]), (keep, acc[i])]) for i in range(8)]
        b_keep = b.nor(d["LDB"], d["SWAP"])
        b_next = [b.or_of_ands([(d["LDB"], opnd[i]), (d["SWAP"], acc[i]), (b_keep, breg[i])]) for i in range(8)]

    with b.section("PC / branch / halt"):
        acc_zero = b.is_zero(acc)
        take = b.or_many([d["JMP"], b.and_(d["JZ"], acc_zero), b.and_(d["JNZ"], b.not_(acc_zero))])
        pc1 = b.inc(pc)
        halt_now = b.or_(halted, d["HALT"])
        pc_next = b.mux_bus(halt_now, b.mux_bus(take, pc1, imm), pc)

    with b.section("RAM write (decode + 3 NAND/bit)"):
        lo = b.decoder(addr[0:4])
        hi = b.decoder(addr[4:8], enable=d["ST"])
        for k in range(RAM_BYTES):
            nwe = b.nand(hi[k >> 4], lo[k & 15])
            we = b.not_(nwe)
            for i in range(8):
                q = ram[k][i]
                b.set_d(q, b.nand(b.nand(q, nwe), b.nand(acc[i], we)))

    b.set_reg(pc, pc_next)
    b.set_reg(acc, acc_next)
    b.set_reg(breg, b_next)
    b.set_d(halted, halt_now)

    data = b.finish(acc + [d["OUT"], halt_now])

    lat = b.sig_to_latch
    info = CartInfo(
        latch={"pc": [lat[s] for s in pc], "acc": [lat[s] for s in acc], "b": [lat[s] for s in breg],
               "halted": [lat[halted]]},
        ram_latch=[[lat[s] for s in r] for r in ram],
        signal={"instr": instr, "alu_sum": total, "acc_next": acc_next, "ram_q": ram_q, "opnd": opnd},
        gates=b.gate_counts(),
    )
    return data, info


if __name__ == "__main__":
    import json
    from player import load
    words, _ = load("dodge")
    data, info = build_cartridge(words)
    print(len(data), "bytes")
    print(json.dumps(info.gates, indent=1))
