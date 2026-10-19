"""Informal simulation of the dispute game of spec section 7 for large netlists.

Claim = (netlistHash2, T, K, inputRoot, checkpointRoot). The claimant publishes the full list of checkpoint
leaves (0x06, at t = 0, K, 2K, ..., T); the referee derives checkpointRoot from that list, so the list and the
root cannot disagree. The inputs of every beat are committed by inputRoot (leaves 0x0A) and published.
stateRoot_0 = EMPTY (the all-zero state), so the first leaf is the constant INITIAL_LEAF.

The referee holds only on-chain data: the store (which is the chunk oracle of the verifiers and gives
nIn/nOut through largeInfo), the budget, the claim tuple and the published leaf list. It never looks at a
party's private values; parties act only through their moves.

Levels:
  1. the challenger compares its own leaves with the published list and names the first index i whose leaf
     differs, posting its own leaf i; it thereby accepts leaf i-1. If its leaf equals the published leaf i,
     the challenger loses.
  2. both commit the beat leaves of [t_{i-1}, t_i] (the size is fixed by the referee); bisection over beats.
  3. the claimant reveals the inputs of the disputed beat against inputRoot; both commit the beat's tuple
     lists (segments 0x07/0, outputs 0x07/1, new state 0x07/2); reveal checks tie the end tuples to the beat
     leaves; bisection over the first list whose last tuple differs.
  4. exactly one call to verify_segment or verify_beat_end decides.

Rounds are counted as the on-chain game (contracts/src/game) sequences them: checkpoint 1 (the challenge);
beat 1 (commit) + one per bisection step; tuples 1 (inputs and tuple lists); <list> 1 (both reveal tuple h)
+ one per bisection step, or + 1 for the longer list's reveal of tuple h + 1; verify 1 (the final call).

Cheaters are deterministic faulty simulators that stay internally consistent (they recompute everything
downstream of their lie), so that the dispute must reach the single-step verifier.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Tuple

import examples
import tapnet2 as t2


class Lose(Exception):
    def __init__(self, who: str, why: str):
        super().__init__(f"{who}: {why}")
        self.who, self.why = who, why


class ClaimRejected(Exception):
    """The game refuses the claim itself (no dispute takes place)."""


class Claim(NamedTuple):
    netlist_hash2: bytes
    T: int
    K: int
    input_root: bytes
    checkpoint_root: bytes


def _bounds_noncanonical(nl: t2.Netlist, budget: t2.Budget, mode: str):
    """Non-canonical segmentations: 'short' ends segment 1 one record early, 'long' one record late."""
    canon = t2.segment_boundaries(nl, budget)
    read, flen = t2._file_reader(nl.data), len(nl.data)
    seg = t2.canonical_segment(read, flen, *canon[0], budget)
    recs = seg.records
    if mode == "short":
        if len(recs) < 2:
            raise ValueError("first segment has one record; cannot shorten")
        raw, s, l = recs[-1]
        b1 = (raw.off, s, l)
    else:
        if seg.next_record is None:
            raise ValueError("first segment ends at EOF; cannot lengthen")
        raw = seg.next_record
        b1 = (raw.end, seg.end_signal + raw.n_out, seg.end_latch + (raw.op == t2.OP_LATCH))
    return [canon[0]] + t2.greedy_boundaries(nl, b1, budget)


def _flip(h: bytes) -> bytes:
    return bytes([h[0] ^ 0xFF]) + h[1:]


class Party:
    """A claimant or challenger. cheat = None (honest) or a dict:
      {"kind": "gate"|"rom", "t": beat, "signal": index}                 flip one signal in beat t
      {"kind": "state", "t": beat, "latch": ordinal}                     flip one new-state bit after beat t
      {"kind": "checkpoint", "t": checkpoint time}                       forge stateRoot in that checkpoint leaf
      {"kind": "empty_state", "t": time >= 1}                            claim stateRoot_t = EMPTY
      {"kind": "segmentation", "mode": "short"|"long", "t", "signal"}    non-canonical boundaries + a gate lie
      {"kind": "eof_extra", "t", "signal": an output signal}            honest values, lied output, and an extra
                                                                         segment tuple after EOF with a fake P_m
      {"kind": "index", "i": i}                                          (challenger) name checkpoint index i
                                                                         whatever the leaves say
    and, for a claimant, optionally "inputs": "withhold" | "wrong" (at level 3, reveal no inputs, or other
    input bits than those committed by inputRoot).
    """

    def __init__(self, name: str, nl: t2.Netlist, inputs: List[List[int]], K: int, budget: t2.Budget,
                 cheat: Optional[dict] = None):
        self.name, self.nl, self.inputs, self.K, self.budget, self.cheat = name, nl, inputs, K, budget, cheat
        self.T = len(inputs)
        self.states = [[0] * nl.n_latch]
        self.io = [t2.EMPTY]
        for t in range(self.T):
            _, outs, ns = self._eval(t)
            self.states.append(ns)
            self.io.append(t2.io_next(self.io[-1], t2.out_root(outs)))
        self.sroots = t2.beat_state_roots(self.states)
        self.cp_ts = t2.checkpoint_times(self.T, K)
        self.cp_leaves = [t2.checkpoint_leaf(*self.leaf(t)) for t in self.cp_ts]
        self.in_levels = t2.input_levels(inputs)
        if cheat and cheat["kind"] == "segmentation":
            self.bounds = _bounds_noncanonical(nl, budget, cheat["mode"])
        else:
            self.bounds = t2.segment_boundaries(nl, budget)
        self._details: Dict[int, dict] = {}

    # ---- own computation
    def _eval(self, t: int):
        c = self.cheat or {}
        flips = {c["signal"]} if c.get("kind") in ("gate", "rom", "segmentation") and c["t"] == t else set()
        sflip = {c["latch"]} if c.get("kind") == "state" and c["t"] == t else set()
        V, outs, ns = t2.evaluate(self.nl, self.states[t], self.inputs[t], flips, sflip)
        if c.get("kind") == "eof_extra" and c["t"] == t:   # honest values, lied outputs
            outs = list(outs)
            outs[c["signal"] - (self.nl.n_signals - self.nl.n_out)] ^= 1
        return V, outs, ns

    def leaf(self, t: int) -> Tuple[int, bytes, bytes]:
        sr = self.sroots[t]
        c = self.cheat or {}
        if c.get("kind") == "checkpoint" and c["t"] == t:
            sr = _flip(sr)
        if c.get("kind") == "empty_state" and c["t"] == t:
            sr = t2.EMPTY
        return t, sr, self.io[t]

    # ---- as claimant: the claim and its published data
    def claim_data(self, nh2: bytes):
        """(netlistHash2, T, K, inputRoot, published checkpoint leaves)."""
        return nh2, self.T, self.K, self.in_levels[-1][0], list(self.cp_leaves)

    def reveal_inputs(self, t: int):
        how = (self.cheat or {}).get("inputs")
        if how == "withhold":
            return None
        packed = t2.pack_bits(self.inputs[t])
        if how == "wrong":
            packed = bytes([packed[0] ^ 1]) + packed[1:]
        return packed, t2.merkle_proof(self.in_levels, t)

    # ---- as challenger: level 1 from public data only
    def choose_index(self, published: List[bytes]) -> Optional[int]:
        c = self.cheat or {}
        if c.get("kind") == "index":
            return c["i"]
        for i, (mine, theirs) in enumerate(zip(self.cp_leaves, published)):
            if mine != theirs:
                return i
        return None

    def posted_leaf(self, i: int) -> Tuple[int, bytes, bytes]:
        return self.leaf(self.cp_ts[i])

    # ---- level 2: beats of an interval
    def commit_interval(self, t_lo: int, t_hi: int) -> bytes:
        self._iv = (t_lo, t2.merkle_levels([t2.checkpoint_leaf(*self.leaf(t)) for t in range(t_lo, t_hi + 1)]))
        return self._iv[1][-1][0]

    def reveal_beat(self, t: int):
        t_lo, lv = self._iv
        return self.leaf(t), t2.merkle_proof(lv, t - t_lo)

    # ---- level 3: tuples of one beat
    def details(self, t: int) -> dict:
        if t not in self._details:
            V, outs, ns = self._eval(t)
            seg = t2.tuples_for_boundaries(self.nl, V, self.bounds)
            out = t2.out_tuples(outs)
            st = t2.state_tuples(ns)
            claimed = self.leaf(t + 1)[1]
            if claimed != st[-1].root:        # forger: keep the reveal consistent with the forged leaf
                st[-1] = t2.EndTuple(st[-1].ordinal, claimed)
            if self.cheat and self.cheat["kind"] == "eof_extra" and self.cheat["t"] == t:
                # "justify" the lied outputs with one more tuple after EOF carrying a fake P_m
                V2 = list(V)
                V2[self.cheat["signal"]] ^= 1
                m = seg[-1]
                seg.append(t2.SegTuple(m.byte_offset, m.first_signal, m.latch_ordinal,
                                       t2.prefix_root(t2.DOM_SIGNAL, V2, m.first_signal), m.d))
            lists = {"seg": seg, "out": out, "state": st}
            leaves = {"seg": [t2.seg_tuple_leaf(j, s) for j, s in enumerate(seg)],
                      "out": [t2.end_tuple_leaf("out", k, e) for k, e in enumerate(out)],
                      "state": [t2.end_tuple_leaf("state", k, e) for k, e in enumerate(st)]}
            self._details[t] = {"V": V, "outs": outs, "ns": ns, "lists": lists,
                                "levels": {k: t2.merkle_levels(v) for k, v in leaves.items()}}
        return self._details[t]

    def commit_details(self, t: int) -> dict:
        d = self.details(t)
        return {k: (d["levels"][k][-1][0], len(d["lists"][k])) for k in d["lists"]}

    def reveal_tuple(self, t: int, kind: str, j: int):
        d = self.details(t)
        return d["lists"][kind][j], t2.merkle_proof(d["levels"][kind], j)

    def witness(self, t: int, kind: str, j: int) -> bytes:
        """Serialized witness (spec 6.3) for the step j-1 -> j of list `kind` in beat t, built from this
        party's own values."""
        d = self.details(t)
        if kind == "seg":
            lst = d["lists"]["seg"]
            end = lst[j].byte_offset if j < len(lst) else len(self.nl.data)
            w = t2.build_seg_witness(self.nl, d["V"], self.states[t], lst[j - 1], end, self.leaf(t)[1])
            return t2.serialize_seg_witness(w)
        if kind == "out":
            return t2.serialize_end_witness(t2.build_out_witness(self.nl, d["V"], d["outs"], j - 1))
        return t2.serialize_end_witness(t2.build_state_witness(self.nl, d["V"], d["ns"], j - 1))


