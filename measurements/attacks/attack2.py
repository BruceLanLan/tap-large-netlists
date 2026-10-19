"""Review attack 2 (unbound state tree size), re-run: 700 LATCHes (3 state words); the honest witness is
verified with every claimed stateSize 1..8; only the true size (3) is accepted."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "tap-draft-large-netlists"))
import tapnet2 as t2  # noqa: E402

nIn, L = 1, 700
recs = bytearray()
for i in range(L):
    recs += t2.enc_latch(2)          # latch i: d = input 0
recs += t2.enc_nand(3, 4)
data = t2.make_file(bytes(recs), nIn, 1)
nl = t2.decode(data)
state = [(i * 7) & 1 for i in range(L)]
V, o, ns = t2.evaluate(nl, state, [1])
sr = t2.state_root(state)
B = t2.DEFAULT_BUDGET
T = t2.tuples_for_boundaries(nl, V, t2.segment_boundaries(nl, B))
w = t2.build_seg_witness(nl, V, state, T[0], T[1].byte_offset)
store, nh2 = t2.oracle_for(data)
print("honest", t2.verify_segment(store, nh2, sr, T[0], T[1], w, B), "s_size", w.s_size, sorted(w.s_words))
accepted = []
for s in range(1, 9):
    w2 = copy.deepcopy(w)
    w2.s_size = s
    if t2.verify_segment(store, nh2, sr, T[0], T[1], w2, B):
        accepted.append(s)
print("accepted stateSize values:", accepted)
w3 = copy.deepcopy(w)
w3.s_words, w3.s_proof, w3.s_size = {}, [], 0
print("real stateRoot, state words removed:", t2.verify_segment(store, nh2, sr, T[0], T[1], w3, B))
print("stateRoot EMPTY with this (nonzero) state's witness:", t2.verify_segment(store, nh2, t2.EMPTY, T[0], T[1], w3, B))
