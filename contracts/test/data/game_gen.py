"""Move data for the end-to-end dispute tests of src/game/TapLargeDisputeGame.sol (test/game/E2E.t.sol).

The parties are the honest and cheating simulators of assets/tap-draft-large-netlists/bisect_sim.py (`Party`), which compute their values
with assets/tap-draft-large-netlists/tapnet2.py. This script plays each scenario against a Python mirror of the on-chain referee (same
phases, same midpoint rule, same list pick, same reveal checks, same final call) and records every transaction
each party sends, together with the outcome the mirror predicts: for every dispute of the claim its winner,
reason, number of verifier calls and picked list, and the final status of the claim (accepted or rejected).
The Solidity test replays the moves on chain and asserts that the contract reaches the same outcome.

A scenario is one claim and one or more disputes about it, each keyed by its challenger (§7.1). Single-dispute
scenarios end, when the claimant wins, with an `accept` after the challenge window; multi-challenger scenarios
interleave several disputes (sock puppets, two honest challengers, two cheating challengers).

Games (one per file): `message` (TINY budget, inputs of case (a)), `large` (default budget, case (b)), and the
files with RAM ports (MEM): `ram16` (TINY, case (a)), `rammix` (default, case (b)) with the RAM cheaters of
bisect_sim (a RAM read lie, a forged RAM write, a write-data gate lie, a LATCH lie, a lie in beat 0) and a claimant
that cannot open the agreed stateRoot_t, and `dodge`, the TAPBOY-8 cartridge of demo/cartridge (default budget,
case (b), T = 651, K = 32) with the score disputes of demo/cartridge/dispute_demo.py.

Usage: python3 game_gen.py     (deterministic; writes game_moves.bin next to this file)

File format (big-endian):
  file     = "TDG3" ‖ u8(nGames) ‖ game* ‖ u32(nScenarios) ‖ scenario*
  game     = u32(len) ‖ file bytes ‖ u32(gates) ‖ u32(extWords) ‖ u32(chunks) ‖ u8(input case: 0 (a), 1 (b))
  scenario = u16(nameLen) ‖ name ‖ u8(game) ‖ u8(final claim status: 1 open, 2 rejected, 3 accepted)
             ‖ u8(nDisputes) ‖ dispute* ‖ u16(nMoves) ‖ move*
  dispute  = u8(challenger actor) ‖ u8(outcome: 0 claimant won, 1 challenger won, 2 voided) ‖ u8(reason)
             ‖ u8(verifierCalls) ‖ u8(kind: 0 seg, 1 out, 2 state, 255 none)
  move     = u8(actor) ‖ u8(type) ‖ u8(dispute: its challenger actor, 255 for claim-level moves) ‖ u32(len) ‖ body
  actors   = 0 claimant, 1 challenger, 2 bystander, 3 input provider, 4 second challenger
  proof    = u16(count) ‖ hashes;  tuple = u64 ‖ u32 ‖ u32 ‖ P ‖ D ‖ M;  end = u32(ordinal) ‖ root
  bodies:  1 CLAIM          u64 T ‖ u64 K ‖ inputRoot ‖ u16 n ‖ leaves ‖ u32 len ‖ inputData (case (a) only)
           2 CHALLENGE      u32 i ‖ prevSr ‖ prevIo ‖ proof ‖ leafCur ‖ proof ‖ sr ‖ io
           3 COMMIT_BEATS   root ‖ proofLo ‖ proofHi
           4 REVEAL_BEAT    u64 expected beat ‖ sr ‖ io ‖ proof
           5 COMMIT_TUPLES  u32 len ‖ packedInputs ‖ proof ‖ srT ‖ ioT ‖ srT1 ‖ ioT1 ‖ latchRootT ‖ memRootT
                            ‖ 3 roots ‖ 3 u32 lengths
                            ‖ tuple segLast ‖ end outLast ‖ end stateLast ‖ 6 proofs (seg0, segLast, out0,
                            outLast, state0, stateLast)
           6 REVEAL_SEG     u64 expected index ‖ tuple ‖ proof
           7 REVEAL_END     u64 expected index ‖ end ‖ proof
           8 PROVE_SEG      tuple a ‖ tuple b ‖ u32 len ‖ witness
           9 PROVE_END      tuple segM ‖ end a ‖ end b ‖ u32 len ‖ witness
          10 TIMEOUT        (empty; the test warps past the dispute's deadline)
          11 PUBLISH_INPUTS u64 T ‖ inputRoot ‖ u32 len ‖ inputData (case (b), by the input provider)
          12 ACCEPT         (empty; the test warps past the challenge window)
          13 REFUND         (empty; the dispute was voided by the rejection of the claim)
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "assets", "tap-draft-large-netlists"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "demo", "cartridge"))

import bisect_sim as bs  # noqa: E402
import tapnet2 as t2  # noqa: E402

# must match TapLargeDisputeGame.Reason
REASONS = ["None", "Timeout", "IndexOutOfRange", "BadCheckpointProof", "LeavesEqual", "BadEndpoints", "BadProof",
           "BadInputs", "BadFirstTuple", "SegNotAtEof", "OutputMismatch", "StateMismatch", "BadLastTuple",
           "VerifierTrue", "VerifierFalse", "BadOpening"]
R = {n: i for i, n in enumerate(REASONS)}
CL, CH, BY, PV, CH2 = 0, 1, 2, 3, 4     # actors (roles inside a dispute are CL and CH)
NONE = 255
KINDS = ("seg", "out", "state")
CASE_A, CASE_B = 0, 1                  # how the game obtains inputRoot (§7.1)
OPEN, REJECTED, ACCEPTED = 1, 2, 3     # TapLargeDisputeGame.Status
CL_WON, CH_WON, VOIDED = 0, 1, 2

M_CLAIM, M_CHALLENGE, M_COMMIT_BEATS, M_REVEAL_BEAT, M_COMMIT_TUPLES = 1, 2, 3, 4, 5
M_REVEAL_SEG, M_REVEAL_END, M_PROVE_SEG, M_PROVE_END, M_TIMEOUT = 6, 7, 8, 9, 10
M_PUBLISH, M_ACCEPT, M_REFUND = 11, 12, 13
MOVE_NAMES = {M_CLAIM: "claim", M_CHALLENGE: "challenge", M_COMMIT_BEATS: "commitBeats",
              M_REVEAL_BEAT: "revealBeat", M_COMMIT_TUPLES: "commitTuples", M_REVEAL_SEG: "revealSeg",
              M_REVEAL_END: "revealEnd", M_PROVE_SEG: "prove", M_PROVE_END: "prove", M_TIMEOUT: "timeout",
              M_PUBLISH: "publishInputs", M_ACCEPT: "accept", M_REFUND: "refund"}


def u16(x): return struct.pack(">H", x)
def u32(x): return struct.pack(">I", x)
def u64(x): return struct.pack(">Q", x)
def proof(p): return u16(len(p)) + b"".join(p)
def tup(s): return u64(s.byte_offset) + u32(s.first_signal) + u32(s.latch_ordinal) + s.p + s.d + s.m
def end(e): return u32(e.ordinal) + e.root


class Done(Exception):
    def __init__(self, winner, reason, calls=0, kind=255):
        self.winner, self.reason, self.calls, self.kind = winner, reason, calls, kind


class NoDispute(Exception):
    pass


def claim_moves(case, nh2, cl):
    """The claim-level moves: in case (b) the input provider publishes the inputs first (the claimant can
    never be the provider), then the claimant claims; in case (a) the inputs travel with the claim."""
    _, T, K, input_root, leaves = cl.claim_data(nh2)
    input_data = b"".join(t2.pack_bits(x) for x in cl.inputs)
    moves = []
    if case == CASE_B:
        moves.append((PV, M_PUBLISH, NONE, u64(T) + input_root + u32(len(input_data)) + input_data))
        input_data = b""
    moves.append((CL, M_CLAIM, NONE, u64(T) + u64(K) + input_root + u16(len(leaves)) + b"".join(leaves)
                  + u32(len(input_data)) + input_data))
    return moves


class Game:
    """Plays one dispute about a claim against the mirror referee and records the moves. `cha` is the actor of
    the challenger, which keys the dispute; the claim itself is made by `claim_moves`."""

    def __init__(self, store, nh2, budget, cl, ch, stop=None, index=None, cha=CH):
        self.store, self.nh2, self.budget, self.cl, self.ch = store, nh2, budget, cl, ch
        self.P = {CL: cl, CH: ch}
        self.stop = stop          # (role, move type, occurrence): that party stops sending at that move
        self.index = index        # challenger names this index (wrong-index scenarios), else its first difference
        self.cha = cha
        self.moves = []
        self.count = {}

    def emit(self, role, mtype, body):
        key = (role, mtype)
        n = self.count.get(key, 0)
        self.count[key] = n + 1
        if self.stop and self.stop == (role, mtype, n):
            self.moves.append((BY, M_TIMEOUT, self.cha, b""))
            raise Done(1 - role, R["Timeout"])
        self.moves.append((CL if role == CL else self.cha, mtype, self.cha, body))

    def run(self):
        """Returns Done, or None if the challenger finds no differing leaf (no dispute)."""
        try:
            self._run()
        except Done as d:
            return d
        except NoDispute:
            return None
        raise AssertionError("game did not end")

    def _run(self):
        cl, ch = self.cl, self.ch
        file_len, n_in, n_out, _, _ = self.store.largeInfo(self.nh2)
        has_mem = bool(self.store.largeChunk(self.nh2, 0)[7] & t2.FLAG_MEM)    # recorded with the claim (§7.1)
        self.has_mem = has_mem
        _, T, K, input_root, leaves = cl.claim_data(self.nh2)
        cps = t2.checkpoint_times(T, K)
        n = len(cps)
        cp_root = t2.merkle_root(leaves)
        cp_levels = t2.merkle_levels(leaves)

        # ---- level 1
        i = self.index if self.index is not None else ch.choose_index(leaves)
        if i is None:
            raise NoDispute()
        if not 1 <= i < n:
            self.emit(CH, M_CHALLENGE, u32(i) + bytes(64) + proof([]) + bytes(32) + proof([]) + bytes(64))
            raise Done(CL, R["IndexOutOfRange"])
        _, psr, pio = ch.leaf(cps[i - 1])          # the challenger's own leaf i-1 (it accepts L_{i-1})
        _, sr, io = ch.posted_leaf(i)
        self.emit(CH, M_CHALLENGE, u32(i) + psr + pio + proof(t2.merkle_proof(cp_levels, i - 1)) + leaves[i]
                  + proof(t2.merkle_proof(cp_levels, i)) + sr + io)
        lo_leaf = t2.checkpoint_leaf(cps[i - 1], psr, pio)
        if not t2.merkle_verify(cp_root, n, i - 1, lo_leaf, t2.merkle_proof(cp_levels, i - 1)):
            raise Done(CL, R["BadCheckpointProof"])
        posted = t2.checkpoint_leaf(cps[i], sr, io)
        if posted == leaves[i]:
            raise Done(CL, R["LeavesEqual"])
        hi_leaf = {CL: leaves[i], CH: posted}

        # ---- level 2
        base, top = cps[i - 1], cps[i]
        nb = top - base + 1
        roots = {}
        for who in (CL, CH):
            p = self.P[who]
            roots[who] = p.commit_interval(base, top)
            lv = p._iv[1]
            self.emit(who, M_COMMIT_BEATS, roots[who] + proof(t2.merkle_proof(lv, 0))
                      + proof(t2.merkle_proof(lv, nb - 1)))
            if lv[0][0] != lo_leaf or lv[0][nb - 1] != hi_leaf[who]:
                raise Done(1 - who, R["BadEndpoints"])
        lo, hi = base, top
        while hi - lo > 1:
            mid = (lo + hi) // 2
            got = {}
            for who in (CL, CH):
                (t_, s_, i_), pr = self.P[who].reveal_beat(mid)
                self.emit(who, M_REVEAL_BEAT, u64(mid) + s_ + i_ + proof(pr))
                got[who] = t2.checkpoint_leaf(mid, s_, i_)
                if not t2.merkle_verify(roots[who], nb, mid - base, got[who], pr):
                    raise Done(1 - who, R["BadProof"])
            if got[CL] == got[CH]:
                lo, lo_leaf = mid, got[CL]
            else:
                hi, hi_leaf = mid, dict(got)

        # ---- level 3
        t = lo
        _, sr_t, io_t = cl.leaf(t)
        assert t2.checkpoint_leaf(t, sr_t, io_t) == lo_leaf
        rev = cl.reveal_inputs(t)
        if rev is None:                      # withholding claimant: it cannot make the move at all
            self.stop = (CL, M_COMMIT_TUPLES, self.count.get((CL, M_COMMIT_TUPLES), 0))
            self.emit(CL, M_COMMIT_TUPLES, b"")
        packed, in_proof = rev
        # §7.4: for a file with MEM and stateRoot_t != EMPTY the claimant opens stateRoot_t; otherwise the game
        # uses (stateRoot_t, ZERO_MEM) and ignores the passed roots (sent as zero)
        latch_t, mem_t = sr_t, t2.ZERO_MEM
        opening = cl.state_opening(t) if has_mem and sr_t != t2.EMPTY else None
        sent = opening if opening is not None else (bytes(32), bytes(32))
        seg0_pub = None
        ends = {}
        com = {}
        for who in (CL, CH):
            p = self.P[who]
            d = p.details(t)
            c = p.commit_details(t)
            com[who] = c
            _, sr1, io1 = p.leaf(t + 1)
            lists, lvls = d["lists"], d["levels"]
            ln = [len(lists[k]) for k in KINDS]
            body = (u32(len(packed)) + packed + proof(in_proof)) if who == CL else (u32(0) + proof([]))
            body += sr_t + io_t + sr1 + io1 + (sent[0] + sent[1] if who == CL else bytes(64))
            body += b"".join(c[k][0] for k in KINDS) + b"".join(u32(x) for x in ln)
            body += tup(lists["seg"][-1]) + end(lists["out"][-1]) + end(lists["state"][-1])
            proofs = {k: (t2.merkle_proof(lvls[k], 0), t2.merkle_proof(lvls[k], len(lists[k]) - 1)) for k in KINDS}
            if getattr(p, "mode", None) == "last_proof":
                p0, pl = proofs["state"]
                proofs["state"] = (p0, [_flip(pl[0])] + pl[1:])
            for k in KINDS:
                body += proof(proofs[k][0]) + proof(proofs[k][1])
            self.emit(who, M_COMMIT_TUPLES, body)
            if who == CL:
                leaf = t2.sha256(bytes([t2.DOM_INPUT]) + t2.u64(t) + t2.sha256(packed))
                if not t2.merkle_verify(input_root, T, t, leaf, in_proof):
                    raise Done(CH, R["BadInputs"])
                if has_mem and sr_t != t2.EMPTY:
                    if t2.combine_state(*sent) != sr_t:
                        raise Done(CH, R["BadOpening"])
                    latch_t, mem_t = sent
                seg0_pub = t2.initial_tuple(n_in, t2.unpack_inputs(n_in, packed), mem_t)
            r = self.reveal_checks({k: c[k][0] for k in KINDS}, lists, proofs, seg0_pub, file_len, n_out, io_t,
                                   sr1, io1, has_mem)
            if r:
                raise Done(1 - who, R[r])
            ends[who] = {k: lists[k][-1] for k in KINDS}
        kind = next(k for k in KINDS if ends[CL][k] != ends[CH][k])
        ki = KINDS.index(kind)
        size = {who: com[who][kind][1] for who in (CL, CH)}
        h = min(size[CL], size[CH]) - 1

        def reveal(who, j):
            v, pr = self.P[who].reveal_tuple(t, kind, j)
            if kind == "seg":
                self.emit(who, M_REVEAL_SEG, u64(j) + tup(v) + proof(pr))
                lf = t2.seg_tuple_leaf(j, v, has_mem)
            else:
                self.emit(who, M_REVEAL_END, u64(j) + end(v) + proof(pr))
                lf = t2.end_tuple_leaf(kind, j, v)
            if not t2.merkle_verify(com[who][kind][0], size[who], j, lf, pr):
                raise Done(1 - who, R["BadProof"])
            return v

        vcl, vch = reveal(CL, h), reveal(CH, h)
        if vcl == vch:
            prover = CL if size[CL] > size[CH] else CH
            lo_j, hi_j = h, h + 1
            a = vcl
            b = reveal(prover, hi_j)
        else:
            lo_j, hi_j = 0, h
            a, b = self.P[CL].reveal_tuple(t, kind, 0)[0], vcl
            while hi_j - lo_j > 1:
                mid = (lo_j + hi_j) // 2
                x, y = reveal(CL, mid), reveal(CH, mid)
                if x == y:
                    lo_j, a = mid, x
                else:
                    hi_j, b = mid, x
            prover = CL
        w = self.P[prover].witness(t, kind, hi_j)
        if kind == "seg":
            self.emit(prover, M_PROVE_SEG, tup(a) + tup(b) + u32(len(w)) + w)
            ok = t2.verify_segment(self.store, self.nh2, latch_t, a, b, w, self.budget, mem_t)
        else:
            seg_m = ends[CL]["seg"]
            self.emit(prover, M_PROVE_END, tup(seg_m) + end(a) + end(b) + u32(len(w)) + w)
            ok = t2.verify_beat_end(self.store, self.nh2, n_out, seg_m, a, b, w)
        raise Done(prover if ok else 1 - prover, R["VerifierTrue" if ok else "VerifierFalse"], 1, ki)

    @staticmethod
    def reveal_checks(roots, lists, proofs, seg0_pub, file_len, n_out, io_t, sr1, io1, has_mem=False):
        """Section 7.4 checks 1-5 in the contract's order: first-tuple proofs against the public values, last-tuple
        proofs, then checks 2, 4, 5 (check 5 with the combined root for a file with MEM)."""
        seg, out, st = lists["seg"], lists["out"], lists["state"]
        leaf = lambda k, j, v: (t2.seg_tuple_leaf(j, v, has_mem) if k == "seg"  # noqa: E731
                                else t2.end_tuple_leaf(k, j, v))
        first = {"seg": seg0_pub, "out": t2.EndTuple(0, t2.EMPTY), "state": t2.EndTuple(0, t2.EMPTY)}
        for k in KINDS:
            if not t2.merkle_verify(roots[k], len(lists[k]), 0, leaf(k, 0, first[k]), proofs[k][0]):
                return "BadFirstTuple"
        for k in KINDS:
            n = len(lists[k])
            if not t2.merkle_verify(roots[k], n, n - 1, leaf(k, n - 1, lists[k][-1]), proofs[k][1]):
                return "BadLastTuple"
        if seg[-1].byte_offset != file_len:
            return "SegNotAtEof"
        if out[-1].ordinal != n_out or t2.io_next(io_t, out[-1].root) != io1:
            return "OutputMismatch"
        want = t2.combine_state(st[-1].root, seg[-1].m) if has_mem else st[-1].root
        if st[-1].ordinal != seg[-1].latch_ordinal or want != sr1:
            return "StateMismatch"
        return None


class OutputLiar(bs.Party):
    """Lies about one output of beat t only (honest signal values, honest segment list): the output list is the
    first whose last tuples differ, so the dispute ends in verify_beat_end of kind output. (bisect_sim's
    'eof_extra' cheater tells the same lie but also appends a segment tuple after EOF; this one does not.)"""

    def details(self, t):
        d = super().details(t)
        seg = d["lists"]["seg"]
        if len(seg) >= 2 and seg[-1].byte_offset == seg[-2].byte_offset:   # drop the appended tuple
            seg.pop()
            d["levels"]["seg"] = t2.merkle_levels([t2.seg_tuple_leaf(j, x, self.nl.has_mem) for j, x in enumerate(seg)])
        return d


def _flip(h: bytes) -> bytes:
    return bytes([h[0] ^ 0xFF]) + h[1:]


def _rebuild(d, kind, has_mem=False):
    lst = d["lists"][kind]
    leaves = [t2.seg_tuple_leaf(j, x, has_mem) if kind == "seg" else t2.end_tuple_leaf(kind, j, x)
              for j, x in enumerate(lst)]
    d["levels"][kind] = t2.merkle_levels(leaves)


class RuleBreaker(bs.Party):
    """A bisect_sim cheater that also breaks one rule of the game, so that a reveal check or a Merkle-proof check
    of the game fires instead of the final call. cheat["corrupt"] is one of:
      endpoints    its beat tree ends at another leaf than its own leaf at t_i          -> BadEndpoints
      beat_proof   a level-2 reveal with a wrong proof                                   -> BadProof
      tuple_proof  a level-3 reveal with a wrong proof                                   -> BadProof
      seg0         its first segment tuple is not the public seg_0                       -> BadFirstTuple
      last_proof   a wrong proof of its last state tuple                                 -> BadLastTuple
      seg_eof      its last segment tuple does not end at fileLen                        -> SegNotAtEof
      out_root     its last output tuple does not give its io_{t+1}                      -> OutputMismatch
      state_root   its last state tuple is not its stateRoot_{t+1}                       -> StateMismatch"""

    @property
    def mode(self):
        return self.cheat["corrupt"]

    def commit_interval(self, t_lo, t_hi):
        root = super().commit_interval(t_lo, t_hi)
        if self.mode != "endpoints":
            return root
        leaves = list(self._iv[1][0])
        leaves[-1] = _flip(leaves[-1])
        self._iv = (t_lo, t2.merkle_levels(leaves))
        return self._iv[1][-1][0]

    def reveal_beat(self, t):
        lf, pr = super().reveal_beat(t)
        return (lf, [_flip(pr[0])] + pr[1:]) if self.mode == "beat_proof" else (lf, pr)

    def reveal_tuple(self, t, kind, j):
        v, pr = super().reveal_tuple(t, kind, j)
        return (v, [_flip(pr[0])] + pr[1:]) if self.mode == "tuple_proof" and pr else (v, pr)

    def details(self, t):
        fresh = t not in self._details
        d = super().details(t)
        if fresh:
            seg, out, st = d["lists"]["seg"], d["lists"]["out"], d["lists"]["state"]
            if self.mode == "seg0":
                seg[0] = seg[0]._replace(p=_flip(seg[0].p))
                _rebuild(d, "seg", self.nl.has_mem)
            elif self.mode == "seg_eof":
                seg[-1] = seg[-1]._replace(byte_offset=seg[-1].byte_offset - 1)
                _rebuild(d, "seg", self.nl.has_mem)
            elif self.mode == "out_root":
                out[-1] = t2.EndTuple(out[-1].ordinal, _flip(out[-1].root))
                _rebuild(d, "out", self.nl.has_mem)
            elif self.mode == "state_root":
                st[-1] = t2.EndTuple(st[-1].ordinal, _flip(st[-1].root))
                _rebuild(d, "state", self.nl.has_mem)
        return d


def scenarios():
    """(name, game index, claimant cheat, challenger cheat, stop, index[, K]) of the single-dispute scenarios."""
    out = []
    for gi, ex in enumerate(("message", "large")):
        _, nl, inputs, K, budget, cheats = bs.scenario_setup(ex)
        out.append((f"{ex}/honest_unchallenged", gi, None, None, None, None))
        for cname, cheat in cheats.items():
            out.append((f"{ex}/{cname}_as_claimant", gi, cheat, None, None, None))
            out.append((f"{ex}/{cname}_as_challenger", gi, None, cheat, None, None))
        base = dict(cheats["c_rom"])
        lie = dict(cheats["e_eof_extra"], liar="output")
        out.append((f"{ex}/o_output_as_claimant", gi, lie, None, None, None))
        out.append((f"{ex}/o_output_as_challenger", gi, None, lie, None, None))
        out.append((f"{ex}/inputs_wrong_as_claimant", gi, dict(base, inputs="wrong"), None, None, None))
        out.append((f"{ex}/inputs_withhold_as_claimant", gi, dict(base, inputs="withhold"), None, None, None))
        # timeouts
        out.append((f"{ex}/timeout_claimant_commitBeats", gi, cheats["c_rom"], None,
                    (CL, M_COMMIT_BEATS, 0), None))
        out.append((f"{ex}/timeout_challenger_commitTuples", gi, None, cheats["b_state"],
                    (CH, M_COMMIT_TUPLES, 0), None))
        out.append((f"{ex}/timeout_claimant_prove", gi, cheats["c_rom"], None, (CL, M_PROVE_SEG, 0), None))
        out.append((f"{ex}/timeout_challenger_revealSeg", gi, None, cheats["c_rom"], (CH, M_REVEAL_SEG, 0), None))
    # wrong indices (message: n = 6 checkpoints at t = 0, 8, 16, 24, 32, 40)
    forger = {"kind": "checkpoint", "t": 24}
    for i in (1, 5):
        out.append((f"message/index_{i}_vs_honest", 0, None, None, None, i))
    for i in (0, 6):
        out.append((f"message/index_{i}_out_of_range", 0, None, None, None, i))
    for i in (2, 4):
        out.append((f"message/index_{i}_vs_forger_t24", 0, forger, None, None, i))
    out.append(("message/index_3_vs_forger_t24", 0, forger, None, None, 3))
    # rule breakers (message): the honest party wins at a reveal check, without a verifier call
    _, _, _, _, _, mc = bs.scenario_setup("message")
    rules = [("endpoints", CL), ("beat_proof", CH), ("tuple_proof", CL), ("seg0", CH), ("last_proof", CL),
             ("seg_eof", CL), ("out_root", CH), ("state_root", CL)]
    for mode, role in rules:
        c = dict(mc["c_rom"], corrupt=mode)
        who = "claimant" if role == CL else "challenger"
        out.append((f"message/rule_{mode}_by_{who}", 0, c if role == CL else None, c if role == CH else None,
                    None, None))
    # K = 1: 41 checkpoint leaves; the disputed interval is one beat, so level 2 has no bisection round
    out.append(("message/k1_c_rom_as_claimant", 0, mc["c_rom"], None, None, None, 1))
    out.append(("message/k1_c_rom_as_challenger", 0, None, mc["c_rom"], None, None, 1))
    out.append(("message/timeout_challenger_revealBeat", 0, None, {"kind": "state", "t": 21, "latch": 2},
                (CH, M_REVEAL_BEAT, 1), None))
    # files with RAM ports (MEM): the cheaters of bisect_sim.run_scenarios(("ram16", "rammix")) in both roles,
    # wrong and withheld inputs, and a claimant that does not open the agreed stateRoot_t (§7.4)
    for gi, ex in ((2, "ram16"), (3, "rammix")):
        _, nl, inputs, K, budget, cheats = bs.scenario_setup(ex)
        out.append((f"{ex}/honest_unchallenged", gi, None, None, None, None))
        for cname, cheat in cheats.items():
            out.append((f"{ex}/{cname}_as_claimant", gi, cheat, None, None, None))
            out.append((f"{ex}/{cname}_as_challenger", gi, None, cheat, None, None))
        base = dict(cheats["c_write_data"])
        out.append((f"{ex}/inputs_wrong_as_claimant", gi, dict(base, inputs="wrong"), None, None, None))
        out.append((f"{ex}/inputs_withhold_as_claimant", gi, dict(base, inputs="withhold"), None, None, None))
        out.append((f"{ex}/i_no_opening_as_claimant", gi, dict(cheats["b_ram_write"], opening="none"), None, None,
                    None))
    out.append(("ram16/timeout_claimant_prove", 2, cheats_of("ram16")["a_ram_read"], None, (CL, M_PROVE_SEG, 0),
                None))
    # the TAPBOY-8 cartridge: the score disputes of demo/cartridge/dispute_demo.py (A to F)
    dc = dodge_cheats()
    out.append(("dodge/A_honest_unchallenged", 4, None, None, None, None))
    out.append(("dodge/B_ram_write_as_claimant", 4, dc["state"], None, None, None))
    out.append(("dodge/C_alu_gate_as_claimant", 4, dc["gate"], None, None, None))
    out.append(("dodge/D_ram_write_as_challenger", 4, None, dc["state"], None, None))
    out.append(("dodge/E_ram_read_as_claimant", 4, dc["read"], None, None, None))
    out.append(("dodge/F_ram_read_as_challenger", 4, None, dc["read"], None, None))
    return out


def cheats_of(ex):
    return bs.scenario_setup(ex)[5]


_DODGE = {}


def dodge_setup():
    """(data, nl, inputs, K, budget, cheats) of the cartridge game of demo/cartridge/dispute_demo.py."""
    if not _DODGE:
        import dispute_demo as dd
        s = dd.setup()
        cs, _ = dd.cheats(s)
        _DODGE["v"] = (s["data"], s["nl"], s["inputs"], dd.K, dd.BUDGET, cs)
    return _DODGE["v"]


def dodge_cheats():
    return dodge_setup()[5]


class NoOpening(bs.Party):
    """A cheating claimant that does not reveal a valid opening of the agreed stateRoot_t at level 3."""

    def state_opening(self, t):
        return None


def _mk(c):
    if c and c.get("liar") == "output":
        return OutputLiar
    if c and c.get("opening") == "none":
        return NoOpening
    return RuleBreaker if c and "corrupt" in c else bs.Party


class Setup:
    """One example: the file, its store, the input case of its game and the scenario parameters."""

    def __init__(self, ex, case):
        self.ex, self.case = ex, case
        if ex == "dodge":
            self.data, self.nl, self.inputs, self.K, self.budget, self.cheats = dodge_setup()
        else:
            self.data, self.nl, self.inputs, self.K, self.budget, self.cheats = bs.scenario_setup(ex)
        self.store, self.nh2 = t2.oracle_for(self.data)

    def party(self, name, cheat, K=None):
        return _mk(cheat)(name, self.nl, self.inputs, K or self.K, self.budget, cheat)

    def dispute(self, cl, ch, cha=CH, stop=None, index=None):
        return Game(self.store, self.nh2, self.budget, cl, ch, stop, index, cha)


def _outcome(res):
    return CL_WON if res.winner == CL else CH_WON


def single(st, name, ccl, cch, stop, index, K):
    """One claim, at most one dispute. A claimant that wins (or is not challenged) is accepted after the
    challenge window; a challenger that wins rejects the claim."""
    cl = st.party("claimant", ccl, K)
    ch = st.party("challenger", cch, K)
    moves = claim_moves(st.case, st.nh2, cl)
    g = st.dispute(cl, ch, CH, stop, index)
    res = g.run()
    if res is None:
        return moves + [(BY, M_ACCEPT, NONE, b"")], [], ACCEPTED, None
    moves += g.moves
    disputes = [(CH, _outcome(res), res.reason, res.calls, res.kind)]
    if res.winner == CL:
        return moves + [(BY, M_ACCEPT, NONE, b"")], disputes, ACCEPTED, res
    return moves, disputes, REJECTED, res


def _interleave(a, b, stop_after_a=False):
    """Round-robin merge of two dispute move lists (both challenges first). If stop_after_a, the moves of b
    that would come after the last move of a are dropped (a's end rejects the claim and voids b)."""
    out, i, j = [], 0, 0
    while i < len(a) or j < len(b):
        if i < len(a):
            out.append(a[i])
            i += 1
            if stop_after_a and i == len(a):
                break
        if j < len(b):
            out.append(b[j])
            j += 1
    return out, j


def multi_scenarios(setups):
    """Several disputes about one claim (§7.1): a claimant that wins one dispute does not get its claim
    accepted, the window stays open for others, and one dispute won by a challenger rejects the claim."""
    recs = []
    msg = setups["message"]
    lie = msg.cheats["c_rom"]

    # 1. the claimant's sock puppet challenges with index 0 and loses in the same transaction; the honest
    #    challenger can still challenge, and wins
    cl = msg.party("claimant", lie)
    moves = claim_moves(msg.case, msg.nh2, cl)
    sock = msg.dispute(cl, msg.party("sock", None), CH2, index=0)
    rs = sock.run()
    assert rs.winner == CL and rs.reason == R["IndexOutOfRange"]
    hon = msg.dispute(msg.party("claimant", lie), msg.party("honest", None), CH)
    rh = hon.run()
    assert rh.winner == CH
    recs.append(("multi/sock_index0_then_honest", 0, REJECTED,
                 [(CH2, CL_WON, rs.reason, 0, 255), (CH, CH_WON, rh.reason, rh.calls, rh.kind)],
                 moves + sock.moves + hon.moves))

    # 2. the sock puppet challenges a real index and then stalls; it loses by timeout, long before the window
    #    ends, and the honest challenger still challenges and wins
    cl = msg.party("claimant", lie)
    moves = claim_moves(msg.case, msg.nh2, cl)
    sock = msg.dispute(cl, msg.party("sock", None), CH2, stop=(CH, M_COMMIT_BEATS, 0))
    rs = sock.run()
    assert rs.winner == CL and rs.reason == R["Timeout"]
    hon = msg.dispute(msg.party("claimant", lie), msg.party("honest", None), CH)
    rh = hon.run()
    assert rh.winner == CH
    recs.append(("multi/sock_stall_then_honest", 0, REJECTED,
                 [(CH2, CL_WON, rs.reason, 0, 255), (CH, CH_WON, rh.reason, rh.calls, rh.kind)],
                 moves + sock.moves + hon.moves))

    # 3. two honest challengers against a cheating claimant, disputes in parallel: the first to win takes the
    #    claimant's bond and rejects the claim; the other dispute is voided and its bond refunded
    for ex in ("message", "large"):
        st = setups[ex]
        cheat = st.cheats["c_rom"]
        cl = st.party("claimant", cheat)
        moves = claim_moves(st.case, st.nh2, cl)
        da = st.dispute(cl, st.party("honestA", None), CH)
        ra = da.run()
        db = st.dispute(st.party("claimant", cheat), st.party("honestB", None), CH2)
        rb = db.run()
        assert ra.winner == CH and rb.winner == CH
        assert len(da.moves) <= len(db.moves)
        merged, _ = _interleave(da.moves, db.moves, stop_after_a=True)
        recs.append((f"multi/two_honest_{ex}", ("message", "large").index(ex), REJECTED,
                     [(CH, CH_WON, ra.reason, ra.calls, ra.kind), (CH2, VOIDED, R["None"], 0, 255)],
                     moves + merged + [(BY, M_REFUND, CH2, b"")]))

    # 4. an honest claimant against two cheating challengers in parallel: it wins both disputes, takes both
    #    bonds, and the claim is accepted after the window
    for ex in ("message", "large"):
        st = setups[ex]
        cl = st.party("claimant", None)
        moves = claim_moves(st.case, st.nh2, cl)
        da = st.dispute(cl, st.party("cheaterA", st.cheats["b_state"]), CH)
        ra = da.run()
        db = st.dispute(st.party("claimant", None), st.party("cheaterB", st.cheats["c_rom"]), CH2)
        rb = db.run()
        assert ra.winner == CL and rb.winner == CL
        merged, _ = _interleave(da.moves, db.moves)
        recs.append((f"multi/two_cheaters_{ex}", ("message", "large").index(ex), ACCEPTED,
                     [(CH, CL_WON, ra.reason, ra.calls, ra.kind), (CH2, CL_WON, rb.reason, rb.calls, rb.kind)],
                     moves + merged + [(BY, M_ACCEPT, NONE, b"")]))
    return recs


def main():
    setups = {"message": Setup("message", CASE_A), "large": Setup("large", CASE_B),
              "ram16": Setup("ram16", CASE_A), "rammix": Setup("rammix", CASE_B), "dodge": Setup("dodge", CASE_B)}
    games = [setups[k] for k in ("message", "large", "ram16", "rammix", "dodge")]
    recs = []
    for sc in scenarios():
        name, gi, ccl, cch, stop, index = sc[:6]
        st = games[gi]
        K = sc[6] if len(sc) > 6 else None
        moves, disputes, status, res = single(st, name, ccl, cch, stop, index, K)
        if stop is not None:
            assert res.reason == R["Timeout"], (name, "the scripted stop was never reached")
        honest_role = CL if ccl is None else (CH if cch is None else None)
        if res is not None and honest_role is not None and index is None and stop is None:
            assert res.winner == honest_role, (name, res.winner, REASONS[res.reason])
        recs.append((name, gi, status, disputes, moves))
    recs += multi_scenarios(setups)
    for name, gi, status, disputes, moves in recs:
        ds = " ".join(f"[{'CDBPE'[a]}:{['cl', 'ch', 'void'][o]}/{REASONS[r]}/calls={c}]" for a, o, r, c, _ in disputes)
        print(f"{name:44s} status={['', 'open', 'rejected', 'accepted'][status]:9s} moves={len(moves):3d} {ds}")
    path = os.path.join(HERE, "game_moves.bin")
    with open(path, "wb") as f:
        f.write(b"TDG3" + bytes([len(games)]))
        for st in games:
            b = st.budget
            f.write(u32(len(st.data)) + st.data + u32(b.gates) + u32(b.ext_words) + u32(b.chunks) + bytes([st.case]))
        f.write(u32(len(recs)))
        for name, gi, status, disputes, moves in recs:
            nb = name.encode()
            f.write(u16(len(nb)) + nb + bytes([gi, status, len(disputes)]))
            for d in disputes:
                f.write(bytes(d))
            f.write(u16(len(moves)))
            for actor, mtype, target, body in moves:
                f.write(bytes([actor, mtype, target]) + u32(len(body)) + body)
    print(f"wrote {path}: {len(recs)} scenarios")


if __name__ == "__main__":
    main()
