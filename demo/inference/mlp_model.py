"""The integer model of model/weights.json and a beat-by-beat mirror of the circuit rtl/mlp_digits.v.

Standard library only. `predict(pixels)` is the arithmetic the circuit computes (int8 weights, 4-bit inputs and
hidden activations, 20-bit accumulators, argmax with the lowest index on a tie). `trace(pixels)` runs the circuit's
register-transfer schedule beat by beat and returns, for every beat t, the registers at the start of the beat and
the outputs of the beat, so that the netlist can be compared with it on every beat and every state bit.

Schedule (one beat = one clock edge; T = 92 beats per inference, inputs: pixel t on beats 0..63, zero after):
    beats  0..63   L1     acc[j] = (t == 0 ? b1[j] : acc[j]) + x_t * W1[j][t]        16 lanes in parallel
    beat  64       ACT    h[j]   = min(15, max(0, acc[j]) >> SHIFT1)
    beats 65..80   L2     acc[c] = (i == 0 ? b2[c] : acc[c]) + h[i] * W2[c][i],  i = t - 65  (lanes 10..15: zero)
    beats 81..90   ARGMAX c = t - 81: if c == 0 or acc[c] > best: best = acc[c], cls = c
    beat  91       DONE   outputs: cls (4 bits) and done = 1; the state no longer changes
The outputs of a beat are the values before the clock edge: cls_o shows the `cls` register, done_o is 1 iff the
phase register is DONE at the start of the beat, so the final class is on the outputs of beat 91 (= T - 1).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, NamedTuple, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
WEIGHTS = os.path.join(HERE, "model", "weights.json")
TEST_DIGITS = os.path.join(HERE, "model", "test_digits.json")

PH_L1, PH_ACT, PH_L2, PH_ARGMAX, PH_DONE = 0, 1, 2, 3, 4
PHASE_NAMES = ["L1", "ACT", "L2", "ARGMAX", "DONE"]
N_IN, N_HIDDEN, N_OUT, LANES = 64, 16, 10, 16
ACC_BITS = 20
ACC_MASK = (1 << ACC_BITS) - 1
T_BEATS = N_IN + 1 + N_HIDDEN + N_OUT + 1          # 92
BEAT_ACT = N_IN                                     # 64
BEAT_L2 = N_IN + 1                                  # 65
BEAT_ARGMAX = BEAT_L2 + N_HIDDEN                    # 81
BEAT_DONE = BEAT_ARGMAX + N_OUT                     # 91
N_INPUT_BITS = 4
N_OUTPUT_BITS = 5                                   # cls[3:0], done


def load_weights(path: str = WEIGHTS) -> dict:
    with open(path) as f:
        return json.load(f)


def load_test_digits(path: str = TEST_DIGITS) -> dict:
    with open(path) as f:
        return json.load(f)


def pixels_from_hex(s: str) -> List[int]:
    return [int(c, 16) for c in s]


def quantize_pixels(values: Sequence[int]) -> List[int]:
    """Raw sklearn pixels (0..16) to 4 bits: min(v, 15)."""
    return [min(int(v), 15) for v in values]


def to_signed(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def predict(pixels: Sequence[int], w: dict) -> int:
    """The class the integer model gives; the plain arithmetic, without the beat schedule."""
    return forward(pixels, w)["cls"]


def forward(pixels: Sequence[int], w: dict) -> dict:
    assert len(pixels) == N_IN and all(0 <= p <= 15 for p in pixels)
    acc1 = [sum(pixels[k] * w["w1"][j][k] for k in range(N_IN)) + w["b1"][j] for j in range(N_HIDDEN)]
    h = [min(15, max(0, a) >> w["shift1"]) for a in acc1]
    acc2 = [sum(h[i] * w["w2"][c][i] for i in range(N_HIDDEN)) + w["b2"][c] for c in range(N_OUT)]
    cls = 0
    for c in range(1, N_OUT):
        if acc2[c] > acc2[cls]:
            cls = c
    return {"acc1": acc1, "h": h, "acc2": acc2, "cls": cls}


class Regs(NamedTuple):
    """The registers of the circuit at the start of a beat (acc and best as 20-bit two's complement values)."""
    phase: int
    cnt: int
    acc: List[int]      # 16 lanes, each 0 .. 2^20 - 1
    h: List[int]        # 16 x 4 bits
    best: int
    cls: int

    def bits(self) -> Dict[str, List[int]]:
        """Named register bits, LSB first (for the mapping of LATCH ordinals to registers)."""
        out = {}
        for i in range(3):
            out[f"phase[{i}]"] = [(self.phase >> i) & 1]
        for i in range(6):
            out[f"cnt[{i}]"] = [(self.cnt >> i) & 1]
        for j in range(LANES):
            for i in range(ACC_BITS):
                out[f"acc{j}[{i}]"] = [(self.acc[j] >> i) & 1]
        for j in range(LANES):
            for i in range(4):
                out[f"h{j}[{i}]"] = [(self.h[j] >> i) & 1]
        for i in range(ACC_BITS):
            out[f"best[{i}]"] = [(self.best >> i) & 1]
        for i in range(4):
            out[f"cls[{i}]"] = [(self.cls >> i) & 1]
        return out


def inputs_for(pixels: Sequence[int], T: int = T_BEATS) -> List[List[int]]:
    """The input vector of every beat: pixel t (4 bits, LSB first) on beats 0..63, zero after (canonical inputs)."""
    assert len(pixels) == N_IN
    out = []
    for t in range(T):
        v = pixels[t] if t < N_IN else 0
        out.append([(v >> i) & 1 for i in range(N_INPUT_BITS)])
    return out


def outputs_of(regs: Regs) -> List[int]:
    """The 5 output bits of a beat that starts in `regs`: cls_o[0..3], done_o."""
    return [(regs.cls >> i) & 1 for i in range(4)] + [1 if regs.phase == PH_DONE else 0]


def step(regs: Regs, x_in: int, w: dict) -> Regs:
    """One clock edge of rtl/mlp_digits.v."""
    phase, cnt, acc, h, best, cls = regs
    lanes = range(LANES)
    if phase in (PH_L1, PH_L2):
        l1 = phase == PH_L1
        xin = x_in if l1 else h[cnt & 15]
        row = [w["w1"][j][cnt] if l1 else (w["w2"][j][cnt & 15] if j < N_OUT else 0) for j in lanes]
        bias = [w["b1"][j] if l1 else (w["b2"][j] if j < N_OUT else 0) for j in lanes]
        first = cnt == 0
        new_acc = []
        for j in lanes:
            base = bias[j] if first else to_signed(acc[j], ACC_BITS)
            new_acc.append((base + xin * row[j]) & ACC_MASK)
        last = cnt == (N_IN - 1 if l1 else N_HIDDEN - 1)
        return Regs(PH_ACT if (l1 and last) else (PH_ARGMAX if last else phase), 0 if last else cnt + 1, new_acc,
                    h, best, cls)
    if phase == PH_ACT:
        new_h = []
        for j in lanes:
            a = to_signed(acc[j], ACC_BITS)
            new_h.append(min(15, max(0, a) >> w["shift1"]))
        return Regs(PH_L2, 0, acc, new_h, best, cls)
    if phase == PH_ARGMAX:
        c = cnt & 15
        cur = acc[c]
        upd = c == 0 or to_signed(cur, ACC_BITS) > to_signed(best, ACC_BITS)
        last = cnt == N_OUT - 1
        return Regs(PH_DONE if last else phase, cnt if last else cnt + 1, acc, h, cur if upd else best,
                    c if upd else cls)
    return regs     # DONE: hold


def initial_regs() -> Regs:
    return Regs(PH_L1, 0, [0] * LANES, [0] * LANES, 0, 0)


def trace(pixels: Sequence[int], w: dict, T: int = T_BEATS) -> List[Regs]:
    """regs[t] = the registers at the start of beat t, for t = 0 .. T (T + 1 entries)."""
    ins = inputs_for(pixels, T)
    regs = [initial_regs()]
    for t in range(T):
        x = sum(b << i for i, b in enumerate(ins[t]))
        regs.append(step(regs[-1], x, w))
    return regs


def circuit_class(pixels: Sequence[int], w: dict) -> int:
    """The class shown on the outputs of the last beat (T - 1), from the beat schedule."""
    r = trace(pixels, w)
    assert r[T_BEATS - 1].phase == PH_DONE
    return r[T_BEATS - 1].cls


def check_schedule_matches_arithmetic(w: dict, digits: Sequence[Sequence[int]]) -> None:
    """The beat schedule (trace) and the plain arithmetic (forward) agree on acc1, h, acc2 and the class."""
    for px in digits:
        f = forward(px, w)
        r = trace(px, w)
        assert [to_signed(a, ACC_BITS) for a in r[BEAT_ACT].acc] == f["acc1"]
        assert r[BEAT_L2].h == f["h"]
        assert [to_signed(a, ACC_BITS) for a in r[BEAT_ARGMAX].acc[:N_OUT]] == f["acc2"]
        assert r[BEAT_DONE].cls == f["cls"] and r[BEAT_DONE].phase == PH_DONE


if __name__ == "__main__":
    W = load_weights()
    D = load_test_digits()
    px = [pixels_from_hex(s) for s in D["pixels"]]
    check_schedule_matches_arithmetic(W, px)
    pred = [predict(p, W) for p in px]
    assert pred == D["intPrediction"], "mlp_model.py does not reproduce train.py's integer predictions"
    acc = sum(p == y for p, y in zip(pred, D["labels"])) / len(pred)
    print(f"{len(pred)} test digits: integer model accuracy {acc:.4f}; schedule mirror agrees with the arithmetic")
