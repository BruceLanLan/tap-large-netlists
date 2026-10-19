"""Generate vectors.json (deterministic, sorted keys) and print its SHA-256.

Usage: python3 make_vectors.py            write vectors.json next to this file
       python3 make_vectors.py --check    compare with the file on disk, exit 1 on mismatch
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

import bisect_sim
import examples
import tapnet2 as t2

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "vectors.json")


def bits(b) -> str:
    return "".join(str(x) for x in b)


def wser(w) -> bytes:
    return t2.serialize_seg_witness(w) if isinstance(w, t2.SegWitness) else t2.serialize_end_witness(w)


def wfields(w) -> dict:
    """The witness of a vector: its full encoding (spec 6.3), SHA-256 and size."""
    ser = wser(w)
    return {"witnessHex": ser.hex(), "witnessSha256": t2.hx(t2.sha256(ser)), "witnessBytes": len(ser)}


_ORACLES: dict = {}


def oracle(data: bytes):
    """(store, netlistHash2) of a store holding exactly this file: the chunk oracle of the vectors."""
    if data not in _ORACLES:
        _ORACLES[data] = t2.oracle_for(data)
    return _ORACLES[data]


# ---------------------------------------------------------------- beat contexts

def contexts():
    """(name, file, nl, state, inputs, label) — the beat each segmentation and witness vector is about."""
    out = []
    for name, x in (("sbox", 0x53), ("mul4", 0xB7)):
        data = examples.EXAMPLES[name]()
        out.append((name, data, t2.decode(data), [], [(x >> i) & 1 for i in range(8)], f"inputs=0x{x:02x}"))
    data = examples.message_file()
    nl = t2.decode(data)
    st = [0] * nl.n_latch
    for _ in range(12):
        _, st = t2.step(nl, st, [1])
    out.append(("message", data, nl, st, [1], "beat 12 of the enable=1 run"))
    data = examples.large_file()
    nl = t2.decode(data)
    rng = examples.XorShift(0x1A26E)
    ins = [rng.bits(16) for _ in range(6)]
    st = [0] * nl.n_latch
    for t in range(3):
        _, st = t2.step(nl, st, ins[t])
    out.append(("large", data, nl, st, ins[3], "beat 3 of the dispute-simulation inputs"))
    return out


# ---------------------------------------------------------------- witness mutations

def seg_mutations(nl, V, state, tuples, j, budget):
    """Negative cases for the step tuples[j-1] -> tuples[j]. Each: (name, a, b, witness, budget, stateRoot)."""
    a, b = tuples[j - 1], tuples[j]
    w = t2.build_seg_witness(nl, V, state, a, b.byte_offset)
    sr = t2.state_root(state)
    cases = []
    flip = lambda h: bytes([h[0] ^ 1]) + h[1:]  # noqa: E731

    cases.append(("claimed P wrong", a, b._replace(p=flip(b.p)), w, budget, sr))
    cases.append(("claimed D wrong", a, b._replace(d=flip(b.d)), w, budget, sr))
    cases.append(("claimed firstSignal wrong", a, b._replace(first_signal=b.first_signal + 1), w, budget, sr))
    cases.append(("claimed latchOrdinal wrong", a, b._replace(latch_ordinal=b.latch_ordinal + 1), w, budget, sr))
    # non-maximal: one record shorter, with P/D/offsets honestly computed for the shorter range
    read, flen = t2._file_reader(nl.data), len(nl.data)
    seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, budget)
    if len(seg.records) >= 2:
        raw, s, l = seg.records[-1]
        short = t2.tuples_for_boundaries(nl, V, [(a.byte_offset, a.first_signal, a.latch_ordinal), (raw.off, s, l)])[1]
        ws = t2.build_seg_witness(nl, V, state, a, raw.off)
        cases.append(("non-maximal segment (one record short)", a, short, ws, budget, sr))
    if seg.next_record is not None:
        raw = seg.next_record
        bl = (raw.end, seg.end_signal + raw.n_out, seg.end_latch + (raw.op == t2.OP_LATCH))
        long_ = t2.tuples_for_boundaries(nl, V, [(a.byte_offset, a.first_signal, a.latch_ordinal), bl])[1]
        wl = t2.build_seg_witness(nl, V, state, a, raw.end)
        cases.append(("over budget (one record long)", a, long_, wl, budget, sr))
    if w.p_words:
        m = copy.deepcopy(w)
        k = min(m.p_words)
        m.p_words[k] = flip(m.p_words[k])
        cases.append(("P word tampered", a, b, m, budget, sr))
        m = copy.deepcopy(w)
        m.p_words.pop(min(m.p_words))
        cases.append(("P word missing", a, b, m, budget, sr))
    if w.p_peaks or w.p_partial:
        m = copy.deepcopy(w)
        if m.p_partial is not None:
            m.p_partial = flip(m.p_partial)
        else:
            m.p_peaks[0] = flip(m.p_peaks[0])
        cases.append(("P frontier tampered", a, b, m, budget, sr))
    if w.s_words:
        m = copy.deepcopy(w)
        k = min(m.s_words)
        m.s_words[k] = flip(m.s_words[k])
        cases.append(("state word tampered", a, b, m, budget, sr))
        cases.append(("wrong stateRoot", a, b, w, budget, flip(sr)))
        m = copy.deepcopy(w)
        m.s_size += 1
        cases.append(("state tree size wrong", a, b, m, budget, sr))
        cases.append(("stateRoot EMPTY with state words in the witness", a, b, w, budget, t2.EMPTY))
    for alt in (budget._replace(gates=budget.gates + 1), budget._replace(gates=budget.gates - 1),
                budget._replace(ext_words=budget.ext_words - 1), budget._replace(chunks=budget.chunks - 1),
                budget._replace(chunks=budget.chunks + 1), budget._replace(gates=2 * budget.gates)):
        alt_seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, alt)
        if alt_seg.end_off != b.byte_offset:
            cases.append((f"verified under a different budget {tuple(alt)}", a, b, w, alt, sr))
            break
    return cases


def rom_data_only_case(nl, V, state, tuples, budget):
    """A segment with a ROM whose addressed data lies in a chunk that no structural byte of the step needs (the
    verifier must fetch that chunk from the oracle). Returns (j, a, b, chunks read) or None."""
    read, flen = t2._file_reader(nl.data), len(nl.data)
    for j in range(1, len(tuples)):
        a, b = tuples[j - 1], tuples[j]
        seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, budget)
        struct = set()
        for r, _, _ in seg.records:
            struct |= t2.chunk_set(r.off, r.struct_end)
        if seg.next_record is not None:
            struct |= t2.chunk_set(seg.next_record.off, seg.next_record.struct_end)
        for r, _, _ in seg.records:
            if r.op == t2.OP_ROM:
                addr = sum(V[x] << i for i, x in enumerate(r.refs))
                if t2.chunk_set(*t2._rom_addressed_range(r, addr)) - struct:
                    return j, a, b
    return None


def straddling_next_record_case(nl, V, tuples, budget):
    """A step stopped by the budget whose next record straddles a chunk boundary (its structural bytes are
    read from two chunks). Returns (j, a, b) or None."""
    read, flen = t2._file_reader(nl.data), len(nl.data)
    for j in range(1, len(tuples)):
        a, b = tuples[j - 1], tuples[j]
        seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, budget)
        nr = seg.next_record
        if nr is not None and len(t2.chunk_set(nr.off, nr.struct_end)) > 1:
            return j, a, b
    return None


def rom_search_context(nl, budget, seeds=range(64)):
    """A deterministic (state, inputs) under which some ROM's addressed data lies in a data-only chunk."""
    for seed in seeds:
        rng = examples.XorShift(0xD00D + seed)
        state, ins = rng.bits(nl.n_latch), rng.bits(nl.n_in)
        V, _, _ = t2.evaluate(nl, state, ins)
        tuples = t2.segment_tuples(nl, V, budget)
        rd = rom_data_only_case(nl, V, state, tuples, budget)
        if rd:
            return seed, state, ins, rd
    return None


