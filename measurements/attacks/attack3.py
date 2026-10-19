"""Review attack 3 (hidden middle checkpoint), re-run. The claimant forges only checkpoint t (8, 16, 24 or 32
of the 40-beat message run, K = 8); the last leaf is correct. The claimant must publish the full leaf list, the
referee derives checkpointRoot from it, and the challenger finds the first differing index in that public list
(no peeking at private data). A challenger that names an index whose leaves are equal loses."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "tap-draft-large-netlists"))
import bisect_sim as bs  # noqa: E402

ref, nh2, data, nl, inputs, K, budget, _ = bs.setup_game("message")
honest = bs.Party("honest", nl, inputs, K, budget)
for forged_t in (8, 16, 24, 32):
    liar = bs.Party(f"liar_t{forged_t}", nl, inputs, K, budget, {"kind": "checkpoint", "t": forged_t})
    published = liar.claim_data(nh2)[4]
    i = honest.choose_index(published)
    r = bs.run_one(ref, nh2, liar, honest)
    print(f"forged t={forged_t}: last leaves equal={published[-1] == honest.cp_leaves[-1]}; challenger names "
          f"index {i}; winner={r['winner']} via {r['final']} (rounds {r['totalRounds']})")
forger = bs.Party("liar_t24", nl, inputs, K, budget, {"kind": "checkpoint", "t": 24})
for guess in (1, 2, 3, 4):
    ch = bs.Party(f"challenger_names_{guess}", nl, inputs, K, budget, {"kind": "index", "i": guess})
    r = bs.run_one(ref, nh2, forger, ch)
    print(f"forged t=24, challenger names index {guess}: winner={r['winner']} ({r['reason']})")
