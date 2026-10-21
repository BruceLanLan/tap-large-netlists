"""Disputes about one inference, played through the referee of assets/tap-draft-large-netlists/bisect_sim.py.

A claimant runs the circuit build/mlp_digits.tapnet2 on one test digit (T = 92 beats, K = 8: 13 checkpoint
leaves) and publishes the claim (netlistHash2, T, K, inputRoot, checkpointRoot) with the leaf list (spec 7.1).
The predicted class is bound to the claim because the circuit keeps it on its output pins from beat 91 on: the last
checkpoint leaf (time T = 92) commits io_92 = H(0x08 || io_91 || outRoot_91), and outRoot_91 commits the 5 output
bits of beat 91 (Appendix C of the TAP; `open_class`).

Scenarios (default budget (6000, 256, 16); every cheater recomputes everything downstream of its lie, so the
dispute has to reach the one-step verifier):
  A  honest claim                                   vs honest checker     -> no dispute
  B  class lie: a gate feeding the class register flipped in the last ARGMAX beat   (claimant)
  C  accumulator lie: an adder output bit of one lane flipped in an L1 beat          (claimant)
  D  state lie: a bit of the new accumulator state after an L1 beat forged           (claimant; verify_beat_end)
  E  honest claim vs the cheater of B as challenger
  F  honest claim vs the cheater of C as challenger
  G  accumulator lie as C, in a lane whose adder lies in the middle segment of the beat   (claimant)
With B, C and G the final call lands on each of the three canonical segments of a beat.

    python3 mlp_dispute.py [digit index in the test split, default 0]
"""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Tuple

from _paths import t2
import bisect_sim as bs  # noqa: E402  (assets/tap-draft-large-netlists/, read-only)

import mlp_model as M

HERE = os.path.dirname(os.path.abspath(__file__))
NETLIST = os.path.join(HERE, "build", "mlp_digits.tapnet2")
K = 8
BUDGET = t2.DEFAULT_BUDGET
DIGIT = 0


def load_netlist(path: str = NETLIST) -> t2.Netlist:
    with open(path, "rb") as f:
        return t2.decode(f.read())


def open_class(published: List[bytes], T: int, state_root_T: bytes, io_prev: bytes, out_bits) -> Tuple[int, int]:
    """Checks an opening of the last beat's outputs against the published last checkpoint leaf (Appendix C).
    Returns (class, done) or raises ValueError."""
    io_T = t2.io_next(io_prev, t2.out_root(out_bits))
    if t2.checkpoint_leaf(T, state_root_T, io_T) != published[-1]:
        raise ValueError("opening does not match the published checkpoint leaf at T")
    return sum(b << i for i, b in enumerate(out_bits[:4])), out_bits[4]


def claimed_class(party: bs.Party, published: List[bytes]) -> Tuple[int, int]:
    """What the party's own claim says: the class on the outputs of beat T - 1, opened from its last leaf."""
    T = party.T
    _, outs, _ = party._eval(T - 1)
    _, sr, _ = party.leaf(T)
    return open_class(published, T, sr, party.io[T - 1], outs)


def honest_states(nl: t2.Netlist, inputs) -> List[List[int]]:
    states = [[0] * nl.n_latch]
    mem: dict = {}
    for t in range(len(inputs)):
        _, _, st, mem, _ = t2.evaluate_full(nl, states[-1], mem, inputs[t])
        states.append(st)
    return states


def latch_map(nl: t2.Netlist, runs: List[Tuple[List[List[int]], List[M.Regs]]]) -> Dict[str, List[int]]:
    """Register bit name -> LATCH ordinals whose value sequence over the given runs (netlist states, model
    registers) equals that bit's sequence; the converter keeps no names, and a register bit can have a copy (see
    verilog2tapnet's output tail). Bits whose sequence is constant or equal to another register bit's in all runs
    (for example the sign-extension bits of an accumulator that stays small) are left out as ambiguous."""
    bits = [[r.bits() for r in regs] for _, regs in runs]
    sig: Dict[tuple, List[str]] = {}
    for name in bits[0][0]:
        seq = tuple(b[name][0] for run in bits for b in run)
        sig.setdefault(seq, []).append(name)
    out: Dict[str, List[int]] = {}
    for l in range(nl.n_latch):
        seq = tuple(states[t][l] for states, _ in runs for t in range(len(states)))
        names = sig.get(seq, [])
        if len(names) == 1 and len(set(seq)) > 1:
            out.setdefault(names[0], []).append(l)
    return out


def signature_digits(d: dict, digit: int, n_labels: int = 4) -> List[int]:
    """`digit` and then the first test digit of each further label, until n_labels labels are covered."""
    out, seen = [digit], {d["labels"][digit]}
    for i, y in enumerate(d["labels"]):
        if len(seen) >= n_labels:
            break
        if y not in seen:
            out.append(i)
            seen.add(y)
    return out