def store_vectors() -> dict:
    """beginLarge length bounds and hash checks, and putChunk behaviour, on the reference store (spec 3)."""
    root = t2.sha256(b"any chunk root")
    cases = []
    for length in (16, 17, t2.MAX_FILE, t2.MAX_FILE + 1):
        h = t2.nh2_from(length, root)
        st = t2.LargeStore([h])
        try:
            st.beginLarge(h, length, root)
            res = "ok"
        except t2.StoreError as e:
            res = e.name
        cases.append({"call": "beginLarge", "netlistHash2": t2.hx(h), "length": length, "chunkRoot": t2.hx(root),
                      "known": True, "expect": res, "largeInfo": list(st.largeInfo(h))})
    h = t2.nh2_from(17, root)
    for name, known, hh, length in (("unknown netlist hash", False, h, 17),
                                    ("length does not match the hash", True, h, 18)):
        st = t2.LargeStore([hh] if known else [])
        try:
            st.beginLarge(hh, length, root)
            res = "ok"
        except t2.StoreError as e:
            res = e.name
        cases.append({"call": "beginLarge", "case": name, "netlistHash2": t2.hx(hh), "length": length,
                      "chunkRoot": t2.hx(root), "known": known, "expect": res})
    data = examples.large_file()
    lv = t2.chunk_levels(data)
    h = t2.netlist_hash2(data)
    st = t2.LargeStore([h])
    try:
        st.putChunk(h, 0, t2.split_chunks(data)[0], t2.chunk_proof(data, 0, lv))
        before_begin = "ok"
    except t2.StoreError as e:
        before_begin = e.name
    st.beginLarge(h, len(data), lv[-1][0])
    order = [5, 2, 0, 0, 4, 1, 3]
    infos = []
    for i in order:
        st.putChunk(h, i, t2.split_chunks(data)[i], t2.chunk_proof(data, i, lv))
        infos.append(list(st.largeInfo(h)))
    bad = t2.split_chunks(data)[1]
    try:
        st.putChunk(h, 1, bad[:-1] + bytes([bad[-1] ^ 1]), t2.chunk_proof(data, 1, lv))
        tampered = "ok"
    except t2.StoreError as e:
        tampered = e.name
    upload = {"example": "large", "netlistHash2": t2.hx(h), "putChunkBeforeBegin": before_begin,
              "order": order, "largeInfoAfterEach": infos, "events": [[n, list(a)] for n, a in st.events],
              "tamperedChunk": tampered}
    return {"beginLarge": cases, "upload": upload}


