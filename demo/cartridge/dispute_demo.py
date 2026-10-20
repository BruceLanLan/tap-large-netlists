"""High-score dispute on the dodge cartridge, played through assets/tap-draft-large-netlists/bisect_sim.py's referee.

A player claims "after T beats with these buttons my score is S". The claim is the spec 7.1 tuple
(netlistHash2, T, K, inputRoot, checkpointRoot) plus the published checkpoint leaves; the score is bound to it
because the cartridge's OUT pins show ACC on every beat, and after HALT ACC holds the score: the last checkpoint
leaf (time T) commits io_T = H(0x08 || io_{T-1} || outRoot_{T-1}), and outRoot_{T-1} commits the 10 output
bits of the final beat. open_final_score() checks such an opening against the published leaf.

RAM is bank 0 of the RAM ports (records 0x11 / 0x12), so a lie about RAM is a lie about a RAM write or read.

Scenarios (all against the same stored cartridge, default budget (6000, 256, 16)):
  A  honest claim,                    honest challenger   -> no dispute
  B  inflated score via a RAM write   (forge RAM[SCORE] bit after the last ST [SCORE]), honest challenger
  C  inflated score via a gate        (flip an ALU sum bit in the last ADD #1 on the score), honest challenger
  D  honest claim,                    cheating challenger (the cheater of B tries to knock the honest claim)
  E  inflated score via a RAM read    (flip a bit of the RAM read port output in the last LD [SCORE])
  F  honest claim,                    cheating challenger (the cheater of E)

    python3 dispute_demo.py
"""
from __future__ import annotations

import sys
import time

from _ref import t2
import bisect_sim as bs  # noqa: E402  (assets/tap-draft-large-netlists/, read-only)

from cpu import bits, build_cartridge, pins
from emulator import Machine
from isa import fields, OPS
from player import good_player, load, play

K = 32
BUDGET = t2.DEFAULT_BUDGET


def open_final_score(published, T: int, state_root_T: bytes, io_prev: bytes, out_bits):
    """Checks an opening of the final beat's outputs against the published last checkpoint leaf.
    Returns the (OUT byte, VALID, HALTED) it proves, or raises ValueError."""
    io_T = t2.io_next(io_prev, t2.out_root(out_bits))
    if t2.checkpoint_leaf(T, state_root_T, io_T) != published[-1]:
        raise ValueError("opening does not match the published checkpoint leaf at T")
    return pins(out_bits)


def claimed_score(party: bs.Party, published):
    """What the party's own claim says the final score is, via an opening of its last leaf."""
    T = party.T
    _, outs, _ = party._eval(T - 1)
    t, sr, _io = party.leaf(T)
    return open_final_score(published, T, sr, party.io[T - 1], outs)


def setup():
    words, names = load("dodge")
    data, info = build_cartridge(words)
    nl = t2.decode(data)
    buttons = play(words, names, good_player(names))
    inputs = [bits(x) for x in buttons]
    # beats (in the honest run) of the last score update: LD [SCORE] / ADD #1 / ST [SCORE] at label `survive`
    m, add_beats, st_beats, ld_beats = Machine(words), [], [], []
    for t, x in enumerate(buttons):
        if m.pc == names["survive"] and not m.halted:
            ld_beats.append(t)
        if m.pc == names["survive"] + 1 and not m.halted:
            add_beats.append(t)
        if m.pc == names["survive"] + 2 and not m.halted:
            st_beats.append(t)
        m.step(x)
    final_score = m.acc
    return {"words": words, "names": names, "data": data, "info": info, "nl": nl, "buttons": buttons,
            "inputs": inputs, "final_score": final_score, "t_add": add_beats[-1], "t_st": st_beats[-1],
            "t_ld": ld_beats[-1]}


def inflate_bit(score: int) -> int:
    """A bit that is 0 in the honest score, so that flipping it inflates the score: bit 6 (+64) if
    possible, otherwise the highest zero bit."""
    zeros = [i for i in range(8) if not (score >> i) & 1]
    return 6 if 6 in zeros else max(zeros)