def is_gate(nl: t2.Netlist, signal: int) -> bool:
    """True iff the signal is the output of a NAND record."""
    if signal < 2 + nl.n_in:
        return False
    lo, hi = 0, len(nl.records)
    while lo < hi:                                   # records are in signal order
        mid = (lo + hi) // 2
        r = nl.records[mid]
        if signal < r.first_out:
            hi = mid
        elif signal >= r.first_out + r.raw.n_out:
            lo = mid + 1
        else:
            return r.raw.op == t2.OP_NAND
    return False


def trace_with_acc_flip(pixels, w: dict, t: int, lane: int, bit: int) -> int:
    """The class the model gives when bit `bit` of lane `lane`'s new accumulator is flipped after beat t."""
    ins = M.inputs_for(pixels)
    r = M.initial_regs()
    for tt in range(M.T_BEATS):
        x = sum(b << i for i, b in enumerate(ins[tt]))
        r = M.step(r, x, w)
        if tt == t:
            acc = list(r.acc)
            acc[lane] ^= 1 << bit
            r = r._replace(acc=acc)
    assert r.phase == M.PH_DONE
    return r.cls


def setup(digit: int = DIGIT) -> dict:
    nl = load_netlist()
    w = M.load_weights()
    d = M.load_test_digits()
    pixels = M.pixels_from_hex(d["pixels"][digit])
    label = d["labels"][digit]
    inputs = M.inputs_for(pixels)
    regs = M.trace(pixels, w)
    states = honest_states(nl, inputs)
    runs = [(states, regs)]
    for i in signature_digits(d, digit)[1:]:
        px_i = M.pixels_from_hex(d["pixels"][i])
        runs.append((honest_states(nl, M.inputs_for(px_i)), M.trace(px_i, w)))
    lm = latch_map(nl, runs)
    honest_cls = regs[M.BEAT_DONE].cls
    # B: the d signal of a class-register bit whose flip gives another digit 0..9, in the last ARGMAX beat
    t_b = M.BEAT_ARGMAX + M.N_OUT - 1           # 90
    bit_b = next(b for b in range(4) if (honest_cls ^ (1 << b)) < M.N_OUT and f"cls[{b}]" in lm)
    cls_latch = lm[f"cls[{bit_b}]"][0]
    sig_b = nl.latch_d[cls_latch]
    assert is_gate(nl, sig_b), "the d of the class bit must be a gate"
    # C and D: the first (beat, lane, bit) in a fixed scan order whose accumulator flip changes the class;
    # G: the same, restricted to lanes whose adder output (the d of the bit) lies in the middle segment
    bounds = t2.segment_boundaries(nl, BUDGET)
    assert len(bounds) == 4, "the demo expects three canonical segments per beat"
    mid = range(bounds[1][1], bounds[2][1])

    def find_acc_lie(signal_range=None):
        for bit in range(14, 8, -1):
            for t in range(M.N_IN - 1, M.N_IN // 2, -8):
                for lane in range(M.N_HIDDEN):
                    name = f"acc{lane}[{bit}]"
                    if name not in lm:
                        continue
                    sig = nl.latch_d[lm[name][0]]
                    if signal_range is not None and sig not in signal_range:
                        continue
                    if trace_with_acc_flip(pixels, w, t, lane, bit) != honest_cls:
                        return t, lane, bit, lm[name][0], sig
        raise AssertionError("no accumulator flip among the candidates changes the class")

    t_c, lane_c, bit_c, acc_latch, sig_c = find_acc_lie()
    t_g, lane_g, bit_g, acc_latch_g, sig_g = find_acc_lie(mid)
    assert is_gate(nl, sig_c) and is_gate(nl, sig_g), "the d of an accumulator bit must be a gate"
    cheats = {
        "class": {"kind": "gate", "t": t_b, "signal": sig_b},
        "acc_gate": {"kind": "gate", "t": t_c, "signal": sig_c},
        "acc_state": {"kind": "state", "t": t_c, "latch": acc_latch},
        "acc_gate_mid": {"kind": "gate", "t": t_g, "signal": sig_g},
    }
    return {"nl": nl, "w": w, "digit": digit, "pixels": pixels, "label": label, "inputs": inputs, "regs": regs,
            "states": states, "latch_map": lm, "honest_cls": honest_cls, "cheats": cheats,
            "where": {"class": (t_b, f"cls[{bit_b}]", cls_latch, sig_b),
                      "acc": (t_c, f"acc{lane_c}[{bit_c}]", acc_latch, sig_c),
                      "acc_mid": (t_g, f"acc{lane_g}[{bit_g}]", acc_latch_g, sig_g)}}


def parties(s: dict) -> Dict[str, bs.Party]:
    nl, inputs, cs = s["nl"], s["inputs"], s["cheats"]
    p = {
        "honest": bs.Party("honest prover", nl, inputs, K, BUDGET),
        "honest2": bs.Party("honest checker", nl, inputs, K, BUDGET),
        "class": bs.Party("cheater (class gate)", nl, inputs, K, BUDGET, cs["class"]),
        "acc_gate": bs.Party("cheater (accumulator gate)", nl, inputs, K, BUDGET, cs["acc_gate"]),
        "acc_state": bs.Party("cheater (accumulator state)", nl, inputs, K, BUDGET, cs["acc_state"]),
        "acc_gate_mid": bs.Party("cheater (accumulator gate, middle segment)", nl, inputs, K, BUDGET,
                                 cs["acc_gate_mid"]),
    }
    for name in ("class", "acc_gate", "acc_state", "acc_gate_mid"):
        assert p[name].cp_leaves != p["honest"].cp_leaves, f"{name}: the lie does not change the claim"
    return p


SCENARIOS = [("A honest claim vs honest checker", "honest", "honest2"),
             ("B class lie (gate) vs honest checker", "class", "honest"),
             ("C accumulator lie (gate) vs honest checker", "acc_gate", "honest"),
             ("D accumulator lie (state bit) vs honest checker", "acc_state", "honest"),
             ("E honest claim vs cheating challenger (class)", "honest", "class"),
             ("F honest claim vs cheating challenger (accumulator)", "honest", "acc_gate"),
             ("G accumulator lie (gate, middle segment) vs honest checker", "acc_gate_mid", "honest")]


def run_all(verbose: bool = True, digit: int = DIGIT):
    t0 = time.time()
    s = setup(digit)
    nl = s["nl"]
    store, nh2 = t2.oracle_for(nl.data)
    ref = bs.Referee(store, BUDGET)
    P = parties(s)
    results = {}
    for name, cl, ch in SCENARIOS:
        published = P[cl].claim_data(nh2)[4]
        cls, done = claimed_class(P[cl], published)
        res = bs.run_one(ref, nh2, P[cl], P[ch])
        res["claimant"], res["challenger"], res["claimedClass"], res["claimedDone"] = P[cl].name, P[ch].name, cls, done
        results[name] = res
    if verbose:
        bounds = t2.segment_boundaries(nl, BUDGET)
        T = len(s["inputs"])
        print("== inference: build/mlp_digits.tapnet2 on test digit", digit, f"(label {s['label']})")
        print(f"   file {len(nl.data):,} bytes, {t2.n_chunks(len(nl.data))} chunks, netlistHash2 {t2.hx(nh2)}")
        print(f"   {len(bounds) - 1} canonical segments per beat under budget {tuple(BUDGET)}")
        print(f"== run: T = {T} beats, K = {K} -> {len(t2.checkpoint_times(T, K))} published checkpoint leaves")
        print(f"   honest class {s['honest_cls']}; class lie: beat {s['where']['class'][0]}, signal "
              f"{s['where']['class'][3]} (d of {s['where']['class'][1]}); accumulator lie: beat "
              f"{s['where']['acc'][0]}, signal {s['where']['acc'][3]} (d of {s['where']['acc'][1]}, latch ordinal "
              f"{s['where']['acc'][2]}); middle-segment lie: beat {s['where']['acc_mid'][0]}, signal "
              f"{s['where']['acc_mid'][3]} (d of {s['where']['acc_mid'][1]})")
        for name, r in results.items():
            print(f"\n-- {name}")
            print(f"   claim by {r['claimant']}: class {r['claimedClass']} (done={r['claimedDone']})")
            print(f"   winner: {r['winner']}   loser: {r['loser']}")
            print(f"   rounds: {r['totalRounds']}  {r['rounds']}   verifier calls: {r['verifyCalls']}")
            print(f"   decided by: {r['final']}  beat={r['beat']}  index={r['index']}  witness={r['witnessBytes']} bytes")
            print(f"   reason: {r['reason']}")
        print(f"\n(total {time.time() - t0:.1f} s)")
    return s, results


def check(results) -> None:
    a, b, c, d, e, f, g = (results[k] for k in sorted(results))
    assert a["verifyCalls"] == 0 and a["winner"] == "honest prover"
    for r in (b, c, d, e, f, g):
        assert r["winner"] == "honest prover" and r["verifyCalls"] == 1, r
    for r in (b, c, e, f, g):
        assert r["final"].startswith("verify_segment"), r["final"]
    assert d["final"].startswith("verify_beat_end[state]"), d["final"]
    assert g["final"] == "verify_segment(seg_1 -> seg_2)", g["final"]
    for r in (b, c, g):
        assert r["claimedClass"] != a["claimedClass"]


if __name__ == "__main__":
    digit = int(sys.argv[1]) if len(sys.argv) > 1 else DIGIT
    _, res = run_all(digit=digit)
    check(res)
    print("OK: every cheater lost; each dispute that reached the chain was decided by exactly one verifier call")
    sys.exit(0)
