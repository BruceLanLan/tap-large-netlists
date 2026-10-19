"""Review attack 1 (witness size), re-run through the chunk-oracle path.

The reviewer's file: NAND filler, then two ROMs with a = 11, w = 256 whose addressed entries straddle chunk
boundaries, then NANDs. Under the old rules the honest witness of segment 1 carried 9 chunks (136,075 bytes,
over the 128 KiB transaction-pool limit). Now the verifier reads chunks from the store (the chunk oracle), so
the witness holds tree proofs only. Prints, per segment: verifier result, chunks read from the oracle, and
witness bytes (spec 6.3 encoding)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "tap-draft-large-netlists"))
import tapnet2 as t2  # noqa: E402

C = t2.CHUNK
nIn = 22
recs = bytearray()
sig = 2 + nIn


def nand(x, y):
    global sig
    recs.extend(t2.enc_nand(x, y))
    sig += 1
    return sig - 1


while 16 + len(recs) < C + 100:      # filler NANDs so that the first ROM header lands in chunk 1
    nand(2, 3)


def rom(addr_sigs, w=256):
    global sig
    a = len(addr_sigs)
    data = bytes(((i * 37 + 11) & 0xff) for i in range(t2.rom_data_len(a, w)))
    off = 16 + len(recs)
    recs.extend(t2.enc_rom(addr_sigs, w, data))
    s = sig
    sig += w
    return off + 4 + 3 * a, s


ds1, _ = rom(list(range(2, 13)))     # a = 11, w = 256 -> 64 KiB data
ds2, _ = rom(list(range(13, 24)))
for _ in range(3000):
    nand(2, 3)
data = t2.make_file(bytes(recs), nIn, 1)
nl = t2.decode(data)
print("file", len(data), "bytes,", t2.n_chunks(len(data)), "chunks")


def straddle_addr(ds):
    for k in range(1 << 11):
        lo = ds + 32 * k
        hi = lo + 31
        if lo // C != hi // C and lo // C >= ds // C + 2:
            return k
    return None


k1, k2 = straddle_addr(ds1), straddle_addr(ds2)
print("addresses", k1, k2)
inputs = [(k1 >> i) & 1 for i in range(11)] + [(k2 >> i) & 1 for i in range(11)]
V, outs, ns = t2.evaluate(nl, [], inputs)
store, nh2 = t2.oracle_for(data)
b = t2.segment_boundaries(nl)
tups = t2.tuples_for_boundaries(nl, V, b)
mx = 0
for j in range(1, len(tups)):
    w = t2.build_seg_witness(nl, V, [], tups[j - 1], tups[j].byte_offset)
    ok = t2.verify_segment(store, nh2, t2.EMPTY, tups[j - 1], tups[j], w)
    read = t2.LAST_READ["chunks"]
    sz = len(t2.serialize_seg_witness(w))
    mx = max(mx, sz)
    print(f"segment {j}: verify={ok} chunksRead={read} chunks={sorted(t2.segment_chunks(nl, V, tups[j - 1], tups[j].byte_offset))} witnessBytes={sz}")
ow = t2.serialize_end_witness(t2.build_out_witness(nl, V, outs, 0))
print("output beat-end witness bytes", len(ow))
print("max segment witness", mx, "bytes; bound under the default budget", t2.seg_witness_bound(),
      "; cap", t2.WITNESS_CAP, "; 128 KiB =", 131072)