def cheats(s):
    info, score = s["info"], s["final_score"]
    k = inflate_bit(score)
    sum_sig = info.signal["alu_sum"][k]
    assert sum_sig >= 2 + s["nl"].n_in, "cheat target must be a gate, not a constant or input"
    # LD [SCORE] reads score - 1 (the ADD #1 follows); flipping read bit k inflates the final score by 2^k
    # when that bit is 0 in score - 1 too: pick the bit for score - 1
    kr = inflate_bit(score - 1)
    return {
        "state": {"kind": "mem", "t": s["t_st"], "bank": 0, "addr": s["names"]["SCORE"], "bit": k},
        "gate": {"kind": "gate", "t": s["t_add"], "signal": sum_sig},
        "read": {"kind": "gate", "t": s["t_ld"], "signal": info.signal["ram_q"][kr]},
    }, k


def run_all(verbose: bool = True):
    t0 = time.time()
    s = setup()
    nl, data, inputs = s["nl"], s["data"], s["inputs"]
    store, nh2 = t2.oracle_for(data)
    ref = bs.Referee(store, BUDGET)
    T = len(inputs)
    cs, k = cheats(s)

    honest = bs.Party("honest player", nl, inputs, K, BUDGET)
    honest2 = bs.Party("honest checker", nl, inputs, K, BUDGET)
    liar_state = bs.Party("cheater (RAM write)", nl, inputs, K, BUDGET, cs["state"])
    liar_gate = bs.Party("cheater (ALU gate)", nl, inputs, K, BUDGET, cs["gate"])
    liar_read = bs.Party("cheater (RAM read)", nl, inputs, K, BUDGET, cs["read"])
    for liar in (liar_state, liar_gate, liar_read):
        assert liar.cp_leaves != honest.cp_leaves, "the lie must change the claim"

    scen = [("A honest claim vs honest checker", honest, honest2),
            ("B inflated score (RAM write) vs honest checker", liar_state, honest),
            ("C inflated score (gate) vs honest checker", liar_gate, honest),
            ("D honest claim vs cheating challenger", honest, liar_state),
            ("E inflated score (RAM read) vs honest checker", liar_read, honest),
            ("F honest claim vs cheating challenger (RAM read)", honest, liar_read)]
    results = {}
    for name, cl, ch in scen:
        published = cl.claim_data(nh2)[4]
        score = claimed_score(cl, published)
        res = bs.run_one(ref, nh2, cl, ch)
        res["claimant"], res["challenger"], res["claimedPins"] = cl.name, ch.name, score
        results[name] = res

    if verbose:
        bounds = t2.segment_boundaries(nl, BUDGET)
        print("== cartridge: dodge.asm on TAPBOY-8")
        print(f"   file {len(data):,} bytes, {t2.n_chunks(len(data))} chunks, netlistHash2 {t2.hx(nh2)}")
        print(f"   {len(bounds) - 1} canonical segments under budget {tuple(BUDGET)}")
        print(f"== run: T = {T} beats, K = {K} -> {len(t2.checkpoint_times(T, K))} published checkpoint leaves")
        print(f"   honest final score {s['final_score']}; cheaters flip bit {k} (+{1 << k})")
        print(f"   RAM-write cheat: RAM[SCORE] bit {k} after the write port in beat {s['t_st']};  gate cheat: "
              f"ALU sum bit {k} (signal {cs['gate']['signal']}) in beat {s['t_add']};  RAM-read cheat: read-port "
              f"signal {cs['read']['signal']} in beat {s['t_ld']}")
        for name, r in results.items():
            out, valid, halted = r["claimedPins"]
            print(f"\n-- {name}")
            print(f"   claim by {r['claimant']}: final OUT = {out} (halted={halted})  ->  'my score is {out}'")
            print(f"   winner: {r['winner']}   loser: {r['loser']}")
            print(f"   rounds: {r['totalRounds']}  {r['rounds']}   verifier calls: {r['verifyCalls']}")
            print(f"   decided by: {r['final']}  beat={r['beat']}  index={r['index']}  "
                  f"witness={r['witnessBytes']} bytes")
            print(f"   reason: {r['reason']}")
        print(f"\n(total {time.time() - t0:.1f} s)")
    return s, results


def check(results) -> None:
    a, b, c, d, e, f = (results[k] for k in sorted(results))
    assert a["verifyCalls"] == 0 and a["winner"] == "honest player"
    for r in (b, c, d, e, f):
        assert r["winner"] == "honest player" and r["verifyCalls"] == 1, r
    for r in (b, c, d, e, f):   # with one segment and memory in M, every lie is decided by verify_segment
        assert r["final"].startswith("verify_segment"), r["final"]


if __name__ == "__main__":
    _, res = run_all()
    check(res)
    print("OK: every cheater lost; each dispute that reached the chain was decided by exactly one verifier call")
    sys.exit(0)
