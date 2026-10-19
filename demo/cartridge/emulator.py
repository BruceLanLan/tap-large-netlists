"""Instruction-level emulator of TAPBOY-8, written from the ISA table in isa.py only (it does not look at the
netlist). Used as the independent reference the gate-level cartridge is checked against."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from isa import ROM_WORDS, fields


@dataclass
class Machine:
    rom: Sequence[int]
    pc: int = 0
    acc: int = 0
    b: int = 0
    halted: int = 0
    ram: List[int] = field(default_factory=lambda: [0] * 256)

    def __post_init__(self):
        assert len(self.rom) == ROM_WORDS

    def snapshot(self) -> dict:
        return {"pc": self.pc, "acc": self.acc, "b": self.b, "halted": self.halted, "ram": list(self.ram)}

    def step(self, buttons: int) -> Tuple[int, int, int]:
        """Executes one beat. Returns the pins of this beat: (OUT byte, VALID, HALTED)."""
        out = self.acc
        if self.halted:
            return out, 0, 1
        op, m, x, imm = fields(self.rom[self.pc])
        addr = (imm + self.b) & 0xFF if x else imm
        opnd = self.ram[addr] if m else imm
        nxt = (self.pc + 1) & 0xFF
        valid = 0
        if op == 0x1:
            self.acc = opnd
        elif op == 0x2:
            self.acc = (self.acc + opnd) & 0xFF
        elif op == 0x3:
            self.acc = (self.acc - opnd) & 0xFF
        elif op == 0x4:
            self.acc &= opnd
        elif op == 0x5:
            self.acc |= opnd
        elif op == 0x6:
            self.acc ^= opnd
        elif op == 0x7:
            self.b = opnd
        elif op == 0x8:
            self.ram[addr] = self.acc
        elif op == 0x9:
            nxt = imm
        elif op == 0xA:
            if self.acc == 0:
                nxt = imm
        elif op == 0xB:
            if self.acc != 0:
                nxt = imm
        elif op == 0xC:
            self.acc = buttons & 0xFF
        elif op == 0xD:
            valid = 1
        elif op == 0xE:
            self.acc, self.b = self.b, self.acc
        elif op == 0xF:
            self.halted = 1
            return out, 0, 1
        self.pc = nxt
        return out, valid, 0


def run(rom: Sequence[int], buttons: Sequence[int]):
    """Runs len(buttons) beats. Returns (pins per beat, snapshot after each beat)."""
    m = Machine(rom)
    pins, snaps = [], []
    for btn in buttons:
        pins.append(m.step(btn))
        snaps.append(m.snapshot())
    return pins, snaps


def run_until_halt(rom: Sequence[int], buttons_for=lambda t, m: 0, limit: int = 100000):
    """Runs until the beat in which HALTED first shows. buttons_for(t, machine) supplies the button byte of
    beat t (it may look at the machine, as a player looking at the screen would). Returns (buttons, pins)."""
    m = Machine(rom)
    buttons, pins = [], []
    for t in range(limit):
        btn = buttons_for(t, m) & 0xFF
        buttons.append(btn)
        pins.append(m.step(btn))
        if pins[-1][2]:
            return buttons, pins
    raise RuntimeError("program did not halt")


def printed(pins) -> bytes:
    """The bytes the program printed (OUT pins of the beats with VALID = 1)."""
    return bytes(o for o, v, _ in pins if v)
