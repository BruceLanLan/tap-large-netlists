"""Builds both cartridges, writes them to build/*.tapnet2, and prints the measurements: size, chunks,
netlistHash2, gate counts, canonical segments under the default budget with their costs, and honest segment
witness sizes for one beat of the dodge run (each also checked with verify_segment against the store).

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
               "ROM": sum(r.raw.end - r.raw.off for r in nl.records if r.raw.op == t2.OP_ROM), "header": 16}
    return {"name": name, "bytes": len(data), "chunks": t2.n_chunks(len(data)),
            "netlistHash2": t2.hx(t2.netlist_hash2(data)), "metrics": met, "bytesByRecord": by_type,
            "nandBySection": info.gates["nandBySection"], "segments": segs, "nl": nl}


def witness_sizes(nl, inputs, beat: int):
    """Honest witnesses for every segment of `beat`, each checked by verify_segment."""
    store, nh2 = t2.oracle_for(nl.data)
    state = [0] * nl.n_latch
    sroot = t2.EMPTY
    for t in range(beat):
        _, state = t2.step(nl, state, inputs[t])
        sroot = t2.state_root(state)
    V, _, _ = t2.evaluate(nl, state, inputs[beat])
    tuples = t2.segment_tuples(nl, V, BUDGET)
    out = []
    for j in range(1, len(tuples)):
        w = t2.build_seg_witness(nl, V, state, tuples[j - 1], tuples[j].byte_offset, sroot)
        enc = t2.serialize_seg_witness(w)
        ok = t2.verify_segment(store, nh2, sroot, tuples[j - 1], tuples[j], enc, BUDGET)
        out.append((len(enc), ok, t2.LAST_READ["chunks"]))
    return out


def main():
    os.makedirs(os.path.join(HERE, "build"), exist_ok=True)
    reports = []
    for prog in ("hello", "dodge"):
        words, names = load(prog)
        data, info = build_cartridge(words)
        with open(os.path.join(HERE, "build", prog + ".tapnet2"), "wb") as f:
            f.write(data)
        reports.append((prog, words, names, describe(prog, data, info)))

    for prog, words, names, r in reports:
        m = r["metrics"]
        used = next(i for i in range(len(words)) if all(w == 0xF000 for w in words[i:])) + 1
        print(f"== {prog}.tapnet2  ({used} instructions incl. the final HALT; the rest of the ROM is HALT)")
        print(f"   {r['bytes']:,} bytes ({r['bytes'] / 49152:.2f} x the 49,152-byte limit of TAP-draft-netlist-bound-nft), "
              f"{r['chunks']} chunks of 16,384")
        print(f"   netlistHash2 {r['netlistHash2']}")
        print(f"   nIn {m['nIn']}  nOut {m['nOut']}  NAND {m['nand']:,}  LATCH {m['latch']:,}  "
              f"ROM records {m['romRecords']} ({m['romBits']:,} bits)  signals {m['signals']:,}")
        print("   bytes by record type: " + ", ".join(f"{k} {v:,}" for k, v in r["bytesByRecord"].items()))
    print("\n   NAND gates by block (identical in both cartridges; only ROM data differs):")
    for k, v in reports[-1][3]["nandBySection"].items():
        print(f"     {k:34s} {v:6,}")
    r = reports[-1][3]
    print(f"\n   canonical segmentation under budget {tuple(BUDGET)}: {len(r['segments'])} segments")
    for j, s in enumerate(r["segments"]):
        print(f"     seg {j}: offset {s['offset']:6,}  firstSignal {s['firstSignal']:6,}  records {s['records']:5,}"
              f"  cost gates={s['gates']} extWords={s['extWords']} chunks={s['chunks']}  (ends: {s['stop']})")

    prog, words, names, r = reports[-1]
    buttons = play(words, names, good_player(names))
    inputs = [bits(x) for x in buttons]
    beat = len(buttons) // 2
    print(f"\n   honest segment witnesses, dodge run beat {beat} of {len(buttons)}:")
    for j, (size, ok, chunks) in enumerate(witness_sizes(r["nl"], inputs, beat)):
        print(f"     seg {j}: witness {size:,} bytes, verify_segment -> {ok}, chunks read {chunks}")


if __name__ == "__main__":
    main()
