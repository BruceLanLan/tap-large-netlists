"""Transaction transcript of the inference demo's disputes for a deployed TapLargeDisputeGame (standard library only).

Plays scenario A (honest claim, accepted after the window) and scenario B (class lie by a gate at beat 90, honest
challenger) of demo/inference/mlp_dispute.py through the SAME mirror referee and the SAME move bodies as
contracts/test/data/game_gen.py (`Setup("inference", ...)`, `single`, `claim_moves`), so the moves are byte for byte
those of contracts/test/data/game_moves.bin that contracts/test/game/E2E.t.sol replays. Every move is then
ABI-encoded into the calldata of the reference game (src/game/TapLargeDisputeGame.sol) and written with its
decoded arguments, sender role and expected effect to transcript.json, which contracts/script/
RunInferenceDispute.s.sol sends to the deployed contracts.

Two values exist only at run time: the claim id (returned by `claim`) and the challenger's address. Both are the
first two words of every dispute move's calldata (uint256 id, address challenger; `accept` and `challenge` take the
id only), so they are encoded as zero here and patched in place by the script (`patch`: 0 none, 1 id, 2 id and
challenger). The deployed game decides the input case of §7.1: both claim variants are recorded (`calldataCaseA`,
inputs in the claim; `calldataCaseB`, `publishInputs` by the input provider first).

    python3 gen_transcript.py           # writes transcript.json next to this file (about 30 s)
    python3 gen_transcript.py --check   # also asserts the moves equal those in contracts/test/data/game_moves.bin
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
GEN_DIR = os.path.join(REPO, "contracts", "test", "data")
sys.path.insert(0, GEN_DIR)
sys.path.insert(0, os.path.join(HERE, ".."))

import game_gen as gg  # noqa: E402  (adds assets/, demo/cartridge, demo/inference to sys.path)
import tapnet2 as t2  # noqa: E402

OUT = os.path.join(HERE, "transcript.json")
NETLIST = os.path.join("demo", "inference", "build", "mlp_digits.tapnet2")
SCENARIOS = {
    "A": ("inference/A_honest_unchallenged", None, "honest claim vs honest checker: no dispute; accepted after the "
          "challenge window"),
    "B": ("inference/B_class_gate_as_claimant", "class", "class lie: a gate feeding the class register flipped in "
          "the last ARGMAX beat (beat 90; the claim says class 0 instead of 1); honest challenger"),
}
ROLE = {gg.CL: "claimant", gg.CH: "challenger", gg.BY: "anyone", gg.PV: "inputProvider"}


# ---------------------------------------------------------------- keccak-256 (selectors; hashlib has only SHA3)
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccak_f(s):
    for rc in _RC:
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        s = [s[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(s[x + 5 * y], _ROT[x][y])
        s = [b[i] ^ ((~b[(i % 5 + 1) % 5 + 5 * (i // 5)]) & b[(i % 5 + 2) % 5 + 5 * (i // 5)]) for i in range(25)]
        s[0] ^= rc
    return s


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data) + b"\x01" + b"\x00" * (rate - 1 - len(data) % rate)
    padded[-1] |= 0x80
    s = [0] * 25
    for off in range(0, len(padded), rate):
        for i in range(rate // 8):
            s[i] ^= struct.unpack_from("<Q", padded, off + 8 * i)[0]
        s = _keccak_f(s)
    return b"".join(struct.pack("<Q", s[i]) for i in range(4))


def selector(signature: str) -> bytes:
    return keccak256(signature.encode())[:4]


# ---------------------------------------------------------------- minimal ABI encoder (the types the game uses)
SEG = ("tuple", ["uint64", "uint32", "uint32", "bytes32", "bytes32", "bytes32"])
END = ("tuple", ["uint32", "bytes32"])
TUPLE_LISTS = ("tuple", ["bytes32"] * 6 + ["bytes32[3]", "uint32[3]", SEG, END, END] + ["bytes32[]"] * 6)
CHALLENGE_ARGS = ("tuple", ["uint32", "bytes32", "bytes32", "bytes32[]", "bytes32", "bytes32[]", "bytes32", "bytes32"])
FUNCTIONS = {
    "publishInputs": ["bytes32", "uint64", "bytes32", "bytes"],
    "claim": ["bytes32", "uint64", "uint64", "bytes32", "bytes32[]", "bytes"],
    "accept": ["uint256"],
    "challenge": ["uint256", CHALLENGE_ARGS],
    "commitBeats": ["uint256", "address", "bytes32", "bytes32[]", "bytes32[]"],
    "revealBeat": ["uint256", "address", "bytes32", "bytes32", "bytes32[]"],
    "commitTuples": ["uint256", "address", "bytes", "bytes32[]", TUPLE_LISTS],
    "revealSeg": ["uint256", "address", SEG, "bytes32[]"],
    "revealEnd": ["uint256", "address", END, "bytes32[]"],
    "proveSegment": ["uint256", "address", SEG, SEG, "bytes"],
    "proveBeatEnd": ["uint256", "address", SEG, END, END, "bytes"],
}


def type_str(t) -> str:
    if isinstance(t, tuple):
        return "(" + ",".join(type_str(x) for x in t[1]) + ")"
    return t


def is_dynamic(t) -> bool:
    if isinstance(t, tuple):
        return any(is_dynamic(x) for x in t[1])
    if t == "bytes" or t.endswith("[]"):
        return True
    if t.endswith("]"):                           # fixed array T[n]
        return is_dynamic(t[:t.rindex("[")])
    return False


def _word(x: int) -> bytes:
    return x.to_bytes(32, "big")


def encode(t, v) -> bytes:
    """ABI encoding of value v of type t (head ‖ tail of a tuple; dynamic types as their tail encoding)."""
    if isinstance(t, tuple):
        return encode_tuple(t[1], v)
    if t == "bytes":
        return _word(len(v)) + v + b"\x00" * (-len(v) % 32)
    if t.endswith("[]"):
        inner = t[:-2]
        return _word(len(v)) + encode_tuple([inner] * len(v), v)
    if t.endswith("]"):
        inner, n = t[:t.rindex("[")], int(t[t.rindex("[") + 1:-1])
        assert len(v) == n
        return encode_tuple([inner] * n, v)
    if t == "bytes32":
        assert len(v) == 32
        return bytes(v)
    if t == "address":
        return _word(int(v))
    if t.startswith("uint"):
        return _word(int(v))
    raise ValueError(t)


def encode_tuple(types, values) -> bytes:
    assert len(types) == len(values)
    heads, tails = [], []
    head_len = sum(32 if is_dynamic(t) else len(encode(t, v)) for t, v in zip(types, values))
    for t, v in zip(types, values):
        e = encode(t, v)
        if is_dynamic(t):
            heads.append(_word(head_len + sum(len(x) for x in tails)))
            tails.append(e)
        else:
            heads.append(e)
    return b"".join(heads) + b"".join(tails)


def calldata(fn: str, values) -> bytes:
    return selector(fn + type_str(("tuple", FUNCTIONS[fn]))) + encode_tuple(FUNCTIONS[fn], values)


# ---------------------------------------------------------------- move bodies (game_gen's layout) -> arguments
class R:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def take(self, n: int) -> bytes:
        v = self.b[self.p:self.p + n]
        assert len(v) == n, "truncated move body"
        self.p += n
        return v

    def u(self, n: int) -> int:
        return int.from_bytes(self.take(n), "big")

    def b32(self) -> bytes:
        return self.take(32)

    def proof(self):
        return [self.b32() for _ in range(self.u(2))]

    def tuple(self):
        return [self.u(8), self.u(4), self.u(4), self.b32(), self.b32(), self.b32()]

    def end(self):
        return [self.u(4), self.b32()]

    def done(self):
        assert self.p == len(self.b), "trailing bytes in move body"


def hx(b) -> str:
    return "0x" + bytes(b).hex()


def J(t, v):
    """JSON form of an argument."""
    if isinstance(t, tuple):
        return [J(x, y) for x, y in zip(t[1], v)]
    if t in ("bytes", "bytes32"):
        return hx(v)
    if t.endswith("]"):
        return [J(t[:t.rindex("[")], x) for x in v]
    return int(v)


def decode_move(mtype: int, body: bytes, nh2: bytes):
    """(function, argument values, expect) of one recorded move; id and challenger are zero placeholders."""
    r = R(body)
    ex = {}
    if mtype == gg.M_PUBLISH:
        T, root = r.u(8), r.b32()
        v = [nh2, T, root, r.take(r.u(4))]
        fn = "publishInputs"
    elif mtype == gg.M_CLAIM:
        T, K, root = r.u(8), r.u(8), r.b32()
        leaves = [r.b32() for _ in range(r.u(2))]
        v = [nh2, T, K, root, leaves, r.take(r.u(4))]
        fn = "claim"
    elif mtype == gg.M_ACCEPT:
        v, fn = [0], "accept"
    elif mtype == gg.M_CHALLENGE:
        v = [0, [r.u(4), r.b32(), r.b32(), r.proof(), r.b32(), r.proof(), r.b32(), r.b32()]]
        fn = "challenge"
    elif mtype == gg.M_COMMIT_BEATS:
        v, fn = [0, 0, r.b32(), r.proof(), r.proof()], "commitBeats"
    elif mtype == gg.M_REVEAL_BEAT:
        ex["ask"] = r.u(8)                        # the beat the game must ask for: (lo + hi) / 2
        v, fn = [0, 0, r.b32(), r.b32(), r.proof()], "revealBeat"
    elif mtype == gg.M_COMMIT_TUPLES:
        packed = r.take(r.u(4))
        in_proof = r.proof()
        x = [r.b32() for _ in range(6)]
        x += [[r.b32() for _ in range(3)], [r.u(4) for _ in range(3)], r.tuple(), r.end(), r.end()]
        x += [r.proof() for _ in range(6)]
        v, fn = [0, 0, packed, in_proof, x], "commitTuples"
    elif mtype in (gg.M_REVEAL_SEG, gg.M_REVEAL_END):
        ex["ask"] = r.u(8)                        # the tuple index the game must ask for
        if mtype == gg.M_REVEAL_SEG:
            v, fn = [0, 0, r.tuple(), r.proof()], "revealSeg"
        else:
            v, fn = [0, 0, r.end(), r.proof()], "revealEnd"
    elif mtype == gg.M_PROVE_SEG:
        a, b = r.tuple(), r.tuple()
        v, fn = [0, 0, a, b, r.take(r.u(4))], "proveSegment"
    elif mtype == gg.M_PROVE_END:
        m, a, b = r.tuple(), r.end(), r.end()
        v, fn = [0, 0, m, a, b, r.take(r.u(4))], "proveBeatEnd"
    else:
        raise ValueError("move type %d is not part of these scenarios" % mtype)
    r.done()
    return fn, v, ex


PATCH = {"accept": 1, "challenge": 1, "claim": 0, "publishInputs": 0}
VALUE = {"claim": "bond", "challenge": "bond"}


def tx_record(n: int, actor: int, mtype: int, body: bytes, nh2: bytes) -> dict:
    fn, v, ex = decode_move(mtype, body, nh2)
    sig = fn + type_str(("tuple", FUNCTIONS[fn]))
    rec = {
        "n": n,
        "role": ROLE[actor],
        "target": "TapLargeDisputeGame",
        "function": fn,
        "signature": sig,
        "selector": hx(selector(sig)),
        "value": VALUE.get(fn, "0"),
        "patch": PATCH.get(fn, 2),
        "args": {"types": [type_str(t) for t in FUNCTIONS[fn]], "values": J(("tuple", FUNCTIONS[fn]), v)},
        "calldata": hx(calldata(fn, v)),
        "expect": ex,
    }
    return rec


def dispute_summary(res, moves):
    """What the mirror predicts for the dispute, in the words of mlp_dispute.py (round counts as bisect_sim.Result:
    commitBeats is the first beat round, the reveal of tuple h the first tuple round) and of the contract's enums."""
    reason = gg.REASONS[res.reason]
    kinds = {0: "seg", 1: "out", 2: "state", 255: "none"}
    counts = {}
    for actor, mtype, _, _ in moves:
        counts[gg.MOVE_NAMES[mtype]] = counts.get(gg.MOVE_NAMES[mtype], 0) + 1
    rounds = {"checkpoint": 1, "beat": 1 + counts.get("revealBeat", 0) // 2, "tuples": 1}
    rounds[kinds[res.kind]] = (counts.get("revealSeg", 0) + counts.get("revealEnd", 0)) // 2
    rounds["verify"] = res.calls
    return {
        "winnerRole": "claimant" if res.winner == gg.CL else "challenger",
        "reason": reason,
        "reasonCode": res.reason,
        "verifierCalls": res.calls,
        "pickedList": kinds[res.kind],
        "pickedListCode": res.kind,
        "rounds": rounds,
        "totalRounds": sum(rounds.values()),
    }


def final_index(txs, kind: int):
    """(lo, hi) of the final call, replayed from the revealed tuples with the game's rules: both reveal tuple h;
    equal there means the longer list justifies h -> h + 1, otherwise the bisection over [0, h] narrows to the
    first differing index."""
    name = "revealSeg" if kind == 0 else "revealEnd"
    reveals = [t for t in txs if t["function"] == name]
    pairs = []
    for k in range(0, len(reveals), 2):
        a, b = reveals[k], reveals[k + 1]
        assert a["role"] == "claimant" and b["role"] == "challenger" and a["expect"]["ask"] == b["expect"]["ask"]
        va, vb = a["args"]["values"][2], b["args"]["values"][2]
        if kind == 0:
            va, vb = va[:5], vb[:5]              # M is not committed for a file without MEM (§4.5)
        pairs.append((a["expect"]["ask"], va == vb))
    h, equal = pairs[0]
    if equal:
        return h, h + 1
    lo, hi = 0, h
    for mid, equal in pairs[1:]:
        assert mid == (lo + hi) // 2
        if equal:
            lo = mid
        else:
            hi = mid
    assert hi - lo == 1
    return lo, hi


def build() -> dict:
    st = gg.Setup("inference", gg.CASE_B)
    nh2 = st.nh2
    data = st.data
    levels = t2.chunk_levels(data)
    ic = st.cheats
    file_len, n_in, n_out, _, _ = st.store.largeInfo(nh2)
    out = {
        "generatedBy": "demo/inference/onchain/gen_transcript.py",
        "sameMovesAs": "contracts/test/data/game_moves.bin (game_gen.py scenarios inference/A_honest_unchallenged "
                       "and inference/B_class_gate_as_claimant, replayed by contracts/test/game/E2E.t.sol)",
        "game": "src/game/TapLargeDisputeGame.sol",
        "netlist": {
            "file": NETLIST,
            "bytes": len(data),
            "sha256": hx(t2.sha256(data)),
            "netlistHash2": hx(nh2),
            "chunkRoot": hx(levels[-1][0]),
            "chunks": t2.n_chunks(len(data)),
            "chunkLeaves": [hx(x) for x in levels[0]],
            "chunkProofs": [[hx(x) for x in t2.merkle_proof(levels, i)] for i in range(len(levels[0]))],
            "nIn": n_in,
            "nOut": n_out,
            "mem": False,
        },
        "budget": {"gates": st.budget.gates, "extWords": st.budget.ext_words, "chunks": st.budget.chunks},
        "run": {"T": len(st.inputs), "K": st.K, "checkpointLeaves": len(t2.checkpoint_times(len(st.inputs), st.K)),
                "digit": "test digit 0 of mlp_model.load_test_digits (label 1)"},
        "scenarios": {},
    }
    for key, (name, cheat, title) in SCENARIOS.items():
        cl = st.party("claimant", ic[cheat] if cheat else None)
        ch = st.party("challenger", None)
        _, T, K, input_root, leaves = cl.claim_data(nh2)
        input_data = b"".join(t2.pack_bits(x) for x in cl.inputs)
        case_a = gg.claim_moves(gg.CASE_A, nh2, cl)
        case_b = gg.claim_moves(gg.CASE_B, nh2, cl)
        assert len(case_a) == 1 and len(case_b) == 2
        # the dispute, exactly as game_gen.single plays it
        moves, disputes, status, res = gg.single(st, name, ic[cheat] if cheat else None, None, None, None, None)
        assert moves[:2] == case_b, "claim moves differ from game_gen's"
        dispute_moves = moves[2:]
        txs = []
        pub = tx_record(0, gg.PV, gg.M_PUBLISH, case_b[0][3], nh2)
        pub["onlyIf"] = "caseB"
        txs.append(pub)
        cla = tx_record(1, gg.CL, gg.M_CLAIM, case_a[0][3], nh2)
        cla["calldataCaseA"] = cla.pop("calldata")
        cla["argsCaseA"] = cla.pop("args")
        clb = tx_record(1, gg.CL, gg.M_CLAIM, case_b[1][3], nh2)
        cla["calldataCaseB"] = clb["calldata"]
        cla["argsCaseB"] = clb["args"]
        cla["expect"] = {"claimant": "claimant", "T": T, "K": K, "inputRoot": hx(input_root),
                         "checkpointRoot": hx(t2.merkle_root(leaves)), "leaves": len(leaves), "status": "Open"}
        txs.append(cla)
        for actor, mtype, target, body in dispute_moves:
            rec = tx_record(len(txs), actor, mtype, body, nh2)
            if mtype == gg.M_ACCEPT:
                rec["expect"] = {"after": "windowEnd", "status": "Accepted", "statusCode": gg.ACCEPTED}
            txs.append(rec)
        sc = {
            "name": name,
            "title": title,
            "claimant": "honest prover" if cheat is None else "cheater (%s gate)" % cheat,
            "cheat": ic[cheat] if cheat else None,
            "inputCase": {"a": "claim carries inputData (inputProvider = 0x0; the game computes inputRoot)",
                          "b": "inputProvider publishes (netlistHash2, T, inputRoot, inputData) first"},
            "claim": {"T": T, "K": K, "inputRoot": hx(input_root), "checkpointRoot": hx(t2.merkle_root(leaves)),
                      "leaves": [hx(x) for x in leaves], "inputData": hx(input_data),
                      "claimedClass": claimed_class(cl, leaves)},
            "finalStatus": ["", "Open", "Rejected", "Accepted"][status],
            "finalStatusCode": status,
            "dispute": dispute_summary(res, dispute_moves) if res is not None else None,
            "nTxs": len(txs),
            "txs": txs,
        }
        if res is not None:
            s = sc["dispute"]
            s["decidedBy"] = "verifySegment" if res.kind == 0 else "verifyBeatEnd"
            last = [t for t in txs if t["function"].startswith("prove")][-1]
            s["finalCallSender"] = last["role"]
            s["beat"] = beat_of(dispute_moves, cl, input_root)
            lo, hi = final_index(txs, res.kind)
            s["index"] = hi
            if res.kind == 0:
                s["finalCall"] = "verify_segment(seg_%d -> seg_%d)" % (lo, hi)
            else:
                s["finalCall"] = "verify_beat_end[%s](%d -> %d)" % (s["pickedList"], lo, hi)
            s["witnessBytes"] = len(bytes.fromhex(last["args"]["values"][-1][2:]))
        out["scenarios"][key] = sc
    return out


def claimed_class(cl, leaves) -> int:
    import mlp_dispute as mi
    return mi.claimed_class(cl, leaves)[0]


def beat_of(moves, cl, input_root: bytes) -> int:
    """The disputed beat t of level 3: the claimant's commitTuples reveals packedInputs_t with a proof against
    inputRoot at index t, so t is the one index at which that proof verifies."""
    ct = next(body for actor, mtype, _, body in moves if mtype == gg.M_COMMIT_TUPLES and actor == gg.CL)
    r = R(ct)
    packed = r.take(r.u(4))
    proof = r.proof()
    T = cl.T
    hits = [t for t in range(T)
            if t2.merkle_verify(input_root, T, t, t2.sha256(bytes([t2.DOM_INPUT]) + t2.u64(t) + t2.sha256(packed)),
                                proof)]
    assert len(hits) == 1, hits
    return hits[0]


# ---------------------------------------------------------------- --check against game_moves.bin
def read_bin(path: str):
    d = open(path, "rb").read()
    p = 0

    def u(n):
        nonlocal p
        v = int.from_bytes(d[p:p + n], "big")
        p += n
        return v
    assert d[:4] == b"TDG3"
    p = 4
    ng = u(1)
    for _ in range(ng):
        ln = u(4)
        p += ln + 13                             # file bytes, budget (3 x u32), input case
    scen = {}
    for _ in range(u(4)):
        ln = u(2)
        name = d[p:p + ln].decode()
        p += ln
        p += 2                                   # game, status
        nd = u(1)
        p += 5 * nd
        moves = []
        for _ in range(u(2)):
            actor, mtype, target = d[p], d[p + 1], d[p + 2]
            p += 3
            ln = u(4)
            moves.append((actor, mtype, target, d[p:p + ln]))
            p += ln
        scen[name] = moves
    assert p == len(d)
    return scen


def check(out: dict) -> None:
    bin_path = os.path.join(GEN_DIR, "game_moves.bin")
    scen = read_bin(bin_path)
    for key, sc in out["scenarios"].items():
        rec = scen[sc["name"]]
        txs = sc["txs"]
        nh2 = bytes.fromhex(out["netlist"]["netlistHash2"][2:])
        assert len(rec) == len(txs), (key, len(rec), len(txs))
        for (actor, mtype, _, body), tx in zip(rec, txs):
            fn, v, _ = decode_move(mtype, body, nh2)
            assert fn == tx["function"] and ROLE[actor] == tx["role"], (key, fn, tx["function"])
            want = hx(calldata(fn, v))
            got = tx["calldataCaseB"] if fn == "claim" else tx["calldata"]
            assert want == got, (key, fn, "calldata differs from game_moves.bin")
    print("check: every move equals its record in", os.path.relpath(bin_path, REPO))


def main(argv) -> int:
    out = build()
    text = json.dumps(out, indent=1, sort_keys=True) + "\n"
    if "--check" in argv:
        check(out)
        if os.path.exists(OUT):
            assert open(OUT).read() == text, "transcript.json is stale: rerun gen_transcript.py"
            print("check: transcript.json is up to date")
    with open(OUT, "w") as f:
        f.write(text)
    for key, sc in out["scenarios"].items():
        d = sc["dispute"]
        tail = ("no dispute" if d is None else "%s wins by %s after %d rounds, %s %s"
                % (d["winnerRole"], d["reason"], d["totalRounds"], d["decidedBy"], "sent by " + d["finalCallSender"]))
        print("%s %-36s %2d txs, final status %s, claimed class %d: %s"
              % (key, sc["name"], sc["nTxs"], sc["finalStatus"], sc["claim"]["claimedClass"], tail))
    print("wrote", os.path.relpath(OUT, REPO), "(%d bytes)" % len(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
