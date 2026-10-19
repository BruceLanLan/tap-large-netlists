"""Empirical near-worst-case segment witness (spec 6.3). A file of 2^24 signals: nIn = 254, 65,534 ROM records
(a = 1, w = 256, 256 signals each), then 256 NAND records, each reading one signal of a different, evenly spread
signal word, so the last segments prove up to 256 scattered words of a 65,535-word tree. Prints the largest
segment witness and compares it with the analytic bound. Runs for about a minute."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "tap-draft-large-netlists"))
import tapnet2 as t2  # noqa: E402

t0 = time.time()
n_in, n_rom = 254, 65534
rec = t2.enc_rom([2], 256, bytes(range(64)))
body = bytearray(rec * n_rom)
fs = 2 + n_in + 256 * n_rom                      # first NAND output
words = fs // 256
step = words // 256
for k in range(256):
    x = 2 + n_in + k * step * 256 + 5            # one signal in word (k * step + 1)
    body += t2.enc_nand(x, x)
data = t2.make_file(bytes(body), n_in, 1)
nl = t2.decode(data)
print("file", len(data), "bytes, signals", nl.n_signals, f"({time.time() - t0:.0f} s)")
V, _, _ = t2.evaluate(nl, [], [1] * n_in)
store, nh2 = t2.oracle_for(data)
bounds = t2.segment_boundaries(nl)
tail = [i for i in range(1, len(bounds)) if bounds[i][1] > fs]
tuples = t2.tuples_for_boundaries(nl, V, bounds)
mx = 0
for j in tail:
    w = t2.build_seg_witness(nl, V, [], tuples[j - 1], tuples[j].byte_offset)
    ok = t2.verify_segment(store, nh2, t2.EMPTY, tuples[j - 1], tuples[j], w)
    sz = len(t2.serialize_seg_witness(w))
    mx = max(mx, sz)
    print(f"segment {j}: verify={ok} pWords={len(w.p_words)} pProofHashes={len(w.p_proof)} witnessBytes={sz}")
print("largest witness", mx, "bytes; analytic bound", t2.seg_witness_bound(), "; cap", t2.WITNESS_CAP,
      f"({time.time() - t0:.0f} s)")