# ---------------------------------------------------------------- build

def build_vectors() -> dict:
    v: dict = {}
    v["meta"] = {
        "description": "Test vectors for the draft TAP 'Large Netlists and Verifiable Execution' (tapnet/2). "
                       "Bit strings list bit i at position i. Tuples: [byteOffset, firstSignal, latchOrdinal, P, D]; "
                       "beat-end tuples: [ordinal, root]. Witnesses are encoded as in section 6.3 of the TAP; "
                       "the verifiers read the file through a chunk oracle, here a store holding the file of "
                       "'example' (oracle 'file'), an empty store ('none') or a store holding chunk 0 only "
                       "('chunk0').",
        "chunkSize": t2.CHUNK,
        "domains": {"node": 1, "chunkLeaf": 2, "netlistHash2": 3, "signalWord": 4, "stateWord": 5,
                    "checkpointLeaf": 6, "tupleLeaf": 7, "ioChain": 8, "latchDWord": 9, "inputLeaf": 10},
        "defaultBudget": t2.DEFAULT_BUDGET._asdict(),
        "tinyBudget": t2.TINY_BUDGET._asdict(),
        "empty": t2.hx(t2.EMPTY),
    }

    # files, metrics, chunking
    files = {}
    for name, fn in examples.EXAMPLES.items():
        data = fn()
        lv = t2.chunk_levels(data)
        entry = {
            "hex": data.hex(), "sha256": t2.hx(t2.sha256(data)), "metrics": t2.check_file(data),
            "chunks": len(lv[0]), "chunkLeaves": [t2.hx(x) for x in lv[0]], "chunkRoot": t2.hx(lv[-1][0]),
            "netlistHash2": t2.hx(t2.netlist_hash2(data)),
        }
        if len(lv[0]) > 1:
            entry["chunkProofs"] = [[t2.hx(x) for x in t2.chunk_proof(data, i, lv)] for i in range(len(lv[0]))]
        if name != "large":
            exp = t2.expand_rom_to_tapnet1(data)
            nl1 = t2.decode(exp, (t2.VERSION1,))
            entry["expansion"] = {"sha256": t2.hx(t2.sha256(exp)), "bytes": len(exp), "nand": nl1.nand,
                                  "latch": nl1.n_latch}
        files[name] = entry
    v["files"] = files

    v["invalid"] = []
    for name, data, reason in examples.invalid_files():
        try:
            t2.check_file(data)
            raise AssertionError(f"invalid sample {name} accepted")
        except t2.Invalid as e:
            assert reason in str(e), (name, str(e))
        v["invalid"].append({"name": name, "hex": data.hex(), "reason": reason})
    v["invalidNotInHex"] = [
        {"name": "file_larger_than_64_MiB", "construction": "valid header, 64 MiB + 1 bytes in total",
         "reason": "file larger than 64 MiB"},
        {"name": "more_than_2^24_signals", "construction": "nIn=1, nOut=1, 65,536 ROM records a=1, w=256 "
         "(addr = signal 2, data 64 zero bytes)", "reason": "more than 2^24 signals"},
    ]

    # step results
    steps = {}
    sbox, mul4 = examples.sbox_file(), examples.mul4_file()
    steps["sbox"] = []
    for x in (0x00, 0x01, 0x53, 0xFF):
        o, ns = t2.step(sbox, [], [(x >> i) & 1 for i in range(8)])
        steps["sbox"].append({"inputs": bits([(x >> i) & 1 for i in range(8)]), "outputs": bits(o), "newState": bits(ns),
                              "x": x, "y": sum(b << i for i, b in enumerate(o))})
    steps["mul4"] = []
    for x, y in ((0, 0), (3, 5), (15, 15), (7, 9)):
        k = x | (y << 4)
        o, ns = t2.step(mul4, [], [(k >> i) & 1 for i in range(8)])
        steps["mul4"].append({"inputs": bits([(k >> i) & 1 for i in range(8)]), "outputs": bits(o), "newState": bits(ns),
                              "x": x, "y": y, "product": sum(b << i for i, b in enumerate(o))})
    msg = t2.decode(examples.message_file())
    st, trace = [0] * msg.n_latch, []
    for t in range(40):
        o, ns = t2.step(msg, st, [1])
        trace.append({"t": t, "state": bits(st), "inputs": "1", "outputs": bits(o), "newState": bits(ns),
                      "char": sum(b << i for i, b in enumerate(o))})
        st = ns
    steps["message"] = trace
    steps["messageText"] = bytes(e["char"] for e in trace if e["char"]).decode()
    large = t2.decode(examples.large_file())
    rng = examples.XorShift(0x1A26E)
    st, lt = [0] * large.n_latch, []
    for t in range(6):
        ins = rng.bits(16)
        o, ns = t2.step(large, st, ins)
        lt.append({"t": t, "state": bits(st), "inputs": bits(ins), "outputs": bits(o), "newState": bits(ns)})
        st = ns
    steps["large"] = lt
    v["step"] = steps

    # segmentations, segment and beat-end verification (the chunk oracle is a store holding the file)
    segs, vseg, vend = {}, [], []

    def seg_case(example, budget_name, j, a, b, sroot, w, expect, case, budget, store_note="file", **extra):
        e = {"example": example, "budget": budget_name, "j": j, "a": a.as_json(), "b": b.as_json(),
             "stateRoot": t2.hx(sroot), "oracle": store_note, "expect": expect, "case": case,
             "budgetUsed": budget._asdict()}
        e.update(wfields(w))
        e.update(extra)
        vseg.append(e)

    for name, data, nl, state, ins, label in contexts():
        V, outs, ns = t2.evaluate(nl, state, ins)
        store, nh2 = oracle(data)
        sr = t2.state_root(state)
        segs[name] = {"context": {"label": label, "state": bits(state), "inputs": bits(ins), "stateRoot": t2.hx(sr)}}
        for bname, budget in (("default", t2.DEFAULT_BUDGET), ("tiny", t2.TINY_BUDGET)):
            tuples = t2.segment_tuples(nl, V, budget)
            read, flen = t2._file_reader(data), len(data)
            costs = [t2.canonical_segment(read, flen, tp.byte_offset, tp.first_signal, tp.latch_ordinal, budget)
                     for tp in tuples[:-1]]
            segs[name][bname] = {
                "segments": len(tuples) - 1,
                "boundaries": [[tp.byte_offset, tp.first_signal, tp.latch_ordinal] for tp in tuples],
                "tuples": [tp.as_json() for tp in tuples] if len(tuples) <= 64 else None,
                "tuplesRoot": t2.hx(t2.merkle_root([t2.seg_tuple_leaf(j, tp) for j, tp in enumerate(tuples)])),
                "costs": [dict(c.cost.as_json(), stop=c.stop, records=len(c.records)) for c in costs]
                if len(costs) <= 64 else None,
            }
            js = list(range(1, len(tuples)))
            if len(js) > 12:
                js = js[:8] + js[-4:]
            for j in js:
                a, b = tuples[j - 1], tuples[j]
                w = t2.build_seg_witness(nl, V, state, a, b.byte_offset)
                ok = t2.verify_segment(store, nh2, sr, a, b, w, budget)
                assert ok, (name, bname, j)
                seg_case(name, bname, j, a, b, sr, w, True, "honest", budget, chunksRead=t2.LAST_READ["chunks"])
            if name in ("message", "large"):
                jneg = 1 if bname == "default" else min(3, len(tuples) - 1)
                for case, a, b, w, bud, sroot in seg_mutations(nl, V, state, tuples, jneg, budget):
                    ok = t2.verify_segment(store, nh2, sroot, a, b, w, bud)
                    assert not ok, (name, bname, case)
                    seg_case(name, bname, jneg, a, b, sroot, w, False, case, bud)
                a, b = tuples[jneg - 1], tuples[jneg]
                w = t2.build_seg_witness(nl, V, state, a, b.byte_offset)
                empty_store = t2.LargeStore([nh2])
                assert not t2.verify_segment(empty_store, nh2, sr, a, b, w, budget)
                seg_case(name, bname, jneg, a, b, sr, w, False, "file not stored in the oracle", budget, "none")
                st_part = t2.LargeStore([nh2])
                lv = t2.chunk_levels(data)
                st_part.beginLarge(nh2, len(data), lv[-1][0])
                st_part.putChunk(nh2, 0, t2.split_chunks(data)[0], t2.chunk_proof(data, 0, lv))
                if t2.n_chunks(len(data)) > 1:
                    assert not t2.verify_segment(st_part, nh2, sr, a, b, w, budget)
                    seg_case(name, bname, jneg, a, b, sr, w, False, "file only partly stored (chunk 0 only)",
                             budget, "chunk0")
            if name == "large":
                found = rom_search_context(nl, budget)
                assert found, "no ROM data-only chunk context found"
                seed, st2, in2, (j, a, b) = found
                sr2 = t2.state_root(st2)
                V2, _, _ = t2.evaluate(nl, st2, in2)
                w = t2.build_seg_witness(nl, V2, st2, a, b.byte_offset)
                assert t2.verify_segment(store, nh2, sr2, a, b, w, budget)
                seg_case(name, bname, j, a, b, sr2, w, True, "honest, ROM data read from a data-only chunk", budget,
                         chunksRead=t2.LAST_READ["chunks"],
                         context={"state": bits(st2), "inputs": bits(in2), "seed": seed})
        # beat end
        seg_m = t2.segment_tuples(nl, V)[-1]
        for kind, lst in (("out", t2.out_tuples(outs)), ("state", t2.state_tuples(ns))):
            segs[name][kind + "Tuples"] = [e.as_json() for e in lst]
            for k in range(1, len(lst)):
                w = (t2.build_out_witness(nl, V, outs, k - 1) if kind == "out"
                     else t2.build_state_witness(nl, V, ns, k - 1))
                a, b = lst[k - 1], lst[k]
                assert t2.verify_beat_end(store, nh2, nl.n_out, seg_m, a, b, w)
                e = {"example": name, "kind": kind, "k": k, "nOut": nl.n_out, "segM": seg_m.as_json(),
                     "a": a.as_json(), "b": b.as_json(), "oracle": "file", "expect": True, "case": "honest"}
                e.update(wfields(w))
                vend.append(e)
                for case, bb, ww, sm, n_out in end_mutations(kind, a, b, w, seg_m, nl.n_out):
                    assert not t2.verify_beat_end(store, nh2, n_out, sm, a, bb, ww), (name, kind, case)
                    e = {"example": name, "kind": kind, "k": k, "nOut": n_out, "segM": sm.as_json(),
                         "a": a.as_json(), "b": bb.as_json(), "oracle": "file", "expect": False, "case": case}
                    e.update(wfields(ww))
                    vend.append(e)

    # the initial state: stateRoot_0 = EMPTY means "every LATCH reads 0" (beat 0 of the message run)
    data = examples.message_file()
    nl = t2.decode(data)
    store, nh2 = oracle(data)
    zero = [0] * nl.n_latch
    V, _, _ = t2.evaluate(nl, zero, [1])
    for bname, budget in (("default", t2.DEFAULT_BUDGET), ("tiny", t2.TINY_BUDGET)):
        tuples = t2.segment_tuples(nl, V, budget)
        a, b = tuples[0], tuples[1]
        w = t2.build_seg_witness(nl, V, zero, a, b.byte_offset, t2.EMPTY)
        assert w.s_size == 0 and not w.s_words
        assert t2.verify_segment(store, nh2, t2.EMPTY, a, b, w, budget)
        seg_case("message", bname, 1, a, b, t2.EMPTY, w, True, "beat 0: stateRoot EMPTY, LATCHes read 0", budget,
                 context={"state": "all zero (beat 0)", "inputs": "1"})
        wz = t2.build_seg_witness(nl, V, zero, a, b.byte_offset, t2.state_root(zero))
        assert wz.s_words
        assert t2.verify_segment(store, nh2, t2.state_root(zero), a, b, wz, budget)
        assert not t2.verify_segment(store, nh2, t2.EMPTY, a, b, wz, budget)
        seg_case("message", bname, 1, a, b, t2.EMPTY, wz, False,
                 "beat 0: stateRoot EMPTY but the witness carries state words", budget)
        assert not t2.verify_segment(store, nh2, t2.state_root(zero), a, b, w, budget)
        seg_case("message", bname, 1, a, b, t2.state_root(zero), w, False,
                 "stateRoot of the zero state written out (not EMPTY) and no state words", budget)
    v["segmentations"] = segs
    v["verifySegment"] = vseg
    v["verifyBeatEnd"] = vend

    # checkpoints and inputs (message example, T = 40, K = 8)
    data, nl, inputs, K, budget, _ = bisect_sim.scenario_setup("message")
    p = bisect_sim.Party("honest", nl, inputs, K, budget)
    v["checkpoints"] = {
        "example": "message", "T": p.T, "K": K, "times": p.cp_ts, "initialLeaf": t2.hx(t2.INITIAL_LEAF),
        "leaves": [{"t": t, "stateRoot": t2.hx(p.sroots[t]), "ioHash": t2.hx(p.io[t]),
                    "leaf": t2.hx(t2.checkpoint_leaf(*p.leaf(t)))} for t in p.cp_ts],
        "checkpointRoot": t2.hx(t2.merkle_root(p.cp_leaves)),
        "inputs": {"packed": [t2.pack_bits(x).hex() for x in inputs],
                   "leaves": [t2.hx(t2.input_leaf(t, x)) for t, x in enumerate(inputs)],
                   "inputRoot": t2.hx(t2.input_root(inputs))},
    }
    v["store"] = store_vectors()
    d = bisect_sim.run_scenarios()
    d.update(bisect_sim.checkpoint_position_scenarios())
    d.update(bisect_sim.wrong_index_scenarios())
    v["disputes"] = d
    v["measurements"] = measurements()
    return v


