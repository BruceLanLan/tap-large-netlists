"""Measures the demo and writes build/numbers.json (figure-ready) and prints the tables of README.md.

    python3 mlp_report.py [--digits N]      # N test digits compared with the netlist beat by beat (default 60)

Standard library only. Reads the committed build/mlp_digits.tapnet2, model/*.json, build/convert.json, the
Foundry gas rows contracts/test/data/inference_gas.csv and game_gas.csv (written by `forge test`, see
docs/TESTING.md), runs the dispute scenarios of mlp_dispute.py, and measures honest witnesses. Deterministic.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from typing import Dict, List

from _paths import t2
import mlp_dispute as D
import mlp_model as M

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "build", "numbers.json")
CONVERT = os.path.join(HERE, "build", "convert.json")
GAS_MOVES = os.path.join(REPO, "contracts", "test", "data", "inference_gas.csv")
GAS_TABLE = os.path.join(REPO, "contracts", "test", "data", "game_gas.csv")
BUDGET = t2.DEFAULT_BUDGET
SAMPLE_BEATS = list(range(0, M.T_BEATS, 10)) + [M.T_BEATS - 1]


def netlist_numbers(nl: t2.Netlist) -> dict:
    met = nl.metrics()
    bounds = t2.segment_boundaries(nl, BUDGET)
    read = t2._file_reader(nl.data)
    segs = []
    for o, s, l in bounds[:-1]:
        seg = t2.canonical_segment(read, len(nl.data), o, s, l, BUDGET)
        segs.append({"offset": o, "firstSignal": s, "latchOrdinal": l, "records": len(seg.records),
                     "stop": seg.stop, **seg.cost.as_json()})
    gates = sum(t2.record_gates(r.raw) for r in nl.records)
    by_type = {"NAND": 7 * met["nand"], "LATCH": 4 * met["latch"],
               "ROM": sum(r.raw.end - r.raw.off for r in nl.records if r.raw.op == t2.OP_ROM), "header": 16}
    roms = [{"a": r.raw.a, "w": r.raw.w, "entries": 1 << r.raw.a, "bytes": r.raw.end - r.raw.off}
            for r in nl.records if r.raw.op == t2.OP_ROM]
    out = {"file": "build/mlp_digits.tapnet2", "bytes": len(nl.data), "chunks": t2.n_chunks(len(nl.data)),
           "netlistHash2": t2.hx(t2.netlist_hash2(nl.data)), "metrics": met, "mem": nl.has_mem,
           "bytesByRecord": by_type, "romRecords": roms, "gateCostPerBeat": gates,
           "budget": {"gates": BUDGET.gates, "extWords": BUDGET.ext_words, "chunks": BUDGET.chunks},
           "segmentsPerBeat": len(segs), "segments": segs,
           "stateListSteps": (nl.n_latch + 255) // 256, "outputListSteps": (nl.n_out + 255) // 256}
    if os.path.exists(CONVERT):
        with open(CONVERT) as f:
            c = json.load(f)
        out["converter"] = {"abcScript": c.get("abc_script"), "yosysNand": c.get("yosys_nand"),
                            "yosysNot": c.get("yosys_not"), "yosysDff": c.get("yosys_dff"), "tail": c.get("tail"),
                            "memoryDecisions": c.get("memory_decisions")}
    return out


def witness_numbers(nl: t2.Netlist, inputs, beats=SAMPLE_BEATS) -> dict:
    """Honest segment witnesses of every segment of the sampled beats, each checked by verify_segment, and the
    honest state-list and output-list witnesses of one beat."""
    store, nh2 = t2.oracle_for(nl.data)
    bounds = t2.segment_boundaries(nl, BUDGET)
    per_seg: List[List[int]] = [[] for _ in bounds[:-1]]
    chunks_read: List[List[int]] = [[] for _ in bounds[:-1]]
    state, mem = [0] * nl.n_latch, {}
    states = [state]
    for t in range(len(inputs)):
        _, _, state, mem, _ = t2.evaluate_full(nl, states[-1], mem, inputs[t])
        states.append(state)
    end_sizes = {}
    for beat in beats:
        st = states[beat]
        lroot = t2.state_root(st) if beat else t2.EMPTY
        V, outs, ns, _, ev = t2.evaluate_full(nl, st, {}, inputs[beat])
        tuples = t2.segment_tuples(nl, V, BUDGET, {}, ev)
        for j in range(1, len(tuples)):
            w = t2.build_seg_witness(nl, V, st, tuples[j - 1], tuples[j].byte_offset, lroot, {}, ev)
            enc = t2.serialize_seg_witness(w)
            ok = t2.verify_segment(store, nh2, lroot, tuples[j - 1], tuples[j], enc, BUDGET, t2.ZERO_MEM)
            assert ok, f"honest witness rejected at beat {beat}, segment {j - 1}"
            per_seg[j - 1].append(len(enc))
            chunks_read[j - 1].append(t2.LAST_READ["chunks"])
        if beat == beats[len(beats) // 2]:
            stt = t2.state_tuples(ns)
            outt = t2.out_tuples(outs)
            ws = [len(t2.serialize_end_witness(t2.build_state_witness(nl, V, ns, k))) for k in range(len(stt) - 1)]
            wo = [len(t2.serialize_end_witness(t2.build_out_witness(nl, V, outs, k))) for k in range(len(outt) - 1)]
            end_sizes = {"beat": beat, "stateStepWitnessBytes": ws, "outputStepWitnessBytes": wo}
    return {"sampledBeats": beats,
            "segmentWitnessBytes": [{"segment": j, "min": min(v), "max": max(v), "chunksReadMax": max(c)}
                                    for j, (v, c) in enumerate(zip(per_seg, chunks_read))],
            "allSegmentsMin": min(min(v) for v in per_seg), "allSegmentsMax": max(max(v) for v in per_seg),
            **end_sizes}


def exact_match(nl: t2.Netlist, w: dict, digits: dict, n: int) -> dict:
    """The netlist against the integer model on the first n test digits: the outputs of every beat and the class."""
    match = correct = 0
    for i in range(n):
        px = M.pixels_from_hex(digits["pixels"][i])
        regs = M.trace(px, w)
        ins = M.inputs_for(px)
        st, mem = [0] * nl.n_latch, {}
        ok = True
        for t in range(M.T_BEATS):
            _, outs, st, mem, _ = t2.evaluate_full(nl, st, mem, ins[t])
            if outs != M.outputs_of(regs[t]):
                ok = False
                break
        cls = sum(b << k for k, b in enumerate(outs[:4]))
        ok = ok and cls == regs[M.T_BEATS - 1].cls == digits["intPrediction"][i]
        match += ok
        correct += cls == digits["labels"][i]
    return {"digitsCompared": n, "exactMatch": match, "exactMatchRate": match / n,
            "netlistAccuracyOnCompared": correct / n}


def gas_numbers() -> dict:
    if not (os.path.exists(GAS_MOVES) and os.path.exists(GAS_TABLE)):
        raise SystemExit(f"missing {GAS_MOVES} or {GAS_TABLE}: run `forge test` in contracts/ first (docs/TESTING.md)")
    moves: Dict[str, list] = {}
    with open(GAS_MOVES) as f:
        for row in csv.DictReader(f):
            moves.setdefault(row["scenario"], []).append(
                {"move": int(row["move"]), "actor": row["actor"], "type": row["type"], "gas": int(row["gas"])})
    table = {}
    with open(GAS_TABLE) as f:
        for row in csv.DictReader(f):
            if row["scenario"].startswith("inference/"):
                table[row["scenario"]] = {k: (int(v) if v.isdigit() else v) for k, v in row.items() if k != "scenario"}
    worst = max((dict(m, scenario=sc) for sc, ms in moves.items() for m in ms), key=lambda m: m["gas"])
    verifier = {}
    for sc, ms in moves.items():
        for m in ms:
            if m["type"] in ("proveSegment", "proveBeatEnd"):
                verifier[sc] = {"type": m["type"], "gas": m["gas"], "actor": m["actor"]}
    by_type: Dict[str, List[int]] = {}
    for ms in moves.values():
        for m in ms:
            by_type.setdefault(m["type"], []).append(m["gas"])
    return {"source": "contracts/test/data/inference_gas.csv and game_gas.csv (forge test, Foundry 1.8.3; per "
                      "transaction incl. 21,000 intrinsic and calldata gas, before refunds)",
            "perScenario": table, "finalCall": verifier,
            "worstMove": worst, "byMoveType": {k: {"min": min(v), "max": max(v), "n": len(v)} for k, v in by_type.items()},
            "moves": moves}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    n_digits = int(argv[argv.index("--digits") + 1]) if "--digits" in argv else 60
    t0 = time.time()
    s, results = D.run_all(verbose=False)
    nl, w = s["nl"], s["w"]
    digits = M.load_test_digits()
    net = netlist_numbers(nl)
    wit = witness_numbers(nl, s["inputs"])
    em = exact_match(nl, w, digits, n_digits)
    full_pred = [M.predict(M.pixels_from_hex(p), w) for p in digits["pixels"]]
    assert full_pred == digits["intPrediction"]
    int_acc = sum(p == y for p, y in zip(full_pred, digits["labels"])) / len(full_pred)
    tr = w["training"]
    T = len(s["inputs"])
    disputes = {}
    for name, r in results.items():
        disputes[name] = {k: r[k] for k in ("claimant", "challenger", "claimedClass", "winner", "loser", "rounds",
                                             "totalRounds", "verifyCalls", "final", "beat", "index", "witnessBytes")}
    numbers = {
        "generatedBy": "demo/inference/mlp_report.py",
        "note": "A demonstration on the reference implementation; not a deployed service and not a claim about any "
                "production model.",
        "model": {"architecture": "64-16-10 MLP, ReLU", "inputBits": w["inputBits"], "hiddenBits": w["hiddenBits"],
                  "weightBits": w["weightBits"], "accBits": w["accBits"], "shift1": w["shift1"],
                  "dataset": tr["dataset"], "split": tr["split"],
                  "floatTrainAccuracy": tr["floatTrainAccuracy"], "floatTestAccuracy": tr["floatTestAccuracy"],
                  "intTrainAccuracy": tr["intTrainAccuracy"], "intTestAccuracy": int_acc,
                  "intTestAccuracyFromTrainPy": tr["intTestAccuracy"]},
        "netlist": net,
        "run": {"T": T, "K": D.K, "checkpointLeaves": len(t2.checkpoint_times(T, D.K)), "inputBitsPerBeat": nl.n_in,
                "outputBits": nl.n_out, "schedule": {"L1": [0, M.BEAT_ACT - 1], "ACT": [M.BEAT_ACT, M.BEAT_ACT],
                                                      "L2": [M.BEAT_L2, M.BEAT_ARGMAX - 1],
                                                      "ARGMAX": [M.BEAT_ARGMAX, M.BEAT_DONE - 1],
                                                      "DONE": [M.BEAT_DONE, T - 1]},
                "digit": {"testIndex": s["digit"], "label": s["label"], "honestClass": s["honest_cls"]},
                "lies": {"class": {"beat": s["where"]["class"][0], "register": s["where"]["class"][1],
                                   "signal": s["where"]["class"][3]},
                         "accumulator": {"beat": s["where"]["acc"][0], "register": s["where"]["acc"][1],
                                         "latchOrdinal": s["where"]["acc"][2], "signal": s["where"]["acc"][3]},
                         "accumulatorMiddleSegment": {"beat": s["where"]["acc_mid"][0],
                                                      "register": s["where"]["acc_mid"][1],
                                                      "signal": s["where"]["acc_mid"][3]}}},
        "verification": em,
        "witness": wit,
        "disputes": disputes,
        "gas": gas_numbers(),
    }
    with open(OUT, "w") as f:
        json.dump(numbers, f, indent=1)
        f.write("\n")
    m = net["metrics"]
    print(f"== {net['file']}: {net['bytes']:,} bytes, {net['chunks']} chunks, netlistHash2 {net['netlistHash2']}")
    print(f"   nIn {m['nIn']}  nOut {m['nOut']}  NAND {m['nand']:,}  LATCH {m['latch']}  ROM records {m['romRecords']} "
          f"({m['romBits']:,} bits: " + ", ".join(f"{r['entries']} x {r['w']}" for r in net["romRecords"]) +
          f")  signals {m['signals']:,}")
    print(f"   gate cost per beat {net['gateCostPerBeat']:,}; {net['segmentsPerBeat']} canonical segments under "
          f"budget {tuple(BUDGET)}:")
    for j, sg in enumerate(net["segments"]):
        print(f"     seg {j}: offset {sg['offset']:6,}  firstSignal {sg['firstSignal']:6,}  records {sg['records']:5,}"
              f"  cost gates={sg['gates']} extWords={sg['extWords']} chunks={sg['chunks']}  (ends: {sg['stop']})")
    print(f"   state list {net['stateListSteps']} step(s), output list {net['outputListSteps']} step(s)")
    print(f"== model: float test accuracy {tr['floatTestAccuracy']:.4f}, integer {int_acc:.4f} "
          f"({tr['split']['nTest']} test digits); netlist vs integer model on {em['digitsCompared']} digits: "
          f"{em['exactMatch']} exact matches ({em['exactMatchRate']:.0%}), netlist accuracy {em['netlistAccuracyOnCompared']:.4f}")
    print(f"== run: T = {T}, K = {D.K}, {len(t2.checkpoint_times(T, D.K))} checkpoint leaves; honest witnesses over "
          f"beats {wit['sampledBeats']}: " + "; ".join(f"seg {x['segment']} {x['min']:,}-{x['max']:,} B (chunks read "
                                                       f"<= {x['chunksReadMax']})" for x in wit["segmentWitnessBytes"]))
    print(f"   state-step witnesses (beat {wit['beat']}): {wit['stateStepWitnessBytes']} B; output step: "
          f"{wit['outputStepWitnessBytes']} B")
    print("== disputes (Python referee) and gas (Foundry):")
    g = numbers["gas"]
    for name, r in disputes.items():
        key = "inference/" + name.split(" ")[0] + "_"
        row = next((v for k, v in g["perScenario"].items() if k.startswith(key)), None)
        fc = next((v for k, v in g["finalCall"].items() if k.startswith(key)), None)
        gas = (f"  gas total {row['gasTotal']:,}, worst move {row['maxMoveGas']:,} ({row['maxMove']})"
               if row else "")
        print(f"   {name}: winner {r['winner']}; rounds {r['totalRounds']} {r['rounds']}; {r['final']}; "
              f"witness {r['witnessBytes']} B{gas}" + (f"; final call {fc['gas']:,}" if fc else ""))
    print(f"   worst move over all inference scenarios: {g['worstMove']['gas']:,} gas ({g['worstMove']['type']}, "
          f"{g['worstMove']['scenario']})")
    print(f"wrote {OUT}  ({time.time() - t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
