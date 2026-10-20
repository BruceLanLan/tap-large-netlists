"""Builds both cartridges with RAM ports (build/*.tapnet2) and, for comparison, with LATCH RAM
(build/legacy/*.tapnet2), and prints the measurements: size, chunks, netlistHash2, gate counts, canonical segments
under the default budget with their costs, and honest segment witness sizes of the dodge run (each also checked
with verify_segment against the store).

    python3 report.py
"""
from __future__ import annotations

import os

from _ref import t2
from cpu import bits, build_cartridge
from player import good_player, load, play

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET = t2.DEFAULT_BUDGET


def describe(name: str, data: bytes, info) -> dict:
    nl = t2.decode(data)
    met = nl.metrics()
    bounds = t2.segment_boundaries(nl, BUDGET)
    read = t2._file_reader(data)
    segs = []
    for o, s, l in bounds[:-1]:
        seg = t2.canonical_segment(read, len(data), o, s, l, BUDGET)
        segs.append({"offset": o, "firstSignal": s, "latchOrdinal": l, "records": len(seg.records),
                     "stop": seg.stop, **seg.cost.as_json()})
    by_type = {"NAND": 7 * met["nand"], "LATCH": 4 * met["latch"],
               "ROM": sum(r.raw.end - r.raw.off for r in nl.records if r.raw.op == t2.OP_ROM),
               "RAM ports": sum(r.raw.end - r.raw.off for r in nl.records if r.raw.op in (t2.OP_RAMR, t2.OP_RAMW)),
               "header": 16}
    return {"name": name, "bytes": len(data), "chunks": t2.n_chunks(len(data)),
            "netlistHash2": t2.hx(t2.netlist_hash2(data)), "metrics": met, "bytesByRecord": by_type,
            "nandBySection": info.gates["nandBySection"], "segments": segs, "nl": nl}


def witness_sizes(nl, inputs, beat: int):
    """Honest witnesses for every segment of `beat`, each checked by verify_segment."""
    store, nh2 = t2.oracle_for(nl.data)
    state, mem = [0] * nl.n_latch, {}
    for t in range(beat):
        _, _, state, mem, _ = t2.evaluate_full(nl, state, mem, inputs[t])
    # the game passes (latchRoot_t, memRoot_t), from the opening of stateRoot_t (EMPTY, ZERO_MEM in beat 0)
    lroot = t2.state_root(state) if beat else t2.EMPTY
    mroot = t2.mem_root(nl, mem) if nl.has_mem else t2.ZERO_MEM
    V, _, _, _, ev = t2.evaluate_full(nl, state, mem, inputs[beat])
    tuples = t2.segment_tuples(nl, V, BUDGET, mem, ev)
    out = []
    for j in range(1, len(tuples)):
        w = t2.build_seg_witness(nl, V, state, tuples[j - 1], tuples[j].byte_offset, lroot, mem, ev)
        enc = t2.serialize_seg_witness(w)
        ok = t2.verify_segment(store, nh2, lroot, tuples[j - 1], tuples[j], enc, BUDGET, mroot)
        out.append((len(enc), ok, t2.LAST_READ["chunks"], len(w.mem_reads), len(w.mem_writes)))
    return out


def witness_range(nl, inputs, beats):
    """(min, max) honest segment witness size and the state-list steps per beat, over the given beats."""
    sizes = []
    for beat in beats:
        sizes += [x[0] for x in witness_sizes(nl, inputs, beat)]
    return min(sizes), max(sizes), (nl.n_latch + 255) // 256


def main():
    os.makedirs(os.path.join(HERE, "build", "legacy"), exist_ok=True)
    reports = {}
    for variant, sub in (("ports", ""), ("latch", "legacy")):
        for prog in ("hello", "dodge"):
            words, names = load(prog)
            data, info = build_cartridge(words, ram=variant)
            with open(os.path.join(HERE, "build", sub, prog + ".tapnet2"), "wb") as f:
                f.write(data)
            reports[variant, prog] = (words, names, describe(prog, data, info))

    for (variant, prog), (words, names, r) in reports.items():
        m = r["metrics"]
        used = next(i for i in range(len(words)) if all(w == 0xF000 for w in words[i:])) + 1
        path = ("build/" if variant == "ports" else "build/legacy/") + prog + ".tapnet2"
        what = "RAM ports" if variant == "ports" else "LATCH RAM, for comparison"
        print(f"== {path}  ({what}; {used} instructions incl. the final HALT; the rest of the ROM is HALT)")
        print(f"   {r['bytes']:,} bytes ({r['bytes'] / 49152:.2f} x the 49,152-byte limit of TAP-draft-netlist-bound-nft), "
              f"{r['chunks']} chunks of 16,384")
        print(f"   netlistHash2 {r['netlistHash2']}")
        print(f"   nIn {m['nIn']}  nOut {m['nOut']}  NAND {m['nand']:,}  LATCH {m['latch']:,}  "
              f"ROM records {m['romRecords']} ({m['romBits']:,} bits)  signals {m['signals']:,}"
              + (f"  RAM banks {m['ramBanks']} ({m['ramBits']:,} bits), read ports {m['ramReadPorts']}, "
                 f"write ports {m['ramWritePorts']}" if "ramBanks" in m else ""))
        print("   bytes by record type: " + ", ".join(f"{k} {v:,}" for k, v in r["bytesByRecord"].items()))
    for variant in ("ports", "latch"):
        words, names, r = reports[variant, "dodge"]
        print(f"\n-- dodge, {'RAM ports' if variant == 'ports' else 'LATCH RAM'}")
        print("   NAND gates by block:")
        for k, v in r["nandBySection"].items():
            print(f"     {k:34s} {v:6,}")
        print(f"   canonical segmentation under budget {tuple(BUDGET)}: {len(r['segments'])} segments")
        for j, s in enumerate(r["segments"]):
            print(f"     seg {j}: offset {s['offset']:6,}  firstSignal {s['firstSignal']:6,}  records {s['records']:5,}"
                  f"  cost gates={s['gates']} extWords={s['extWords']} chunks={s['chunks']}  (ends: {s['stop']})")
        buttons = play(words, names, good_player(names))
        inputs = [bits(x) for x in buttons]
        beat = len(buttons) // 2
        print(f"   honest segment witnesses, dodge run beat {beat} of {len(buttons)}:")
        for j, x in enumerate(witness_sizes(r["nl"], inputs, beat)):
            print(f"     seg {j}: witness {x[0]:,} bytes, verify_segment -> {x[1]}, chunks read {x[2]}, "
                  f"RAM read proofs {x[3]}, RAM write proofs {x[4]}")
        lo, hi, steps = witness_range(r["nl"], inputs, range(0, len(buttons), 25))
        print(f"   over every 25th beat: segment witnesses {lo:,} to {hi:,} bytes; {steps} state-list steps per beat")


if __name__ == "__main__":
    main()