def end_mutations(kind, a, b, w, seg_m, n_out):
    flip = lambda h: bytes([h[0] ^ 1]) + h[1:]  # noqa: E731
    out = [("claimed root wrong", b._replace(root=flip(b.root)), w, seg_m, n_out),
           ("claimed ordinal wrong", b._replace(ordinal=b.ordinal - 1), w, seg_m, n_out),
           ("P_m wrong", b, w, seg_m._replace(p=flip(seg_m.p)), n_out)]
    m = copy.deepcopy(w)
    k = min(m.p_words)
    m.p_words[k] = flip(m.p_words[k])
    out.append(("P word tampered", b, m, seg_m, n_out))
    if kind == "out":
        out.append(("wrong nOut parameter", b, w, seg_m, n_out + 1))
    else:
        m = copy.deepcopy(w)
        k = min(m.d_words)
        m.d_words[k] = flip(m.d_words[k])
        out.append(("latch d-table word tampered", b, m, seg_m, n_out))
        out.append(("D_m wrong", b, w, seg_m._replace(d=flip(seg_m.d)), n_out))
    out.append(("seg_m not final", b, w, seg_m._replace(byte_offset=seg_m.byte_offset - 1), n_out))
    return out


def measurements() -> dict:
    res = {"bounds": {"segmentWitness": t2.seg_witness_bound(t2.DEFAULT_BUDGET),
                      "beatEndWitness": t2.end_witness_bound(), "witnessCap": t2.WITNESS_CAP}}
    for name, fn, budget in (("large", examples.large_file, t2.DEFAULT_BUDGET),
                             ("nandOnly21000", examples.nand_only_file, t2.DEFAULT_BUDGET)):
        data = fn()
        nl = t2.decode(data)
        store, nh2 = oracle(data)
        rng = examples.XorShift(0xBEEF)
        state = rng.bits(nl.n_latch)
        ins = rng.bits(nl.n_in)
        V, outs, ns = t2.evaluate(nl, state, ins)
        tuples = t2.segment_tuples(nl, V, budget)
        read, flen = t2._file_reader(data), len(data)
        rows = []
        for j in range(1, len(tuples)):
            a, b = tuples[j - 1], tuples[j]
            seg = t2.canonical_segment(read, flen, a.byte_offset, a.first_signal, a.latch_ordinal, budget)
            w = t2.build_seg_witness(nl, V, state, a, b.byte_offset)
            assert t2.verify_segment(store, nh2, t2.state_root(state), a, b, w, budget)
            rows.append(dict(j=j, stop=seg.stop, records=len(seg.records), cost=seg.cost.as_json(),
                             chunksRead=t2.LAST_READ["chunks"], witness=t2.witness_breakdown(w)))
        ow = t2.build_out_witness(nl, V, outs, 0)
        entry = {"bytes": len(data), "chunks": t2.n_chunks(len(data)), "segments": len(tuples) - 1,
                 "sha256": t2.hx(t2.sha256(data)), "netlistHash2": t2.hx(t2.netlist_hash2(data)),
                 "perSegment": rows, "outWitnessBytes": len(wser(ow))}
        if nl.n_latch:
            entry["stateWitnessBytes"] = len(wser(t2.build_state_witness(nl, V, ns, 0)))
        res[name] = entry
    return res


def render(v: dict) -> bytes:
    return (json.dumps(v, sort_keys=True, indent=1) + "\n").encode()


def main(argv) -> int:
    blob = render(build_vectors())
    digest = hashlib.sha256(blob).hexdigest()
    if "--check" in argv:
        with open(OUT, "rb") as f:
            same = f.read() == blob
        print(("vectors.json matches" if same else "vectors.json DIFFERS"), "sha256:", digest)
        return 0 if same else 1
    with open(OUT, "wb") as f:
        f.write(blob)
    print(f"wrote {OUT} ({len(blob)} bytes)")
    print("sha256:", digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