@dataclass
class Result:
    winner: str
    loser: str
    reason: str
    rounds: Dict[str, int] = field(default_factory=dict)
    final: str = ""
    disputed_beat: Optional[int] = None
    disputed_index: Optional[int] = None
    witness_bytes: Optional[int] = None

    @property
    def total_rounds(self) -> int:
        return sum(self.rounds.values())

    def as_json(self) -> dict:
        return {"winner": self.winner, "loser": self.loser, "reason": self.reason, "rounds": dict(self.rounds),
                "totalRounds": self.total_rounds, "final": self.final, "beat": self.disputed_beat,
                "index": self.disputed_index, "witnessBytes": self.witness_bytes}


class Referee:
    """Holds only on-chain data: the store (chunk oracle, largeInfo), the budget, and for each claim the claim
    tuple and the published checkpoint leaves."""

    MAX_LEAVES = 2048

    def __init__(self, store: t2.LargeStore, budget: t2.Budget, max_K: Optional[int] = None,
                 max_T: Optional[int] = None, input_case: Optional[str] = None, provider: Optional[str] = None):
        """max_K, max_T: the bounds of spec 7.1 (None: unbounded, as in the plain dispute simulations).
        input_case: None (inputRoot taken as given, as in the plain dispute simulations), "a" (computed by the
        referee from the inputs published with the claim) or "b" (supplied by `provider`, which publishes first
        and may never be the claimant)."""
        self.store, self.budget = store, budget
        self.max_K, self.max_T = max_K, max_T
        self.input_case, self.provider = input_case, provider
        self.published = set()

    def publish_inputs(self, who: str, nh2: bytes, T: int, input_root: bytes, packed: List[bytes]) -> None:
        """Case (b): the input provider publishes the packed inputs of all T beats and the inputRoot it
        supplies. Only the provider may publish; only the length is checked, not the consistency."""
        if self.input_case != "b" or who != self.provider:
            raise ClaimRejected("only the input provider publishes inputs")
        _, n_in, _, _, _ = self.store.largeInfo(nh2)
        if len(packed) != T or any(len(x) != (n_in + 7) // 8 for x in packed):
            raise ClaimRejected("published inputs have the wrong length")
        self.published.add((nh2, T, input_root))

    def accept_claim(self, nh2: bytes, T: int, K: int, input_root: bytes, leaves: List[bytes],
                     claimant: Optional[str] = None, input_data: Optional[List[bytes]] = None) -> Claim:
        """Spec 7.1: the file must be stored; T and K within the bounds; the leaf list must have one leaf per
        checkpoint time and start with INITIAL_LEAF; checkpointRoot is computed here from the published list;
        inputRoot computed from the published inputs (case a) or published by a provider other than the
        claimant (case b)."""
        if not self.store.largeStored(nh2):
            raise ClaimRejected("file not stored (largeStored is false)")
        if T < 1 or K < 1:
            raise ClaimRejected("T and K must be at least 1")
        if (self.max_K is not None and K > self.max_K) or (self.max_T is not None and T > self.max_T):
            raise ClaimRejected("T or K above the bounds of the game")
        if self.input_case == "a":
            _, n_in, _, _, _ = self.store.largeInfo(nh2)
            try:
                ins = [t2.unpack_inputs(n_in, x) for x in (input_data or [])]
            except t2.Invalid:
                raise ClaimRejected("malformed published inputs")
            if len(ins) != T or t2.input_root(ins) != input_root:
                raise ClaimRejected("inputRoot does not match the published inputs")
        elif self.input_case == "b":
            if claimant is not None and claimant == self.provider:
                raise ClaimRejected("the input provider cannot be the claimant")
            if (nh2, T, input_root) not in self.published:
                raise ClaimRejected("inputs not published by the input provider")
        if len(leaves) != len(t2.checkpoint_times(T, K)) or len(leaves) > self.MAX_LEAVES:
            raise ClaimRejected("wrong number of checkpoint leaves")
        if any(len(x) != 32 for x in leaves):
            raise ClaimRejected("malformed checkpoint leaf")
        if leaves[0] != t2.INITIAL_LEAF:
            raise ClaimRejected("checkpoint leaf 0 is not the initial leaf")
        return Claim(nh2, T, K, input_root, t2.merkle_root(leaves))

    def _check(self, who: str, root: bytes, size: int, index: int, leaf: bytes, proof) -> None:
        if not t2.merkle_verify(root, size, index, leaf, proof):
            raise Lose(who, "bad Merkle proof in a reveal")

    def dispute(self, claim: Claim, published: List[bytes], cl: Party, ch: Party) -> Result:
        parties = {"claimant": cl, "challenger": ch}
        rounds: Dict[str, int] = {}
        try:
            return self._dispute(claim, published, cl, ch, rounds)
        except Lose as e:
            other = "challenger" if e.who == "claimant" else "claimant"
            return Result(parties[other].name, parties[e.who].name, e.why, rounds, "none (reveal check)")

    def _dispute(self, claim: Claim, published: List[bytes], cl: Party, ch: Party,
                 rounds: Dict[str, int]) -> Result:
        P = {"claimant": cl, "challenger": ch}
        nh2 = claim.netlist_hash2
        file_len, n_in, n_out, _, _ = self.store.largeInfo(nh2)
        cps = t2.checkpoint_times(claim.T, claim.K)
        ncp = len(cps)
        cp_levels = t2.merkle_levels(published)      # anyone can build proofs from the published list

        # ---------------- level 1: the challenger names an index
        i = ch.choose_index(published)
        if i is None:
            return Result(cl.name, ch.name, "no dispute: the challenger's leaves equal the published list",
                          rounds, "none")
        rounds["checkpoint"] = 1
        if not 1 <= i < ncp:
            raise Lose("challenger", "named checkpoint index out of range")
        posted = ch.posted_leaf(i)
        if posted[0] != cps[i]:
            raise Lose("challenger", "posted leaf carries the wrong time")
        lprev, lcur = published[i - 1], published[i]
        self._check("challenger", claim.checkpoint_root, ncp, i - 1, lprev, t2.merkle_proof(cp_levels, i - 1))
        self._check("challenger", claim.checkpoint_root, ncp, i, lcur, t2.merkle_proof(cp_levels, i))
        if t2.checkpoint_leaf(*posted) == lcur:
            raise Lose("challenger", "the named leaves are equal")
        ends = {"claimant": (lprev, lcur), "challenger": (lprev, t2.checkpoint_leaf(*posted))}

        # ---------------- level 2: beats of the interval (size fixed by the referee)
        t_lo, t_hi = cps[i - 1], cps[i]
        n_beats = t_hi - t_lo + 1
        roots = {who: p.commit_interval(t_lo, t_hi) for who, p in P.items()}
        rounds["beat"] = 1

        def beat(t: int):
            out = {}
            for who, p in P.items():
                lf, pr = p.reveal_beat(t)
                self._check(who, roots[who], n_beats, t - t_lo, t2.checkpoint_leaf(*lf), pr)
                if lf[0] != t:
                    raise Lose(who, "beat leaf with the wrong time")
                out[who] = lf
            return out

        lo_r, hi_r = beat(t_lo), beat(t_hi)
        for who in P:   # the interval starts at the accepted leaf i-1 and ends at the party's own leaf i
            if (t2.checkpoint_leaf(*lo_r[who]) != ends[who][0]
                    or t2.checkpoint_leaf(*hi_r[who]) != ends[who][1]):
                raise Lose(who, "interval endpoints differ from the checkpoint leaves")
        blo, bhi = t_lo, t_hi
        while bhi - blo > 1:
            mid = (blo + bhi) // 2
            r = beat(mid)
            rounds["beat"] += 1
            if r["claimant"] == r["challenger"]:
                blo = mid
            else:
                bhi = mid
        t = blo
        before = beat(t)["claimant"]
        after = beat(t + 1)

        # ---------------- level 3: inputs of beat t, then the tuple lists
        rev = cl.reveal_inputs(t)
        if rev is None:
            raise Lose("claimant", "inputs of the disputed beat not revealed")
        packed, pr = rev
        leaf = t2.sha256(bytes([t2.DOM_INPUT]) + t2.u64(t) + t2.sha256(packed))
        self._check("claimant", claim.input_root, claim.T, t, leaf, pr)
        try:
            in_bits = t2.unpack_inputs(n_in, packed)
        except t2.Invalid:
            raise Lose("claimant", "revealed inputs are malformed")

        com = {who: p.commit_details(t) for who, p in P.items()}
        rounds["tuples"] = 1

        def tup(who: str, kind: str, j: int):
            p = P[who]
            root, n = com[who][kind]
            if not 0 <= j < n:
                raise Lose(who, f"{kind} tuple {j} does not exist")
            v, pr2 = p.reveal_tuple(t, kind, j)
            lf = t2.seg_tuple_leaf(j, v) if kind == "seg" else t2.end_tuple_leaf(kind, j, v)
            self._check(who, root, n, j, lf, pr2)
            return v

        seg0_pub = t2.initial_tuple(n_in, in_bits)
        ends3 = {}
        for who in P:
            n = {k: com[who][k][1] for k in com[who]}
            s0, sm = tup(who, "seg", 0), tup(who, "seg", n["seg"] - 1)
            o0, om = tup(who, "out", 0), tup(who, "out", n["out"] - 1)
            q0, qm = tup(who, "state", 0), tup(who, "state", n["state"] - 1)
            lf = after[who]
            if s0 != seg0_pub:
                raise Lose(who, "seg_0 differs from the public initial tuple")
            if sm.byte_offset != file_len:
                raise Lose(who, "last segment tuple does not end at the end of the file")
            if o0 != t2.EndTuple(0, t2.EMPTY) or q0 != t2.EndTuple(0, t2.EMPTY):
                raise Lose(who, "beat-end lists must start at (0, EMPTY)")
            if om.ordinal != n_out or lf[2] != t2.io_next(before[2], om.root):
                raise Lose(who, "output tuples do not match the claimed ioHash")
            if qm.ordinal != sm.latch_ordinal or lf[1] != qm.root:
                raise Lose(who, "state tuples do not match the claimed stateRoot")
            ends3[who] = {"seg": sm, "out": om, "state": qm}

        for kind in ("seg", "out", "state"):
            if ends3["claimant"][kind] != ends3["challenger"][kind]:
                break
        else:
            raise AssertionError("unreachable: equal end tuples imply equal beat leaves")
        n_cl, n_ch = com["claimant"][kind][1] - 1, com["challenger"][kind][1] - 1
        hi = min(n_cl, n_ch)
        rounds[kind] = 1    # both reveal tuple h (a round of its own, as in the on-chain game)
        if tup("claimant", kind, hi) == tup("challenger", kind, hi):
            # equal at the shorter length: the longer list must justify one more step (it cannot, past EOF);
            # its party reveals tuple h + 1 first (one more round)
            prover = "claimant" if n_cl > n_ch else "challenger"
            j = hi + 1
            rounds[kind] += 1
        else:
            lo = 0
            while hi - lo > 1:
                mid = (lo + hi) // 2
                rounds[kind] += 1
                if tup("claimant", kind, mid) == tup("challenger", kind, mid):
                    lo = mid
                else:
                    hi = mid
            prover, j = "claimant", hi
        a = tup("claimant", kind, j - 1)    # agreed
        b = tup(prover, kind, j)
        w = P[prover].witness(t, kind, j)
        rounds["verify"] = 1
        if kind == "seg":
            ok = t2.verify_segment(self.store, nh2, before[1], a, b, w, self.budget)
            final = f"verify_segment(seg_{j - 1} -> seg_{j})"
        else:
            ok = t2.verify_beat_end(self.store, nh2, n_out, ends3["claimant"]["seg"], a, b, w)
            final = f"verify_beat_end[{kind}]({j - 1} -> {j})"
        other = "challenger" if prover == "claimant" else "claimant"
        win, lose = (prover, other) if ok else (other, prover)
        return Result(P[win].name, P[lose].name, f"{final} returned {ok} for the {prover}", rounds, final, t, j,
                      len(w))


# ---------------------------------------------------------------- several challengers (spec 7.1)

class ClaimGame:
    """One claim and every dispute about it (spec 7.1). Each challenger has its own two-party dispute, played
    by the Referee. The claim is rejected as soon as a challenger wins a dispute; a claimant that wins a dispute
    does not make the claim accepted; within the window new challengers keep being accepted; the claim is
    accepted only when the window has ended with no dispute open and none won by a challenger. Bonds: the
    claimant posts one; the claimant takes the bond of every challenger it beats; the first challenger to win
    takes the claimant's bond (and its own back); disputes still open at the rejection end without a winner and
    their bonds are refunded. In this sequential simulation a dispute runs to its end inside `challenge`, so
    "open" disputes are those registered with `open_dispute` and not yet played."""

    def __init__(self, ref: Referee, claimant: Party, nh2: bytes, bond: int = 1, **claim_kw):
        self.ref, self.claimant, self.nh2, self.bond = ref, claimant, nh2, bond
        nh2_, T, K, ir, leaves = claimant.claim_data(nh2)
        self.claim = ref.accept_claim(nh2_, T, K, ir, leaves, **claim_kw)
        self.published = leaves
        self.status = "open"
        self.window_open = True
        self.results: Dict[str, dict] = {}
        self.pending: Dict[str, Party] = {}
        self.credit: Dict[str, int] = {}

    def _pay(self, who: str, v: int) -> None:
        self.credit[who] = self.credit.get(who, 0) + v

    def open_dispute(self, ch: Party) -> None:
        """A challenge that is registered now and played later (disputes in parallel)."""
        if self.status != "open" or not self.window_open:
            raise ClaimRejected("the claim takes no new challengers")
        if ch.name in self.pending or ch.name in self.results:
            raise ClaimRejected("one dispute per challenger")
        self.pending[ch.name] = ch

    def play(self, name: str) -> dict:
        ch = self.pending[name]
        if self.status != "open":
            raise ClaimRejected("the claim was rejected; the dispute is void")
        del self.pending[name]
        res = self.ref.dispute(self.claim, self.published, self.claimant, ch).as_json()
        self.results[name] = res
        if res["winner"] == self.claimant.name:
            self._pay(self.claimant.name, self.bond)
        else:
            self.status = "rejected"
            self._pay(name, 2 * self.bond)
            for other in list(self.pending):           # voided: bond refunded
                self._pay(other, self.bond)
                self.results[other] = {"winner": None, "reason": "voided: claim rejected in another dispute"}
            self.pending.clear()
        return res

    def challenge(self, ch: Party) -> dict:
        self.open_dispute(ch)
        return self.play(ch.name)

    def end_window(self) -> str:
        """The window ends; the claim is accepted iff it is still open and no dispute is open."""
        self.window_open = False
        if self.status == "open" and not self.pending:
            self.status = "accepted"
            self._pay(self.claimant.name, self.bond)
        return self.status


class SockPuppet(Party):
    """A second address of the claimant: it challenges with an out-of-range index (loses at once), or names
    a real index and then stops answering (loses by timeout at level 2)."""

    def choose_index(self, published):
        return 0 if (self.cheat or {}).get("sock") == "index0" else super().choose_index(published)

    def commit_interval(self, t_lo, t_hi):
        if (self.cheat or {}).get("sock") == "stall":
            raise Lose("challenger", "timeout (the sock puppet stalls)")
        return super().commit_interval(t_lo, t_hi)


def multi_challenger_scenarios() -> Dict[str, dict]:
    """Spec 7.1 with several challengers, on the message example: a sock puppet that loses at once or stalls
    does not stop an honest challenger; two honest challengers (the first wins, the other is refunded); an
    honest claimant against two cheating challengers (accepted after the window)."""
    ref, nh2, data, nl, inputs, K, budget, cheats = setup_game("message")
    mk = lambda name, cheat=None, cls=Party: cls(name, nl, inputs, K, budget, cheat)  # noqa: E731
    out = {}
    for sock in ("index0", "stall"):
        liar = mk("cheater_claimant", cheats["c_rom"])
        g = ClaimGame(ref, liar, nh2)
        r1 = g.challenge(mk("sock_puppet", {"sock": sock, "kind": "none"}, SockPuppet))
        after_sock = g.status
        r2 = g.challenge(mk("honest_challenger"))
        out[f"message/sock_{sock}_then_honest"] = {
            "sock": r1, "honest": r2, "statusAfterSock": after_sock, "status": g.end_window(),
            "credit": dict(g.credit)}
    liar = mk("cheater_claimant", cheats["c_rom"])
    g = ClaimGame(ref, liar, nh2)
    g.open_dispute(mk("honest_A"))
    g.open_dispute(mk("honest_B"))
    ra = g.play("honest_A")
    out["message/two_honest_challengers"] = {"A": ra, "B": g.results["honest_B"], "status": g.end_window(),
                                             "credit": dict(g.credit)}
    honest = mk("honest_claimant")
    g = ClaimGame(ref, honest, nh2)
    g.open_dispute(mk("cheater_A", cheats["b_state"]))
    g.open_dispute(mk("cheater_B", cheats["c_rom"]))
    ra, rb = g.play("cheater_A"), g.play("cheater_B")
    out["message/honest_claimant_two_cheaters"] = {"A": ra, "B": rb, "statusBeforeWindow": g.status,
                                                   "status": g.end_window(), "credit": dict(g.credit)}
    return out


# ---------------------------------------------------------------- scenarios

def _first_out(nl: t2.Netlist, op: int, nth: int = 0) -> int:
    return [r.first_out for r in nl.records if r.raw.op == op][nth]


def _observable(nl, inputs, K, budget, kind: str, candidates):
    """First (t, signal) in candidates whose flip changes the claim (a lie in dead logic tests nothing)."""
    honest = Party("h", nl, inputs, K, budget)
    for t, sig in candidates:
        c = {"kind": kind, "t": t, "signal": sig}
        if Party("x", nl, inputs, K, budget, c).cp_leaves != honest.cp_leaves:
            return c
    raise AssertionError("no observable fault among the candidates")


def scenario_setup(name: str):
    """Returns (data, nl, inputs, K, budget, cheats) for a named example."""
    if name == "message":
        data = examples.message_file()
        nl = t2.decode(data)
        inputs = [[1]] * 40
        rom = _first_out(nl, t2.OP_ROM)
        nand = _first_out(nl, t2.OP_NAND, 4)     # the XOR output of counter bit 0 (feeds latch 0)
        cheats = {
            "a_short": {"kind": "segmentation", "mode": "short", "t": 19, "signal": nand},
            "a_long": {"kind": "segmentation", "mode": "long", "t": 19, "signal": nand},
            "b_state": {"kind": "state", "t": 21, "latch": 2},
            "c_rom": {"kind": "rom", "t": 12, "signal": rom + 3},
            "d_checkpoint": {"kind": "checkpoint", "t": 24},
            "e_eof_extra": {"kind": "eof_extra", "t": 15, "signal": nl.n_signals - 1},
            "f_beat0": {"kind": "gate", "t": 0, "signal": nand},
            "g_empty_state": {"kind": "empty_state", "t": 16},
        }
        return data, nl, inputs, 8, t2.TINY_BUDGET, cheats
    if name == "large":
        data = examples.large_file()
        nl = t2.decode(data)
        rng = examples.XorShift(0x1A26E)
        inputs = [rng.bits(16) for _ in range(6)]
        rom_b = _first_out(nl, t2.OP_ROM, 1)
        rom_c = _first_out(nl, t2.OP_ROM, 2)
        out_nand = nl.n_signals - 3
        cheats = {
            "a_short": {"kind": "segmentation", "mode": "short", "t": 3, "signal": out_nand},
            "a_long": {"kind": "segmentation", "mode": "long", "t": 3, "signal": out_nand},
            "b_state": {"kind": "state", "t": 2, "latch": 5},
            "c_rom": _observable(nl, inputs, 2, t2.DEFAULT_BUDGET, "rom",
                                 [(t, rom_c + j) for t in (3, 4, 5, 2) for j in range(4)]),
            "c_rom_big": _observable(nl, inputs, 2, t2.DEFAULT_BUDGET, "rom",
                                     [(t, rom_b + j) for t in (4, 3, 5) for j in range(8)]),
            "d_checkpoint": {"kind": "checkpoint", "t": 4},
            "e_eof_extra": {"kind": "eof_extra", "t": 1, "signal": nl.n_signals - 2},
            "f_beat0": {"kind": "gate", "t": 0, "signal": out_nand},
            "g_empty_state": {"kind": "empty_state", "t": 2},
        }
        return data, nl, inputs, 2, t2.DEFAULT_BUDGET, cheats
    raise KeyError(name)


def setup_game(name: str):
    """A store holding the example file, a referee, and the scenario data."""
    data, nl, inputs, K, budget, cheats = scenario_setup(name)
    store, nh2 = t2.oracle_for(data)
    return Referee(store, budget), nh2, data, nl, inputs, K, budget, cheats


def run_one(ref: Referee, nh2: bytes, cl: Party, ch: Party) -> dict:
    """Claim by cl, challenge by ch; returns the result with the number of verifier calls."""
    claim = ref.accept_claim(*cl.claim_data(nh2))
    published = cl.claim_data(nh2)[4]
    before = sum(t2.VERIFY_CALLS.values())
    res = ref.dispute(claim, published, cl, ch).as_json()
    res["verifyCalls"] = sum(t2.VERIFY_CALLS.values()) - before
    return res


def run_scenarios(names=("message", "large"), verbose: bool = False) -> Dict[str, dict]:
    """Every cheater class against an honest party, in both roles; all of these must be won by the honest
    party."""
    results = {}
    for ex in names:
        ref, nh2, data, nl, inputs, K, budget, cheats = setup_game(ex)
        honest = Party("honest", nl, inputs, K, budget)
        honest2 = Party("honest2", nl, inputs, K, budget)
        results[f"{ex}/honest_vs_honest"] = run_one(ref, nh2, honest, honest2)
        for cname, cheat in cheats.items():
            liar = Party(f"cheater_{cname}", nl, inputs, K, budget, cheat)
            if liar.cp_leaves == honest.cp_leaves:
                raise AssertionError(f"{ex}/{cname}: the lie is not observable; scenario tests nothing")
            for role, (cl, chal) in (("claimant", (liar, honest)), ("challenger", (honest, liar))):
                results[f"{ex}/{cname}_as_{role}"] = run_one(ref, nh2, cl, chal)
        # a lying claimant that withholds, or reveals other, inputs at level 3
        base = dict(cheats["c_rom"])
        for how in ("withhold", "wrong"):
            liar = Party(f"cheater_inputs_{how}", nl, inputs, K, budget, dict(base, inputs=how))
            results[f"{ex}/h_inputs_{how}_as_claimant"] = run_one(ref, nh2, liar, honest)
    return results


def checkpoint_position_scenarios() -> Dict[str, dict]:
    """The reviewer's attack 3: a claimant forges only one middle checkpoint (t = 8, 16, 24, 32 of the message
    run, last leaf correct). The honest challenger finds the index in the published list and wins."""
    ref, nh2, data, nl, inputs, K, budget, _ = setup_game("message")
    honest = Party("honest", nl, inputs, K, budget)
    out = {}
    for t in (8, 16, 24, 32):
        liar = Party(f"cheater_checkpoint_t{t}", nl, inputs, K, budget, {"kind": "checkpoint", "t": t})
        assert liar.cp_leaves[-1] == honest.cp_leaves[-1]
        out[f"message/forged_checkpoint_t{t}_as_claimant"] = run_one(ref, nh2, liar, honest)
    return out


def wrong_index_scenarios() -> Dict[str, dict]:
    """A challenger that names an index whose leaves are equal loses (spec 7.2): against an honest claimant,
    and against a claimant that forged checkpoint 3 (t = 24) when the challenger names 2 or 4 instead."""
    ref, nh2, data, nl, inputs, K, budget, _ = setup_game("message")
    honest = Party("honest", nl, inputs, K, budget)
    forger = Party("cheater_checkpoint_t24", nl, inputs, K, budget, {"kind": "checkpoint", "t": 24})
    out = {}
    for i in (1, 5):
        ch = Party(f"challenger_index_{i}", nl, inputs, K, budget, {"kind": "index", "i": i})
        out[f"message/index_{i}_vs_honest_claimant"] = run_one(ref, nh2, honest, ch)
    for i in (2, 4):
        ch = Party(f"challenger_index_{i}", nl, inputs, K, budget, {"kind": "index", "i": i})
        out[f"message/index_{i}_vs_forger_t24"] = run_one(ref, nh2, forger, ch)
    ch = Party("challenger_index_3", nl, inputs, K, budget, {"kind": "index", "i": 3})
    out["message/index_3_vs_forger_t24"] = run_one(ref, nh2, forger, ch)
    return out


def main(argv=None) -> int:
    res = run_scenarios()
    res.update(checkpoint_position_scenarios())
    bad = 0
    for k, r in res.items():
        honest_won = r["winner"].startswith("honest")
        if not honest_won:
            bad += 1
        rd = " ".join(f"{a}={b}" for a, b in r["rounds"].items())
        print(f"{k:46s} winner={r['winner']:28s} rounds={r['totalRounds']:3d} [{rd}] verifyCalls={r['verifyCalls']}")
        print(f"{'':46s} {r['final']}  ({r['reason']})")
    for k, r in wrong_index_scenarios().items():
        print(f"{k:46s} winner={r['winner']:28s} ({r['reason']})")
    print("all honest parties won" if not bad else f"{bad} scenarios lost by the honest party")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
