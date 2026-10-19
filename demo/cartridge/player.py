"""Button sequences for the dodge game: a scripted "good player" (looks at the screen like a human would)
and random mashing. Beats where the CPU is not executing IN are don't-care; the player sets them to 0 so
that the committed input sequence is canonical."""
from __future__ import annotations

import os
import random
from typing import List

from assembler import assemble_file
from emulator import Machine, run_until_halt
from isa import OPS, fields

HERE = os.path.dirname(os.path.abspath(__file__))
LEFT, RIGHT = 1, 2


def load(name: str):
    words, names, _ = assemble_file(os.path.join(HERE, "programs", name + ".asm"))
    return words, names


def good_player(names):
    """Each round: reach the coin lane if one step gets there, otherwise step to any safe lane."""
    def buttons_for(t: int, m: Machine) -> int:
        if m.halted or fields(m.rom[m.pc])[0] != OPS["IN"]:
            return 0
        pos, lane = m.ram[names["POS"]], m.ram[names["LANE"]]
        moves = [(0, pos), (LEFT, (pos + 7) & 7), (RIGHT, (pos + 1) & 7)]
        for btn, p in moves:
            if p == lane ^ 4:
                return btn
        for btn, p in moves:
            if p != lane:
                return btn
        return 0
    return buttons_for


def lazy_player(t: int, m: Machine) -> int:
    """Never presses anything."""
    return 0


def random_player(seed: int):
    rng = random.Random(seed)
    return lambda t, m: rng.randrange(256)


def play(rom, names, who) -> List[int]:
    """Buttons for every beat until the game halts (inclusive)."""
    buttons, _ = run_until_halt(rom, who)
    return buttons
