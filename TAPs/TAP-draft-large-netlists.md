---
tap: TBD
title: Large Netlists and Verifiable Execution
description: A netlist format with mask-ROM records, a chunked netlist hash with a staged on-chain upload, and the commitments, one-step verifiers and dispute rules that let a contract settle disagreements about runs of netlists far larger than one transaction.
author: Bruce (@BruceLanLan)
discussions-to: TBD (new Idea issue)
status: Draft
type: Standards
created: 2026-10-19
requires: TAP-02, ERC-165
license: CC0-1.0
---

# TAP-TBD: Large Netlists and Verifiable Execution

## Summary

This TAP lets very large circuits, such as a chip with millions of gates and built-in data tables, be stored on chain piece by piece, and lets anyone settle on chain a disagreement about what such a circuit computes.

## Abstract

This TAP defines `tapnet/2`, a netlist format that adds a mask-ROM record to the NAND and LATCH records of TAP-02, and `netlistHash2`, a hash over the file's 16,384-byte chunks, so that a file of up to 64 MiB can be identified by one value and stored on chain chunk by chunk through the optional interface `ITapLargeNetlistStore`. For runs far too large for any transaction, it fixes the commitments, a canonical segmentation of the file, one-step verifiers that check one segment of one beat against the stored file, and the data, acceptance and bisection rules of a dispute game, so that, under the assumptions stated in Security Considerations (collision-resistant SHA-256, a valid file, available data, inputs committed consistently), a wrong claim about a run is rejected if one party that computed correctly challenges it in time. Bond sizes and the lengths of timeouts are left to applications. An optional section describes how netlist-bound NFTs (`TAP-draft-netlist-bound-nft`) can use such netlists.

## Motivation

A TapeOut netlist is a list of NAND and LATCH records (TAP-02). A contract that has to identify such a file, store it or check what it computes is limited by one transaction: on BNB Smart Chain a transaction has at most 16,777,216 gas, enough to hash, store and check a file of some tens of kilobytes. The draft for netlist-bound NFTs (`TAP-draft-netlist-bound-nft`), for example, lets a contract store a netlist and run it only up to 49,152 bytes, the largest file its reference implementation can store and check for validity within one such transaction; a file of that size holds about 7,000 NAND records. That limit is not a property of NAND gates or LATCHes. It is a storage and transaction limit, and it rules out exactly the designs for which an on-chain identity and an on-chain check are most interesting:

- **Verifiable AI inference.** A language model written as a circuit, with its weights in ROM, produces each output token by a run of the circuit that anyone can recompute, and that a contract can decide when two parties disagree about it (the INT-C16 example below).
- **Provable game results.** A game written as a circuit lets a player claim a score after a run with given button inputs; anyone who disagrees can contest the claim, and a contract decides who is right. The cartridge demo of the reference implementation builds such a game as one `tapnet/2` file of 104,076 bytes (Reference Implementation).
- **Large designs for TapeOut processors.** Designs built from the records of TAP-02, such as processors, accelerators and lookup tables, outgrow one transaction as soon as they include memories and tables. A common format with ROM records and a chunked hash lets tools, explorers and contracts refer to such a design by one value.
- **NFTs.** A netlist-bound NFT can carry a large design and still be checked by tools and contracts (§8).

A concrete example is the INT-C16 design published by its author at https://jogjohgoeg.github.io/nand-llama-research/: a language-model chip described at the NAND level, for which the author reports a budget of about 1,012,501 NAND gates and 94,800 LATCH bits, with the model weights in 60 mask-ROM banks, about 98.2 million chip clocks per generated token, and a SKY130 area of 3.17 × 4.77 mm. The author states that the cycle counts and areas are source-level budgets and that there is no full-chip RTL timing or tape-out result yet; this TAP uses the figures only to size the problem. As NAND and LATCH records alone, before any ROM data, those counts take about 7.5 MB, about 152 times the 49,152-byte limit above (Appendix A). Three things are missing to identify, store and check such a design on chain:

1. **A way to write the weights.** Expanding a ROM into NAND multiplexers multiplies its size (the AES S-box of the test vectors: 300 bytes as one ROM record, 6,288 bytes expanded). Mask ROM is also what the hardware uses.
2. **A hash and an upload that fit transactions.** SHA-256 over 7.5 MB cannot be computed in one transaction; the memory expansion alone exceeds the gas cap. A Merkle root over fixed-size chunks can be checked one chunk per transaction.
3. **A way to trust an evaluation.** One generated token of INT-C16 takes about 98 million beats of about 1.1 million gates each (Appendix A). No chain can run that, so no contract can evaluate the netlist in one call, as the Eval extension of `TAP-draft-netlist-bound-nft` does for small files. What a contract can do is settle a *disagreement* about a run: if two parties disagree about the outputs of `T` beats, an interactive bisection narrows the disagreement to one segment of at most about 6,000 gates of one beat, which a contract can re-evaluate. This needs commitments, a segmentation and verifiers that every implementation agrees on byte for byte, which is why they are specified here rather than left to each game.

This TAP adds those three pieces as a standard of its own. It changes nothing in TAP-02 or in `tapnet/1`, and it depends on no NFT standard; §8 describes the optional use with netlist-bound NFTs.

## Specification

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119 and RFC 8174 when, and only when, they appear in all capitals.

### 0. Notation and scope

| Notation | Meaning |
|---|---|
| `‖` | byte string concatenation |
| `u8(x)`, `u16be(x)`, `u24(x)`, `u32be(x)`, `u64be(x)` | `x` as a 1-, 2-, 3-, 4- or 8-byte unsigned big-endian integer |
| `SHA-256` | FIPS 180-4 |
| `C` | the chunk size, 16,384 bytes |
| `EMPTY` | 32 zero bytes |
| `⌈x⌉`, `⌊x⌋` | ceiling and floor |
| `tree(leaves)` | the Merkle root over a non-empty leaf list defined below: `node = SHA-256(0x01 ‖ left ‖ right)`, an odd last node moves up unchanged, a one-leaf tree has the leaf as root |
| `tapnet/1` | the netlist file format of `TAP-draft-netlist-bound-nft` (format version byte `0x01`), referred to only in informative text and in §8 |

**Trees and proofs.** `tree(leaves)` is built level by level from the leaves: nodes `2j` and `2j+1` of a level are combined into node `j` of the next level as `SHA-256(0x01 ‖ left ‖ right)`; if a level has an odd number of nodes, its last node moves up to the next level unchanged. The root is the single node of the last level, and a one-leaf tree has the leaf as root. A proof for leaf `index` of a tree of `size` leaves is the list of sibling nodes from the leaf level upwards, omitting the levels at which the node moved up unchanged. Verifying a proof requires the root, the tree size, the index, the leaf and the proof:

```
verify(root, size, index, leaf, proof):
    if index ≥ size: fail
    h = leaf ; i = index ; n = size ; k = 0
    while n > 1:
        if i is odd:          h = SHA-256(0x01 ‖ proof[k] ‖ h) ; k = k + 1
        else if i + 1 < n:    h = SHA-256(0x01 ‖ h ‖ proof[k]) ; k = k + 1
        i = i >> 1 ; n = (n + 1) >> 1
    succeed if and only if k = len(proof) and h = root
```

(Informative: this is the tree and proof of the pool trees of `TAP-draft-netlist-bound-nft` §5.3, restated so that this TAP does not depend on that draft. A pool leaf starts with `0x00`, which no leaf of this TAP uses, §4.1.)

Where this TAP proves several leaves of one tree at once it uses the following **multiproof** format, which witnesses in the encoding of §6.3 MUST use (a contract that takes the fields in another form, §6.3, MAY use any multiproof from which the verifier recomputes the root from exactly the given leaves and the tree size). For a tree of `size` leaves and a set of known leaf indices, the proof lists, level by level from the leaves upwards, for each known node in increasing index order, the hash of its sibling, unless the sibling is itself known or does not exist (the known node is the odd last node of its level and moves up unchanged); a node is known at the next level if either of its children was known. A single-leaf multiproof is exactly the proof above. Verification consumes the proof in the same order:

```
multiverify(root, size, known: index → hash, proof) → bool
  width = size, p = 0
  while width > 1:
    next = {}
    for i in known, in increasing order:
      if ⌊i/2⌋ is in next: continue                       (its even sibling i − 1 was known)
      if i is even and i + 1 ≥ width:  h = known[i]       (odd last node moves up)
      elif i is even and i + 1 in known: h = SHA-256(0x01 ‖ known[i] ‖ known[i+1])
      elif i is even:  h = SHA-256(0x01 ‖ known[i] ‖ proof[p]), p = p + 1
      else:            h = SHA-256(0x01 ‖ proof[p] ‖ known[i]), p = p + 1
      next[⌊i/2⌋] = h
    known = next, width = ⌈width/2⌉
  return p = |proof| and known[0] = root
```

The verifier MUST fail if the proof is exhausted early, if hashes are left over, or if an index is not below `size`. Example: in a tree of 5 leaves `h0 … h4`, the multiproof of leaves 1, 2 and 4 is `(h0, h3)`: at level 0, leaf 1 needs `h0` and leaf 2 needs `h3`, while leaf 4 is the odd last node and moves up; at level 1 (width 3) the known nodes 0, 1 and 2 need nothing (node 2 moves up again); at level 2 both nodes are known.

This TAP adds:

- §1, the `tapnet/2` format, its validity, semantics and metrics;
- §2, the netlist hash `netlistHash2`;
- §3, an OPTIONAL on-chain storage interface `ITapLargeNetlistStore`, which also serves as the chunk oracle of the verifiers;
- §4–§6, the commitments, the canonical segmentation, the one-step verifiers and the encoding of their witnesses;
- §7, the data, data-availability requirements and rules a dispute game over `tapnet/2` evaluation MUST use;
- §8, OPTIONAL, the use of `tapnet/2` netlists by netlist-bound NFTs (`TAP-draft-netlist-bound-nft`).

§1 to §7 do not depend on §8 or on any NFT standard.

### 1. The `tapnet/2` format

#### 1.1 Layout

| Offset | Length | Content |
|---|---|---|
| 0 | 6 | ASCII `TAPNET` (`0x5441504e4554`) |
| 6 | 1 | Format version, `0x02` |
| 7 | 1 | Reserved, `0x00` |
| 8 | 4 | `nIn`, `u32be` |
| 12 | 4 | `nOut`, `u32be` |
| 16 | rest | records |

A record is one of:

| Record | Opcode | Encoding | Length | New signals |
|---|---|---|---|---|
| NAND | `0x00` | `0x00 ‖ u24(x) ‖ u24(y)` | 7 | 1 |
| LATCH | `0x01` | `0x01 ‖ u24(d)` | 4 | 1 |
| ROM | `0x10` | `0x10 ‖ u8(a) ‖ u16be(w) ‖ u24(addr[0]) ‖ … ‖ u24(addr[a−1]) ‖ data` | `4 + 3a + ⌈2^a · w / 8⌉` | `w` |

NAND and LATCH are encoded as in TAP-02 §2. The REF record of TAP-02 (opcode `0x02`) is not part of `tapnet/2`.

**Signals.** Signal 0 is the constant 0, signal 1 the constant 1, signals `2 … 2 + nIn − 1` are the inputs. Each record, in file order, creates its new signals at the next free indices: a NAND or LATCH one signal, a ROM `w` signals. `S` denotes the total number of signals. The outputs are the last `nOut` signals; output `j` is signal `S − nOut + j`. The LATCHes are numbered `0, 1, …` in file order (the **latch ordinal**); `nLatch` is their number, and `d_l` is the `d` reference of LATCH `l`.

**ROM data.** `data` is a bit stream packed LSB first (TAP-02 §5): bit `i` of the stream is bit `i mod 8` of byte `⌊i/8⌋`. Entry `k` (`0 ≤ k < 2^a`) occupies stream bits `k·w … k·w + w − 1`, its bit `j` at stream bit `k·w + j`. The address is LSB first: `addr[0]` is its least significant bit.

#### 1.2 Validity

A byte string is a valid `tapnet/2` file if and only if all of the following hold:

1. Its length is at least 16 and at most 67,108,864 bytes (64 MiB); bytes 0–5 are `TAPNET`, byte 6 is `0x02`, byte 7 is `0x00`;
2. `nIn ≤ 65,536` and `1 ≤ nOut ≤ 65,536` (the bounds of TAP-02 §3);
3. The records are not empty, and parsing them from offset 16 ends exactly at the end of the file. Every opcode is `0x00`, `0x01` or `0x10`; any other opcode, including `0x02`, makes the file invalid;
4. Every NAND input and every ROM address reference is smaller than the index of the first signal that record creates (constants, inputs and signals of earlier records only);
5. Every LATCH `d` is smaller than `S`;
6. `S ≤ 2^24`;
7. `S − 2 − nIn ≥ nOut`;
8. For every ROM: `1 ≤ a ≤ 20`, `1 ≤ w ≤ 256`, and if `2^a · w` is not a multiple of 8, the unused high bits of the last data byte are zero.

A file without ROM records is a valid `tapnet/2` file if it satisfies these rules. A `tapnet/1` file (version byte `0x01`, §0) is not a `tapnet/2` file, and a `tapnet/1` file can never contain a ROM.

Tools MUST reject invalid files and MUST NOT produce them. A tool MUST check the declared length of each record against the remaining file length before reading the record's references or data, and before allocating memory for it; in particular, a ROM's data length `⌈2^a · w / 8⌉` is computed from its header and checked against the file length before anything else of the record is read. Tools MUST check the file length (item 1) before reading the records.

Validity is a property of the whole file, checked off chain when an application commits to a file (for example in a registry, in a game's configuration, or in the manifest of a netlist-bound NFT collection, §8) and when a tool checks a file against its `netlistHash2`. The one-step verifiers of §6 parse only the records they need and do not establish validity.

#### 1.3 Semantics

One beat maps a state vector `state[0 … nLatch−1]` and an input vector `inputs[0 … nIn−1]` (bits) to the signal values `V[0 … S−1]`, the outputs and the new state. `V[0] = 0`, `V[1] = 1`, `V[2 + i] = inputs[i]`, and in file order:

- NAND `(x, y)`: `V = 1 − (V[x] ∧ V[y])`;
- LATCH number `l`: `V = state[l]`;
- ROM: `addr = Σ_{i<a} V[addr[i]] · 2^i`; output `j` (`0 ≤ j < w`) is stream bit `addr · w + j` of `data`.

The outputs are `V[S − nOut … S − 1]`, and `newState[l] = V[d_l]`. A ROM is combinational and has no state; the TAP defines no writable memory (RAM is expressed with LATCHes; see Rationale for what that costs). The initial state of a run is the all-zero state, as in TAP-02 §4; its state root is `EMPTY` (§4.3).

**Canonical expansion (informative).** Every ROM is equivalent to a NAND multiplexer tree over its address bits, and so every `tapnet/2` file has an equivalent netlist of NAND and LATCH records only, with the same `nIn`, `nOut`, latch order and behaviour, which the reference implementation writes as a `tapnet/1` file (§0). The reference implementation (`expand_rom_to_tapnet1`) copies NAND and LATCH records in order and replaces each ROM, for each output bit `j`, by the multiplexer tree that starts from the `2^a` constant bits of that column and combines pairs level by level, `addr[0]` first, with `mux(s, x0, x1) = x0` if `x0 = x1` and otherwise `NAND(NAND(x0, NOT s), NAND(x1, s))`, where `NOT x = NAND(x, x)`, constants are folded (`NAND(0, y) = 1`, `NAND(1, y) = NOT y`, `NAND(x, x) = NOT x`), and equal NAND and NOT gates are shared within one ROM. Each output bit then gets a fresh gate `NAND(NOT v, NOT v)` so that the `w` outputs are contiguous signals; LATCH references are renumbered; if the expanded outputs are not the last `nOut` signals, two inverters per output are appended. The test vectors give the SHA-256 and size of the expansion of each small example. Equivalence of behaviour, not the exact bytes of the expansion, is what this TAP relies on; a structural hash for `tapnet/2` is an open question.

#### 1.4 Metrics

The metrics of a valid `tapnet/2` file are `bytes` (its length), `nIn`, `nOut`, `nand` (number of NAND records), `latch` (number of LATCH records, equal to `nLatch` and to the state size), `romRecords` (number of ROM records) and `romBits = Σ 2^a · w` over all ROM records.

### 2. Netlist hash and metadata

#### 2.1 Chunks and `netlistHash2`

A file of length `len` is cut into `n = ⌈len / C⌉` chunks, `chunk_i` being bytes `i·C … min((i+1)·C, len) − 1`; only the last chunk can be shorter than `C`. Then

```
chunkLeaf(i)  = SHA-256(0x02 ‖ u32be(i) ‖ chunk_i)
chunkRoot     = tree(chunkLeaf(0), …, chunkLeaf(n − 1))
netlistHash2  = SHA-256(0x03 ‖ u64be(len) ‖ chunkRoot)
```

`netlistHash2` is the netlist hash of a `tapnet/2` file. It is not the SHA-256 of the file, and tools MUST NOT compare one with the other. A chunk `i` of a file with hash `h` is accepted if and only if: `SHA-256(0x03 ‖ u64be(len) ‖ chunkRoot) = h` for the declared `len` and `chunkRoot`; `i < n`; its length is `C` for `i < n − 1` and `len − (n − 1)·C` for `i = n − 1`; and its Merkle proof verifies `chunkLeaf(i)` at index `i` of a tree of size `n` with root `chunkRoot`.

A file is self-describing: byte 6 is its format version, and `netlistHash2` is the netlist hash of version `0x02`. (Informative: a `tapnet/1` file, version `0x01`, is identified by `SHA-256(file)` in `TAP-draft-netlist-bound-nft`; Rationale explains why the two hashes never collide.)

### 3. On-chain storage (optional)

A contract MAY store `tapnet/2` files. If it does, it MUST implement `ITapLargeNetlistStore` and report its ERC-165 identifier `0x8230af7f`.

```solidity
// SPDX-License-Identifier: CC0-1.0
pragma solidity ^0.8.28;

interface ITapLargeNetlistStore {
    function beginLarge(bytes32 netlistHash2, uint64 length, bytes32 chunkRoot) external;
    function putChunk(bytes32 netlistHash2, uint32 index, bytes calldata chunk, bytes32[] calldata proof) external;
    function largeStored(bytes32 netlistHash2) external view returns (bool);
    function largeChunk(bytes32 netlistHash2, uint32 index) external view returns (bytes memory);
    function largeInfo(bytes32 netlistHash2)
        external view returns (uint64 length, uint32 nIn, uint32 nOut, uint32 chunks, bool stored);

    event LargeBegun(bytes32 indexed netlistHash2, uint64 length, bytes32 chunkRoot);
    event ChunkStored(bytes32 indexed netlistHash2, uint32 index);
    event LargeStored(bytes32 indexed netlistHash2);

    error UnknownNetlist(bytes32 netlistHash2);
    error BadLength(uint64 length);
    error HashMismatch(bytes32 netlistHash2);
    error NotBegun(bytes32 netlistHash2);
    error BadChunk(uint32 index);
    error ChunkNotStored(uint32 index);
}
```

1. `beginLarge` MUST revert with `UnknownNetlist` unless `netlistHash2` is a known hash of the contract (item 7); with `BadLength` unless `16 < length ≤ 67,108,864`; and with `HashMismatch` unless `SHA-256(0x03 ‖ u64be(length) ‖ chunkRoot) = netlistHash2`. Otherwise it records `length` and `chunkRoot` for `netlistHash2` and emits `LargeBegun`. A repeated successful call for the same `netlistHash2` has no effect and emits nothing. Anyone MAY call it.
2. `putChunk` MUST revert with `NotBegun` if `beginLarge` has not succeeded for `netlistHash2`, and with `BadChunk` unless the chunk is accepted under §2.1. Otherwise it stores the chunk, emits `ChunkStored`, and, when this was the last missing chunk, emits `LargeStored`. Storing a chunk that is already stored has no effect and emits nothing. Anyone MAY call it, in any order of chunks.
3. `largeStored` returns true if and only if all `n` chunks of `netlistHash2` are stored.
4. `largeChunk` returns chunk `index` exactly as accepted, and MUST revert with `ChunkNotStored` if it is not stored (including when the file was never begun).
5. `largeInfo` returns `length` as recorded by `beginLarge` (0 if it has not succeeded); `nIn` and `nOut`, bytes 8–11 and 12–15 of chunk 0 read as `u32be` (0 while chunk 0 is not stored); `chunks`, the number of distinct chunks stored; and `stored`, equal to `largeStored(netlistHash2)`. A contract MUST record `nIn` and `nOut` when it stores chunk 0.
6. How chunks are stored is implementation-defined (informative: one SSTORE2 data contract per chunk; 16,384 bytes leave margin under the 24,576-byte code size limit). Who pays for an upload is outside this TAP; since anyone may call `beginLarge` and `putChunk`, an upload can be paid by the application, a claimant, or anyone else.
7. Which netlist hashes are **known** is defined by the contract that implements the store, for example hashes registered by an owner or committed by an application; for a collection or a companion of `TAP-draft-netlist-bound-nft`, they are its known netlist hashes (§8.2). (Informative: the reference store leaves the rule to a derived contract.)

A store for which `largeStored(netlistHash2)` is true is a chunk oracle for `netlistHash2` (§6). Function selectors, error selectors and event topics are listed in Test Cases.

### 4. Commitments

#### 4.1 Domain separation

Every hash in this TAP starts with one domain byte, except the digest `SHA-256(packedInputs_t)` inside an input leaf (§4.4), which is never used as a leaf or node itself:

| Byte | Use | Defined in |
|---|---|---|
| `0x00` | not used by this TAP (the pool leaf of `TAP-draft-netlist-bound-nft` §5.3) | |
| `0x01` | Merkle node (every tree of this TAP) | §0 |
| `0x02` | chunk leaf | §2.1 |
| `0x03` | `netlistHash2` | §2.1 |
| `0x04` | word leaf of signal values and of output values | §4.2, §4.3 |
| `0x05` | word leaf of LATCH state | §4.3 |
| `0x06` | checkpoint and beat leaf | §7.1 |
| `0x07` | tuple leaf (followed by a kind byte) | §4.5 |
| `0x08` | output hash chain | §4.4 |
| `0x09` | word leaf of the LATCH `d` table | §4.3 |
| `0x0A` | input leaf | §4.4 |

The kind byte after `0x07` is `0x00` (segment tuple), `0x01` (output tuple) or `0x02` (state tuple). (Informative: the draft `TAP-draft-netlist-nft-bridging` uses keccak-256 with string domains and the draft `TAP-draft-netlist-structural-hash` the ASCII prefix `tap-structural-hash/1`; neither collides with these.)

#### 4.2 Words and prefix roots

A **bit word** holds 256 bits: bit `o` (`0 ≤ o < 256`) is bit `o mod 8` of byte `⌊o/8⌋` of a 32-byte string. A **u32 word** holds 8 values: value `o` (`0 ≤ o < 8`) is `u32be` at bytes `4o … 4o + 3`. For a sequence `x[0 …]` of bits (or of u32 values), word `k` holds `x[k·256 + o]` (or `x[k·8 + o]`).

For a domain byte `dom`, a sequence `x` and a length `s`, the **prefix root** is

```
prefixRoot(dom, x, s) = EMPTY                                    if s = 0
                      = tree(wordLeaf(0), …, wordLeaf(m − 1))     otherwise
wordLeaf(k) = SHA-256(dom ‖ u32be(k) ‖ word_k),   m = ⌈s / per⌉
```

where `per` is 256 for bit words and 8 for u32 words, and word `m − 1` holds `x` only up to index `s − 1`, its unused slots zero. The tree grows with `s`; it is never padded to a fixed size.

**Append proof.** For a prefix of length `s`, let `full = ⌊s / per⌋` and `p = s mod per`. The append proof is the list of **peaks**, the roots of the perfect subtrees of the full words in the binary decomposition of `full` (largest first, one per set bit of `full`), and, if `p > 0`, the **partial word** (word `full`, unused slots zero). The root is recovered by `fold(h_1, …, h_r)`, where `h_1 … h_r` are the peaks followed by `wordLeaf(full)` of the partial word if present, `fold(h_r) = h_r` and `fold(h_i, …) = SHA-256(0x01 ‖ h_i ‖ fold(h_{i+1}, …))`; an empty list gives `EMPTY`. This equals `prefixRoot`. A verifier that accepts an append proof MUST check the number of peaks against `full`, the presence of the partial word against `p`, that every unused slot of the partial word is zero, and that the recovered root equals the expected root. From an accepted append proof, values can be appended one by one and the new prefix root recomputed, which is how §6 computes `P`, `D` and the beat-end roots.

#### 4.3 The commitments of one beat

For a beat with signal values `V`, outputs `out`, state `state` and new state `newState`:

| Name | Value | Kind |
|---|---|---|
| `P(s)` | `prefixRoot(0x04, V, s)` | bit words |
| `stateRoot` | `prefixRoot(0x05, state, nLatch)`; `EMPTY` in beat 0 | bit words |
| `D(l)` | `prefixRoot(0x09, (d_0, d_1, …), l)` | u32 words |
| `outRoot` | `prefixRoot(0x04, out, nOut)` | bit words |

`D` depends only on the file, not on the beat. The state root after the beat is `prefixRoot(0x05, newState, nLatch)`, the `stateRoot` of the next beat.

**The initial state.** As a state root, `EMPTY` denotes the all-zero state of any length. The state root of beat 0 of a run, whose state is all zero (§1.3), is `EMPTY` for every `nLatch`; it is not `prefixRoot(0x05, 0^nLatch, nLatch)`. The state root of every later beat is the prefix root above, also when that state happens to be all zero. A netlist without LATCHes has `stateRoot = EMPTY` in every beat, which agrees with both rules.

#### 4.4 Output hash chain and input commitment

```
io_0         = EMPTY
io_{t+1}     = SHA-256(0x08 ‖ io_t ‖ outRoot_t)
inputLeaf(t) = SHA-256(0x0A ‖ u64be(t) ‖ SHA-256(packedInputs_t))
inputRoot    = tree(inputLeaf(0), …, inputLeaf(T − 1))
```

where `outRoot_t` is the output root of beat `t` (beats are numbered from 0), and `packedInputs_t` is the input vector of beat `t` packed LSB first into `⌈nIn/8⌉` bytes (input `i` is bit `i mod 8` of byte `⌊i/8⌋`), its unused high bits zero; for `nIn = 0` it is the empty string. `io_t` commits to the outputs of beats `0 … t − 1`; `inputRoot` commits to the inputs of all `T` beats of a run (§7.1).

#### 4.5 Tuples and tuple leaves

A **segment tuple** is `seg = (byteOffset, firstSignal, latchOrdinal, P, D)`: a record boundary in the file, the index of the first signal created at or after it, the number of LATCHes before it, `P(firstSignal)` of the beat, and `D(latchOrdinal)`. An **output tuple** is `(ordinal, root)` with `root = prefixRoot(0x04, out, ordinal)`; a **state tuple** is `(ordinal, root)` with `root = prefixRoot(0x05, newState, ordinal)`.

```
segLeaf(j, seg)  = SHA-256(0x07 ‖ 0x00 ‖ u32be(j) ‖ u64be(byteOffset) ‖ u32be(firstSignal)
                           ‖ u32be(latchOrdinal) ‖ P ‖ D)
endLeaf(kind, k, (ordinal, root)) = SHA-256(0x07 ‖ u8(kind) ‖ u32be(k) ‖ u32be(ordinal) ‖ root)
```

with `kind = 1` for output tuples and `kind = 2` for state tuples. A tuple list is committed by `tree` over its leaves together with its length.

### 5. Canonical segmentation and budget

#### 5.1 Budget

A **budget** is a triple `(gates, extWords, chunks)`. A dispute game contract (§7) MUST expose

```solidity
function segmentBudget() external view returns (uint32 gates, uint32 extWords, uint32 chunks);
```

The budget of a claim is the value the game's `segmentBudget()` returns when the claim is made. The game MUST record that value with the claim and use the recorded value in every verifier call of the claim, so that it cannot change for that claim even if `segmentBudget()` later returns another value (an immutable value is RECOMMENDED as well). A verifier contract MAY also expose `segmentBudget()` as its default budget; a game that uses such a verifier MAY return the verifier's value from its own `segmentBudget()`, and still records it per claim (the reference game does both). `gates` bounds the evaluation work of one verifier call, `extWords` the words it proves, and `chunks` the chunks it reads from the chunk oracle (§6). The RECOMMENDED defaults are `(6000, 256, 16)`, chosen from measurements of the Solidity reference verifier: the costliest call measured takes about 9.3 million gas of execution, and a transaction that makes only that call, with a witness at the bound of §6.3, stays below about 11 million gas, which leaves room under a 16,777,216-gas transaction for the game's own work and for verifiers whose execution gas is up to about 1.5 times the reference's; `extWords = 256` keeps the witness bound of §6.3 below the 128 KiB transaction size limit of common clients (Appendix B).

#### 5.2 Cost of a segment

A segment starts at a record boundary with offset `o`, first signal `fs` and latch ordinal `l0`, and consists of consecutive records `r_1 … r_k`. Its cost has three components, each computed from the file alone, never from signal values:

- **gates** `= Σ g(r)`, with `g = 1` for NAND and LATCH and `g = w + a` for a ROM;
- **extWords** = the number of distinct elements of the union, over the records, of: for a NAND or ROM, `("P", ⌊x/256⌋)` for each reference `x` with `2 ≤ x < fs`; for a LATCH with ordinal `l`, `("S", ⌊l/256⌋)`. Constants (signals 0 and 1) are not reads, and references `x ≥ fs` are internal reads, which count nothing. The word `⌊(fs − 1)/256⌋` counts like any other `P` word, although the witness supplies it as the partial word of the append proof (§6.1);
- **chunks** `= |⋃ chunkSet(r.off, r.structEnd)| + Σ_{ROM r} min(|chunkSet(r.dataStart, r.end)|, 2)`, where `r.off` is the offset of the opcode, `r.end` the offset after the record, `r.dataStart = r.off + 4 + 3a` for a ROM, `r.structEnd = r.dataStart` for a ROM and `r.end` otherwise, and `chunkSet(s, e) = {⌊s/C⌋, …, ⌊(e − 1)/C⌋}`. The second term is not deduplicated across ROMs or with the first; it is an upper bound on the chunks a ROM read needs, since one read covers at most 33 bytes.

A cost is **within** a budget if each component is at most the corresponding budget component.

#### 5.3 Canonical segments

The **canonical segment** starting at `(o, fs, l0)` is built greedily: the record at `o` is always included, even if its cost alone exceeds the budget; then each following record is included as long as the cost of the segment including it is within the budget. The segment ends at the end of the file or before the first record whose inclusion would exceed the budget. Every segment therefore has at least one record, and a segment that does not end at the end of the file is **maximal**: adding its next record would exceed the budget.

The canonical segmentation of a file is the sequence of canonical segments starting at `(16, 2 + nIn, 0)`, each next one starting where the previous ended, until the end of the file. Its boundaries `(byteOffset_j, firstSignal_j, latchOrdinal_j)`, `j = 0 … m`, are the same in every beat; `byteOffset_m` is the file length, `firstSignal_m = S` and `latchOrdinal_m = nLatch`. In a beat, the **segment tuples** are `seg_j = (byteOffset_j, firstSignal_j, latchOrdinal_j, P(firstSignal_j), D(latchOrdinal_j))`. In particular

```
seg_0 = (16, 2 + nIn, 0, prefixRoot(0x04, (0, 1, inputs…), 2 + nIn), EMPTY)
```

is determined by the inputs of the beat, which the game obtains against `inputRoot` (§7.4).

**Informative.** A step (§6.1) reads from the chunk oracle the chunks of the segment's structural bytes and ROM reads, bounded by the `chunks` budget, plus the at most two chunks of the next record's structural bytes, so at most `chunks + 2` chunks whenever the first record of the segment fits the budget on its own (always the case under the default budget). The witness carries none of these bytes. Under the default budget the reference implementation measured segment witnesses of 48 to 1,068 bytes and at most 5 chunks read per step for the files of the test vectors (`measurements`); §6.3 gives the worst case.

### 6. One-step verifiers

The verifiers are deterministic functions of their arguments and of the chunk oracle of `netlistHash2`; they read no other state. "Fail" means the function returns false. A verifier MUST fail on any parse error, on any byte, word or proof element that it cannot obtain, on any reference to a signal that is neither a constant, nor covered by the witness, nor already computed in the segment, and on any malformed witness element.

**Chunk oracle.** The verifiers read the file only through a chunk oracle for `netlistHash2`: a source of `fileLen` and, for every `i < ⌈fileLen / C⌉`, of `chunk_i` of the file whose `netlistHash2` (§2.1) is the given one. On chain the oracle is a store of §3 for which `largeStored(netlistHash2)` is true: `fileLen` is `largeInfo(netlistHash2).length` and `chunk_i` is `largeChunk(netlistHash2, i)`; `putChunk` checked every chunk against `netlistHash2`. A verifier MUST fail if the oracle cannot supply the whole file (for a store: `largeStored(netlistHash2)` is false) or supplies a chunk whose length is not that of §2.1. Witnesses therefore contain tree proofs only, never file bytes. (Informative: an implementation without a store can realise the same oracle by taking chunks and a chunk multiproof as extra input and accepting them under §2.1; the result of every verifier call is the same, but such input costs the calldata of the chunks.)

#### 6.1 `verify_segment`

```
verify_segment(netlistHash2, stateRoot, a, b, witness, budget) → bool
```

`a` is the agreed segment tuple, `b` the claimed next tuple, `stateRoot` the agreed state root at the start of the beat, `budget` the budget of the claim (§5.1). The witness contains:

- an append proof for `P` at length `a.firstSignal` (peaks and optional partial word);
- a set of `P` words with indices, and a multiproof for them in the tree of `a.P`;
- an append proof for `D` at length `a.latchOrdinal`;
- `stateSize` (the number of words of the state tree), a set of state words with indices, and a multiproof for them in the tree of `stateRoot`.

The verifier returns true if and only if all of the following succeed, in this order:

1. the chunk oracle supplies the file of `netlistHash2`, with `fileLen ≤ 67,108,864`;
2. `16 ≤ a.byteOffset < fileLen`;
3. the append proof for `P` is accepted at length `fs = a.firstSignal` with root `a.P`, and the append proof for `D` at length `a.latchOrdinal` with root `a.D` (§4.2);
4. if the `P` append proof has a partial word, its index `⌊fs/256⌋` is not among the `P` words; the `P` words verify against `a.P` with tree size `⌈fs/256⌉` and leaf domain `0x04`. If `stateRoot = EMPTY`, `stateSize` is 0 and there are no state words and no state proof; otherwise the state words verify against `stateRoot` with tree size `stateSize` and leaf domain `0x05`. An empty word set MUST come with an empty proof;
5. the canonical segment starting at `(a.byteOffset, fs, a.latchOrdinal)` under `budget` (§5.3) is determined from chunks read from the oracle; this includes parsing the structural bytes of the record after the segment, unless the segment ends at `fileLen`; the record after the segment is only measured against the budget: its operand values are never read, so a missing word for that record MUST NOT make the call fail;
6. the records of that segment are evaluated in order, where the value of a signal `x` is: `x` itself if `x < 2`; the value computed earlier in this segment if `fs ≤ x`; otherwise bit `x mod 256` of word `⌊x/256⌋`, taken from the partial word if that is its index and from the `P` words otherwise:
   - NAND `(x, y)`: `1 − (value(x) ∧ value(y))`;
   - LATCH with ordinal `l`: 0 if `stateRoot = EMPTY`, otherwise bit `l mod 256` of state word `⌊l/256⌋`; its `d` is appended to the `D` accumulator;
   - ROM: `addr = Σ value(addr[i]) · 2^i`; the bytes `dataStart + ⌊addr·w/8⌋ … dataStart + ⌊(addr·w + w − 1)/8⌋` are read through the oracle, and output `j` is bit `(addr·w mod 8) + j` of that byte string (LSB first);
   - every value produced is appended to the `P` accumulator;
7. `b` equals `(end offset, end signal, end latch ordinal, P root, D root)` of the segment.

`b.byteOffset` is compared in step 7; it is never used to find the end of the segment. Because the segment is the canonical one, a `b` that ends one record early or one record late fails, whatever its values. The `EMPTY` rule of step 4 is selected by the `stateRoot` argument alone, never by the absence of state words: with any other state root, a LATCH whose state word is missing makes the verifier fail.

#### 6.2 Beat-end verifiers

The outputs and the new state are checked in steps of 256 values, against the final segment tuple `seg_m`, whose `P` is `P(S)` and whose `D` is the root of the whole `d` table:

```
verify_beat_end(netlistHash2, nOut, seg_m, a, b, witness) → bool
```

`nOut` is the file's `nOut`, a public parameter of the claim (§7.1, `largeInfo`). `a` is the agreed tuple of an output or state list, `b` the claimed next tuple. The witness contains `kind` (output or state), the `P` words needed with a multiproof in the tree of `seg_m.P`, the peaks of the append proof of the list at length `a.ordinal`, and, for `kind` state, the `D` words (u32 words) needed with a multiproof in the tree of `seg_m.D`. The verifier returns true if and only if:

1. the chunk oracle supplies the file of `netlistHash2`, and `seg_m.byteOffset = fileLen`;
2. with `S = seg_m.firstSignal`, the `P` words verify against `seg_m.P` with tree size `⌈S/256⌉` and leaf domain `0x04`;
3. for `kind` output: the witness has no `D` words and no `D` proof, and `1 ≤ nOut ≤ S − 2`; then `total = nOut` and `dom = 0x04`. For `kind` state: `total = seg_m.latchOrdinal`, `dom = 0x05`, and the `D` words verify against `seg_m.D` with tree size `⌈total/8⌉` and leaf domain `0x09`;
4. `a.ordinal` is a multiple of 256 and `0 ≤ a.ordinal < total`;
5. the append proof (peaks only, no partial word) is accepted at length `a.ordinal` with domain `dom` and root `a.root`;
6. with `hi = min(a.ordinal + 256, total)`, for `i = a.ordinal … hi − 1` the value of signal `S − nOut + i` (output) or of signal `d_i` (state, `d_i` read from slot `i mod 8` of `D` word `⌊i/8⌋`) is appended, where a signal's value is `x` for `x < 2`, bit `x mod 256` of `P` word `⌊x/256⌋` for `2 ≤ x < S`, and the verifier fails for `x ≥ S`;
7. `b = (hi, root after appending)`.

The output and state lists of a beat start with `(0, EMPTY)`, step by 256 values, and end with `(nOut, outRoot)` and `(nLatch, stateRoot of the next beat)` respectively. A netlist without LATCHes has the one-element state list `((0, EMPTY))`.

**The kind is bound by the caller.** The verifier takes `kind` from the witness and does not know which list `a` and `b` belong to; both lists start with `(0, EMPTY)`, so without that binding a state step could be offered for an output tuple, or the reverse. A caller MUST therefore check that the witness kind equals the kind of the list `a` and `b` come from before it calls the verifier; in a dispute, that is the list selected by the game (§7.4), and a witness of the other kind is rejected as stated there.

#### 6.3 Witness encoding and size

Witnesses are exchanged and published in the following encoding (the test vectors use it). With `hashes8` and `hashes16` a list of 32-byte hashes preceded by its count as `u8` or `u16be`, `words` a list of `u16be(count)` entries `u32be(index) ‖ word` (32 bytes) in strictly increasing index order, and `frontier` = `u8(number of peaks) ‖ peaks (largest first) ‖ flag ‖ [partial word]` with `flag = 0x00` (no partial word) or `0x01` (a 32-byte partial word follows):

```
segment witness  = frontier(P) ‖ words(P) ‖ hashes16(P multiproof) ‖ frontier(D)
                   ‖ u32be(stateSize) ‖ words(state) ‖ hashes16(state multiproof)
beat-end witness = u8(kind: 0x01 output, 0x02 state) ‖ words(P) ‖ hashes16(P multiproof)
                   ‖ words(D) ‖ hashes16(D multiproof) ‖ hashes8(peaks)
```

Multiproofs in this encoding MUST be in the format of §0. A verifier that takes an encoded witness MUST fail if the encoding is truncated, has trailing bytes, has a flag other than `0x00` or `0x01`, a kind other than `0x01` or `0x02`, or word indices that do not strictly increase. A contract MAY take the same fields in another form (for example ABI-encoded arrays); the verifier's result depends only on the fields.

**Size.** Every signal or state tree has at most 2^16 words (`S ≤ 2^24`; a file of exactly 2^24 signals has 65,536 signal words), and a multiproof of `k` words in such a tree has at most `Σ_{l=1}^{15} min(k, 2^{16−l}) + 2 − k` hashes, which is 2,048 for `k = 256`; the `D` tree has at most 2^21 words. A segment proves at most `max(extWords, 20)` `P` and state words in total: later records keep the segment within `extWords` (§5.3), but the first record is always included and can alone read up to 20 words (a ROM with `a = 20`; a NAND reads at most 2, a LATCH 1). Under the default budget a segment witness therefore has at most 84,208 bytes in this encoding, and a beat-end witness at most 92,810 bytes (a state step that reads 256 scattered `P` words); the largest honest witness measured is 74,896 bytes (Appendix B). A game MUST accept witnesses of up to 98,304 bytes (96 KiB) in this encoding, or the equivalent fields in another form; a game whose budget differs from the default MUST accept at least the bound that follows for its budget from the formula above with `k = max(extWords, 20)`. A game MAY reject longer witnesses.

### 7. Dispute data and rules

This section fixes what a dispute game about the evaluation of a `tapnet/2` file MUST use, so that independent games and provers agree on every value and a wrong claim cannot stand while an honest challenger is willing to challenge it. The economic layer (who may make or challenge a claim, bond sizes, the lengths of the challenge window and of the move deadlines within the rules of §7.1, rewards) is out of scope; Appendix D describes one arrangement. How the disputes of several challengers decide whether a claim is accepted is not economics, and §7.1 fixes it.

#### 7.1 Claims and data availability

A **claim** is `(netlistHash2, T, K, inputRoot, checkpointRoot)` with `1 ≤ T ≤ maxT` and `1 ≤ K ≤ maxK` (bounds, below). It states that running the file of `netlistHash2` for `T` beats from the all-zero state, with the inputs committed by `inputRoot` (§4.4), gives the checkpoint leaves committed by `checkpointRoot`. The public parameters of a claim are `fileLen`, `nIn` and `nOut` from `largeInfo(netlistHash2)` (§3), the budget (§5.1), and the input vectors of its `T` beats, which the application defines and which are committed by `inputRoot` as required below.

The **checkpoint times** are `0, K, 2K, …` (all multiples of `K` below `T`) and always `T`, in increasing order; `n` is their number. The **checkpoint leaves** are `L_k = checkpointLeaf(t_k, stateRoot_{t_k}, io_{t_k})` for the `k`-th checkpoint time `t_k`, with

```
checkpointLeaf(t, stateRoot_t, io_t) = SHA-256(0x06 ‖ u64be(t) ‖ stateRoot_t ‖ io_t)
checkpointRoot = tree(L_0, …, L_{n−1})
```

Because `stateRoot_0 = EMPTY` (§4.3) and `io_0 = EMPTY`, `L_0 = checkpointLeaf(0, EMPTY, EMPTY)` is a constant.

**Opening an output (informative).** A claim does not list its outputs; the output chain inside its checkpoint leaves commits them, and anyone can open the outputs of the beat before a checkpoint time from that checkpoint leaf with a few hashes (Appendix C). An opening shows what a claim says, not that it is right: an application should take an opened value only from an accepted claim (acceptance, below), and should design its circuit so that the result it cares about is held on the outputs of the last beat or of a beat before a checkpoint.

A game MUST NOT accept a claim unless:

1. `largeStored(netlistHash2)` is true in the store the game reads (§3); that store is the chunk oracle of every verifier call of the claim;
2. the claimant publishes the full list `L_0 … L_{n−1}` in calldata or in another medium the game names, and the game computes `checkpointRoot` from that list itself, or otherwise binds the two so that `tree(published list) = checkpointRoot` holds by construction. A `checkpointRoot` whose leaf list is not published MUST NOT be accepted;
3. the list has exactly one leaf per checkpoint time, and `L_0` is the constant above;
4. `inputRoot` is obtained in one of the two ways below, and the input vectors of all beats `0 … T − 1` are available before the challenge window opens;
5. `T ≤ maxT` and `K ≤ maxK` (bounds, below).

**Inputs.** Which inputs a claim is about is defined by the application (for example "the buttons the player pressed", or "the prompt tokens, then zeros"); they are a public parameter of the claim. The game MUST name one of two ways in which `inputRoot` is obtained:

- (a) **computed by the game.** The input vectors of all `T` beats are published on chain with the claim, in calldata the game reads, or fixed by a deterministic rule of the game, and the game computes `inputRoot` from them itself (§4.4), refusing the claim unless every vector has `⌈nIn/8⌉` bytes with zero unused bits. The published inputs and `inputRoot` then agree by construction. This is practical for small `T`; `maxT` bounds the work.
- (b) **supplied by an input provider.** `inputRoot` is supplied by an input provider that the game names (an operator, an oracle or a contract), which publishes the input vectors of all `T` beats in calldata, in a blob, or in another medium the game names, before a claim can use them. The input provider MUST NOT be the claimant: the game MUST refuse a claim made by the provider, and an application MUST NOT name as provider a party that also makes claims about the inputs it supplies (a provider that controls claimants is the claimant for this rule). Otherwise a claimant could commit inputs `Y` while publishing `X` and make its own claim unchallengeable (Security Considerations). A game that takes the inputs as calldata SHOULD check that they have `T · ⌈nIn/8⌉` bytes.

In both cases the input vectors MUST be available before the challenge window opens. In case (b) nothing on chain checks that the published inputs are consistent with `inputRoot`, and this TAP does not provide a proof of input inconsistency (Open questions). A challenger therefore checks that consistency off chain before it computes the run. A challenger that finds the published inputs inconsistent with `inputRoot` does not need to challenge at beat level, and should not: the beat-level dispute decides only whether the run on the inputs committed by `inputRoot` is right, and at level 3 the claimant reveals the committed inputs. Inconsistent inputs are a data-availability failure of the input provider, which the application has to handle by its own rules.

**Canonical inputs (informative).** `inputRoot` commits an input vector for every beat, also for beats whose inputs the circuit ignores (for example a CPU that reads its buttons only when it executes an input instruction). Different values on such beats give different `inputRoot`s for the same meaningful run, so two input providers, or a provider and a checker, could disagree about `inputRoot` while agreeing about everything that matters. An application should therefore fix canonical values, for example zero, for inputs the circuit ignores, and input providers should use them.

`K` SHOULD be chosen so that the list stays small; at most 2,048 leaves is RECOMMENDED (65,536 bytes of leaf hashes, so the list fits in one transaction below the 128 KiB transaction-pool limit of common clients). A game that accepts a longer list MUST define how the root is accumulated over several transactions. Informative: computing `checkpointRoot` from 2,048 leaves on chain takes about 2,047 calls of the SHA-256 precompile; for an INT-C16-scale run of about 9.8 × 10^7 beats this means K of about 48,000 beats.

**Bounds on `T` and `K`.** `T` and `K` are chosen by the claimant, and an unbounded choice lets a claimant make a claim that nobody can check or contest: a huge `K` forces each party to recompute up to `K` beats and build a beat tree of `K + 1` leaves within one move deadline (§7.3), and a huge `T` asks for a run nobody can recompute within the challenge window. A game MUST therefore fix upper bounds `maxK` and `maxT`, and MUST NOT accept a claim with `K > maxK` or `T > maxT`. `maxK` SHOULD be small enough that an honest party can recompute `maxK` beats and build the tree of `maxK + 1` beat leaves within the deadline of a level-2 commit move (below), and `maxT` small enough that an honest challenger can recompute `maxT` beats within the challenge window. With at most `maxLeaves` checkpoint leaves, `T ≤ maxK · (maxLeaves − 1)` already follows, so `maxT` need not exceed that value. An application MAY fix `T` and `K` outright.

**Challengers and acceptance.** A claim has a challenge window, which opens when the claim is made. Each challenge starts its own dispute between the claimant and that challenger, keyed by `(claim, challenger)` and decided by §7.2 to §7.4 alone; no dispute's winner depends on another dispute. A game MUST apply these rules:

1. it keeps taking new challengers until the window ends, whatever happened in other disputes about the claim, unless the claim has been rejected; a challenge within the window is never refused because another dispute about the claim is open or has ended. A game MAY start a new dispute while others about the claim are open (the reference game does);
2. if the claimant loses any dispute, the claim is rejected at once; a rejected claim takes no further challenges and is never accepted, and disputes about it that are still open end without a winner;
3. a claimant that wins a dispute does not thereby make its claim accepted;
4. a claim is accepted only when its challenge window has ended and no dispute about it is open or was won by a challenger.

A single-slot game, in which the first challenge closes the window, does not conform: a claimant could challenge its own wrong claim from a second address, lose on purpose or stall, and lock out every honest challenger. A game that uses bonds SHOULD settle them per dispute, so that no dispute's outcome depends on another: the claimant posts one bond with the claim and each challenger one with its challenge; when the claimant wins a dispute it receives that challenger's bond; the first challenger to win a dispute receives its own bond and the claimant's, and the claim is rejected; the challengers of disputes that end without a winner get their bonds back; when the claim is accepted, the claimant's bond is returned.

**Moves and timeouts.** A dispute is a sequence of moves in which exactly one of its two parties is due at any time, in this order: the challenger makes the level-1 move (§7.2); then, in every round of §7.3 and §7.4, the claimant moves first and the challenger second; the two moves that only one party makes, the reveal of tuple `h + 1` and the final call (§7.4), are made by the party named there. A game MUST give every move a deadline and MUST decide the dispute against the party that is due when its deadline passes without a valid move from it. Since only one party of a dispute is ever due, both parties cannot time out together. Deadlines and the challenge window SHOULD be measured in time (block timestamps), not in blocks, since the off-chain work of a move does not shrink with the block interval of the chain, and the deadline of a level-2 commit move (§7.3) SHOULD grow with `K`, as `base + perBeat · K`, where `base` covers any move and `perBeat` the recomputation of one beat and its leaf. Within these rules the lengths are left to the game. Within a round the second party sees the first party's move, which gives it no advantage: every value a party reveals is checked against its own commitments and its own earlier leaves, and against public data, never against the other party's move of the same round. The order matters only at level 3, where the challenger's reveal check 1 uses the public `seg_0`, which follows from the inputs revealed by the claimant.

**Failed moves.** A move fails in one of two ways, and a game MUST treat each failure as follows.

- **The party loses at once** if it fails to open a commitment as asked, or fails a check of §7.2 to §7.4. Failing to open a commitment means that a value it reveals does not verify by its Merkle proof against the commitment it belongs to (a root the party committed earlier or in the same move, or, at level 1, the published checkpoint list), that a revealed leaf carries a time other than the one asked for, or that it names an index outside the committed list. The checks are: at level 1, the index range, the proofs of `L_{i−1}` and `L_i`, and a posted leaf that equals `L_i` (§7.2); at level 2, the endpoints of a beat tree (§7.3); at level 3, the claimant's inputs, reveal checks 1 to 5 with the proofs of the first and last tuples, and a final call that returns false (§7.4).
- **The move is rejected** (it has no effect and the party stays due until its deadline) if it is malformed, is made out of turn or after the deadline, or re-supplies values whose hash the game already holds and they do not hash to it: the preimages of the agreed beat leaf at `t` and of the party's own beat leaf at `t + 1`, and the tuples passed to the final call (§7.4). A final call whose witness kind differs from the kind of the selected list is rejected too (§6.2, §7.4). A party that computed correctly can always supply these values, since only one value is acceptable and the game already holds its hash; a party that cannot supply them loses by timeout. (In a contract, a rejected move reverts.)

This TAP cannot enforce the validity of a file (§1.2) on chain. A claimant MUST check off chain that the file is valid before making a claim, and a game MUST be started only for netlists that the application committed to in advance (for example in the game's configuration, in a registry, or in the manifest of a netlist-bound NFT collection, §8) and whose files were checked off chain: on an invalid file, any challenger can beat any claimant (Security Considerations).

#### 7.2 Level 1: checkpoints

1. The challenger compares its own checkpoint leaves with the published list and names an index `i` with `1 ≤ i < n`: the first index at which its own leaf differs from the published one. It posts its own values `(stateRoot, io)` for time `t_i`. By naming `i`, the challenger accepts `L_{i−1}`.
2. In the same move the challenger supplies `L_{i−1}` as its preimage `(stateRoot_{t_{i−1}}, io_{t_{i−1}})`, which it knows because `L_{i−1}` equals its own leaf, the leaf `L_i`, and Merkle proofs of both against `checkpointRoot` (anyone can build them from the published list). The game computes `L_{i−1} = checkpointLeaf(t_{i−1}, …)` itself, so the leaf carries the right time. The challenger loses if `i` is out of range, if either proof does not verify (`BadCheckpointProof` in the reference game), or if `checkpointLeaf(t_i, stateRoot, io)` of the posted values equals `L_i`. Otherwise the disputed interval is `[t_{i−1}, t_i]`: both parties stand by `L_{i−1}` at `t_{i−1}`; at `t_i` the claimant stands by `L_i` and the challenger by its posted leaf.

#### 7.3 Level 2: beats

Let `t_lo = t_{i−1}` and `t_hi = t_i`. Both parties commit, the claimant first, `tree(checkpointLeaf(t, stateRoot_t, io_t))` for `t = t_lo … t_hi` (beat leaves, the same leaf function as checkpoints). The size of that tree is `t_hi − t_lo + 1`, fixed by the game from the checkpoint times; the parties do not declare it. With its commitment each party proves its two endpoints; a party whose beat leaf at `t_lo` differs from `L_{i−1}`, or whose beat leaf at `t_hi` differs from its own leaf at `t_i` (`L_i` for the claimant, the posted leaf for the challenger), loses. Bisection: while the interval `[lo, hi]` has `hi − lo > 1`, in one round the claimant and then the challenger reveal their leaf at a beat strictly between `lo` and `hi` that the game chooses, not a party (the midpoint `⌊(lo + hi)/2⌋`, rounded down, is RECOMMENDED; the reference game uses it), as its preimage `(stateRoot, io)` with a proof against their own beat root, the game putting the time into the leaf; if the leaves are equal the beat becomes `lo`, otherwise `hi`. Throughout, the leaves at `lo` agree and the leaves at `hi` differ. Bisection ends at a beat `t` whose leaves agree while the leaves at `t + 1` differ. The agreed leaf at `t` gives the agreed `stateRoot_t` and `io_t` (`stateRoot_0 = EMPTY`).

#### 7.4 Level 3: one beat

Level 3 starts with one commit round, the claimant first. The claimant's move begins with `packedInputs_t` and a Merkle proof of `inputLeaf(t)` at index `t` of a tree of size `T` against `inputRoot`. It loses if it does not reveal them (by timeout), if the proof does not verify, or if `packedInputs_t` is not `⌈nIn/8⌉` bytes with zero unused bits. The revealed inputs give the public `seg_0` of beat `t` (§5.3).

In its commit move, each party commits, for beat `t`, three tuple lists with their lengths: the segment tuples (`segLeaf`), the output tuples (`endLeaf` kind 1) and the state tuples (`endLeaf` kind 2). In the same move it reveals the first and the last tuple of each list with proofs against those lists, and re-supplies the preimages of two beat leaves whose hashes the game holds from §7.3: `(stateRoot_t, io_t)` of the agreed leaf at `t`, and `(stateRoot_{t+1}, io_{t+1})` of its own leaf at `t + 1`. The game checks each preimage against the leaf hash; a mismatch rejects the move (§7.1, failed moves). The first gives the agreed `stateRoot_t` and `io_t`, used by check 4 and by the final call; the second gives the party's `stateRoot_{t+1}` and `io_{t+1}`, used by checks 4 and 5. The party loses, without any verifier call, unless all of the following hold:

1. its `seg_0` equals the public `seg_0` of beat `t` (§5.3);
2. its last segment tuple has `byteOffset = fileLen`;
3. its first output tuple and its first state tuple are `(0, EMPTY)`;
4. its last output tuple has `ordinal = nOut`, and its beat leaf at `t + 1` has `io_{t+1} = SHA-256(0x08 ‖ io_t ‖ root)` with the agreed `io_t` and that tuple's `root`;
5. its last state tuple has `ordinal` equal to the `latchOrdinal` of its last segment tuple, and its beat leaf at `t + 1` has `stateRoot_{t+1}` equal to that tuple's `root`.

If both pass, the game picks the first of the lists segment, output, state whose last tuples differ. Last tuples are compared as values, `(byteOffset, firstSignal, latchOrdinal, P, D)` or `(ordinal, root)`, not as leaves: a leaf includes its index, and the last tuples of two lists of different lengths sit at different indices. Such a list exists, because equal last output and state tuples would give equal leaves at `t + 1` (checks 4 and 5). When the output or state list is picked, the last segment tuples are equal as values, and that common value is the agreed `seg_m`.

**Bisection in the picked list.** Let `n_A` and `n_B` be the last indices of the two lists and `h = min(n_A, n_B)`. In one round, the claimant and then the challenger reveal their tuple at index `h`, with a proof against their own list.

- If the tuples at `h` are equal (possible only when the lengths differ), the party with the longer list MUST justify the step `h → h + 1`. In one more move it reveals its tuple `h + 1` with a proof against its list, and it then makes the final call, with the agreed tuple `h` as `a` and its tuple `h + 1` as `b`. Since the agreed tuple at `h` is a final tuple (offset `fileLen`, or ordinal `total`), this step always fails.
- Otherwise, starting from `lo = 0` (index 0 is agreed after the reveal checks) and `hi = h`, bisection rounds as in §7.3 (the claimant revealing first, at an index the game chooses, RECOMMENDED `⌊(lo + hi)/2⌋`) end at an index `j` with equal tuples at `j − 1` and different tuples at `j`. The claimant's tuple `j` is checked from the agreed tuple `j − 1`, and the claimant makes the final call.

**Final call.** A dispute that reaches this point is decided by exactly one verifier call: `verify_segment(netlistHash2, stateRoot_t, seg_{j−1}, seg_j, witness, budget)` for the segment list, or `verify_beat_end(netlistHash2, nOut, seg_m, a, b, witness)` for an output or state list, both with the chunk oracle of the claim. The party whose tuple is checked makes the call and supplies the witness (§6.3); it wins if the verifier returns true and loses otherwise. With the witness it re-supplies the agreed tuple, its own checked tuple and, for an output or state list, `seg_m`; the game checks them against the leaves it holds. For an output or state list the witness kind (§6.3) MUST equal the kind of the picked list (§6.2). A mismatch of either kind rejects the call without calling the verifier (§7.1, failed moves): the party can still make a correct call before its deadline. Every other dispute ends earlier, at a reveal check, without a verifier call.

**Length.** A dispute that reaches a verifier takes at most `⌈log2 K⌉ + ⌈log2 h⌉ + 5` rounds, or `⌈log2 K⌉ + 6` when the tuples at `h` are equal; Appendix D counts them and notes the costs a game has to price in.

### 8. Use with netlist-bound NFTs (`TAP-draft-netlist-bound-nft`)

This section is OPTIONAL. It applies only to tokens and contracts that follow `TAP-draft-netlist-bound-nft` (called **core** in this section) and bind a token to a `tapnet/2` netlist; §1 to §7 do not depend on it. It relies on the hook of §8.3, which core does not contain yet (Backwards Compatibility).

#### 8.1 Metadata and pools

The `netlist` member of the `tap` object (core §7.1) of a token whose netlist is a `tapnet/2` file MUST contain:

| Member | Type | Content |
|---|---|---|
| `format` | string | `"tapnet/2"` |
| `netlistHash2` | string | §2.1, `0x` + 64 lowercase hexadecimal digits |
| `bytes`, `nIn`, `nOut`, `nand`, `latch`, `romBits`, `romRecords` | numbers | the metrics of §1.4 |
| `uris` | array | a non-empty array of URI strings of the file |

It MUST NOT contain `sha256`, and MUST NOT contain `structuralHash` (no structural hash is defined for `tapnet/2`). Pool entries (core §5.2) carry `netlistHash2` as the netlist value. A tool that checks such a token (core §7.4) MUST fetch the file, check that it is a valid `tapnet/2` file, compute `netlistHash2` from it and compare that with `tap.netlist.netlistHash2` and with the pool entry, and compare the metrics. With the hook of §8.3, core requires a tool that does not support this format to display the token's netlist as unverifiable, not as invalid.

#### 8.2 Relation to core interfaces

These are consequences of core with the hook of §8.3, not new requirements on core:

- At L0 (core §8.1) nothing changes: the metadata carries the hash and the URIs, and the size of the file plays no role.
- At L1 (core §8.2), `netlistHashOf` returns `netlistHash2` for a `tapnet/2` token. A `tapnet/2` netlist hash is **known** under the same rules as any netlist hash (core §8.2 item 4). A collection or a companion (core §8.2–§8.3) MAY store `tapnet/2` files under §3; the known hashes of §3 item 7 are then its known netlist hashes.
- `storeNetlist` never accepts a `tapnet/2` file: it accepts only files whose SHA-256 is a known netlist hash (and at most 49,152 bytes long), and the SHA-256 of a `tapnet/2` file is not its netlist hash. Hence `netlistFile(netlistHash2)` and `netlistOf` do not return `tapnet/2` files; §3 is how such a file is stored and read on chain.
- Because the file is never stored through `storeNetlist`, the Eval extension's functions revert for a `tapnet/2` token (core §8.4 item 3). A contract MUST NOT evaluate a `tapnet/2` file through `ITapNetlistEval`. A large netlist (any `tapnet/2` file, whatever its size) does not implement the Eval extension (`evalOf`, `stepOf`, `evalFile`, `stepFile`).
- A dispute game (§7) about a `tapnet/2` netlist of a collection is started only for netlists committed by a manifest of the collection (core §5) whose files were checked off chain (§7.1).

#### 8.3 The hook in core

Core currently assumes in several places that every netlist value is `SHA-256(file)` of a valid `tapnet/1` file: §2.2 (validity), §2.3 (the hash rule), §2.4 (metrics), §5.2 (every netlist hash in a pool has to belong to a valid `tapnet/1` file), §7.1 (`format` is `"tapnet/1"`, `sha256` is required), §7.3 items 2 and 3 (`tap.netlist.bytes` follows the `tapnet/1` size formula, and `tap.netlist.sha256` equals the pool entry), §7.4 (a tool checks the SHA-256 against `sha256`) and §8.2 item 4 (known netlist hashes and `storeNetlist`). The structural hash and bridging extensions are also written for `tapnet/1`. Using `tapnet/2` netlists with core needs one hook in core, which this TAP does not make; the text proposed for the discussion of core is quoted in Backwards Compatibility.

## Rationale

**A standard of its own, and a new format version.** Large netlists are needed wherever a contract has to identify, store or check a circuit (inference, games, processor designs, NFTs), so this TAP depends only on TAP-02, and netlist-bound NFTs use it through the optional §8. `tapnet/1` and its netlist hash are the byte-level identity of every netlist-bound NFT and must not change. A new version byte keeps tools for `tapnet/1` from misreading a ROM as an unknown opcode, makes the file self-describing, and lets the hash rule follow the format. Keeping REF out of `tapnet/2` avoids importing circuit references whose meaning depends on a processor.

**ROM opcode `0x10`.** Low opcodes are where TAP-02 is likely to add records; `0x10` leaves room and is easy to recognise. Coordination with TAP-02 is an open question.

**ROM limits.** `a ≤ 20` and `w ≤ 256` bound one ROM at 2^20 × 256 bits = 32 MiB, inside the 64 MiB file limit; `w ≤ 256` also bounds one read at 33 bytes and the gate cost of one ROM at 276, so that a single ROM always fits a segment of the default budget. Zero padding bits keep the file canonical: one content, one byte string, one hash.

**Gate cost `w + a` for a ROM.** A verifier pays per address bit (reading and combining a value) and per output bit (appending to the `P` accumulator), not per stored bit; the data itself is read from the store, which the `chunks` component bounds.

**Why not SHA-256 of the file.** On BNB Smart Chain one transaction has 16,777,216 gas, and hashing 7.5 MB in memory exceeds it many times over. A Merkle root over chunks can be checked one chunk at a time, and the same tree gives the verifiers random access to the bytes they need. The length is hashed into `netlistHash2` so that the chunk count and the last chunk's length are fixed. Leaves carry their index and a domain byte, so a chunk cannot be moved and a leaf cannot be confused with a node. The preimage of `netlistHash2` is 41 bytes starting with `0x03`, while every `tapnet/1` file starts with `TAPNET`; a `netlistHash2` therefore never equals the netlist hash of a valid `tapnet/1` file without a SHA-256 collision, and one pool field can hold both kinds.

**Why `C = 16,384`.** A chunk fits in one data contract under the 24,576-byte code size limit with margin, so a verifier can read it with one code copy. Since witnesses carry no chunks, the chunk size no longer affects witness size; a smaller chunk would raise the number of upload transactions and the depth of the chunk tree, and lower the bytes copied per read.

**Prefix roots that grow.** Signals are only appended, and a segment writes a contiguous range. A tree over the words of the prefix lets the verifier continue the root from an append proof of a few hashes, with no knowledge of `S`. Using the same rule for the state means the root at the end of the state list is the next beat's `stateRoot` with no conversion, and the root at the end of the output list is `outRoot`. `EMPTY` for the empty prefix gives every list a fixed first tuple.

**Segment identity: five fields.** Bisection compares tuples by index, so tuple `j` must mean the same boundary for both parties; a canonical segmentation makes it so. The byte offset is needed because ROM records have variable length, so the signal index alone does not locate a record. The latch ordinal locates the state bit of each LATCH. `D` commits the `d` references of the LATCHes before the boundary; `verify_segment` extends it as it passes each LATCH, so the agreed `seg_m.D` is a proven commitment to the whole `d` table, and the state verifier can read `d_i` for 256 LATCHes from a few words instead of scanning the file.

**A canonical, greedy, value-independent segmentation.** If the claimant chose its boundaries, the two parties' lists would not align, and a claimant could choose boundaries that no verifier can afford. The greedy rule is the simplest rule a verifier can check locally: it recomputes the segment and needs only the next record to confirm maximality. Computing the cost from the file alone makes the boundaries the same in every beat, so they can be computed once, and makes the verifier's work independent of the values a cheater supplies. The ROM chunk term is a static upper bound for the same reason: which chunk a ROM read touches depends on the address. The first record is always admitted so that segmentation always progresses, even under a budget smaller than one record.

**Three budget dimensions.** Gates bound the computation of the verifier; external words bound the Merkle proofs it checks and so the witness size (§6.3); chunks bound the chunks it reads from the store. Any one of them alone lets a crafted file exceed a transaction: a file of NANDs that each read a different old word, or of ROMs whose data straddles many chunks. A circuit with scattered fan-in gets shorter segments, which costs a few more bisection rounds, not soundness.

**Constants are not reads.** Signals 0 and 1 have fixed values; counting them would penalise files that use constants and buy nothing.

**`stateRoot` as an argument, and the unbound state tree size.** The state root comes from the agreed beat leaf, not from a tuple, so it is a verifier argument. The witness supplies the size of the state tree, and nothing binds it. This is sound. Compare the verifier's recomputation with the true tree from the root downwards. The recomputed root equals the true root, and wherever a recomputed hash equals a true hash, their preimages are equal unless SHA-256 collides. A node preimage starts with `0x01` and a leaf preimage with its leaf domain (`0x05` here), so a recomputed node can only equal a true node, and then its two children equal the true node's two children; a recomputed leaf can only equal a true leaf. Following the path of each claimed leaf down from the root, every step is such an equality, so each claimed leaf equals a true leaf, whose preimage carries the claimed index and the true word. Under a wrong claimed size, nodes may move up unchanged at other levels; that changes which true hash a recomputed hash is compared with, not the argument. The same argument covers every tree in this TAP.

**The initial state as `EMPTY`.** A game cannot compute the root of `nLatch` zero bits: `nLatch` is not in the header, and building the tree of up to 65,536 zero words is too much for one transaction. If the claimant supplied the initial root, it could pick one whose preimage only it knows: a challenger could not compute the run and would disagree at every step, the dispute would end at beat 0, and there the claimant, holding the preimage, would win. Defining `stateRoot_0 = EMPTY` makes `L_0` a constant and beat 0 checkable without state words. It opens no new attack: for `nLatch > 0` and `t ≥ 1`, a correct `stateRoot_t` is a tree root, a SHA-256 output, and equals `EMPTY` only for a preimage of 32 zero bytes. A party that claims `stateRoot_{t+1} = EMPTY` after a beat must end its state list with `(nLatch, EMPTY)`, and `verify_beat_end` computes a real root for that step, so the party loses (a test vector covers this). `EMPTY` is selected only by the `stateRoot` argument, which comes from a leaf both parties agreed on; an honest party agrees to `EMPTY` only at `t = 0` or when `nLatch = 0`, and in both cases every LATCH does read 0. The first new state is computed normally, so the state root after beat 0 is a real root.

**A published checkpoint list.** If a claim committed only `checkpointRoot`, a claimant could forge one middle checkpoint and keep the last one correct; a challenger that sees only the root would have to guess where the forged leaf is, and the published list removes the guess. With the list public, the challenger names the first differing index itself, so level 1 needs no bisection and no claimant move, and every leaf, not only the last, is open to challenge. The game derives `checkpointRoot` from the list so that the challenger's choice, made from the list, is always consistent with the root it is checked against.

**Committed inputs.** For about 10^8 beats the inputs cannot be stored or hashed on chain, and inputs known only to the claimant would make its claim unchallengeable. `inputRoot` commits them in one value; data availability lets every challenger compute the run; the game needs the inputs of one beat only, revealed against `inputRoot` before `seg_0` is checked. Where `T` is small the game can compute `inputRoot` from inputs published on chain, which rules out any inconsistency (§7.1, case a). Where it is not, someone has to supply `inputRoot`, and the game names who (case b). The provider must not be the claimant: a claimant that commits inputs `Y` and publishes `X` can compute its run correctly on `Y`, and every challenger that computes on `X` then loses at level 3, so the only safe move is not to challenge and the claim stands on inputs nobody else knows. A third-party provider can still be inconsistent, but it does not gain a claim from it, and a challenger can detect the inconsistency before it challenges. A proof that published inputs do not match `inputRoot` would need a dispute over the input data itself, with its own commitments and availability rules. This version leaves that out, keeps the beat-level dispute about the run on the committed inputs only, and lists such a proof as an open question.

**Reading chunks from the store.** Carrying the chunks of a step in the witness made honest witnesses as large as 136 KB for a valid file whose ROM reads straddle chunk boundaries, above the 128 KiB transaction size limit of geth-based mempools, so an honest prover could lose by being unable to submit. The verifiers now read the chunks the store already holds, which are bound to `netlistHash2` by `putChunk`; witnesses keep only tree proofs, bounded by the budget (§6.3). The `chunks` budget still bounds the reads.

**Beat-end steps of 256 values, aligned.** One step reads at most one word of the list it builds, its append proof needs no partial word, and the lists have `⌈nOut/256⌉ + 1` and `⌈nLatch/256⌉ + 1` tuples. Outputs and new state are checked against `P(S)` of the agreed `seg_m`, which is why the segment list is examined first.

**Sparse checkpoints and lazy refinement.** A claim publishes only every `K`-th beat's leaf. The per-beat leaves are computed only for the disputed interval, and the tuple lists only for the disputed beat. This moves the off-chain cost of fine-grained commitments from every claim to the rare disputed one. Always including `T` makes the final state and outputs part of every claim.

**The output chain.** A hash chain over output roots commits all outputs of a run in one 32-byte value per checkpoint, and a beat leaf can be checked against the previous one with one hash. Inputs are committed separately by `inputRoot`, since the game needs only those of the disputed beat.

**Losing at once, and rejected moves.** A party that cannot open its own commitment, or that fails a check, would lose by timeout anyway; deciding at once saves the other party a wait and makes the outcome of a failed move the same in every game. Values whose hash the game already holds are different: only one value is acceptable, and a party that computed correctly can always supply it, so a mismatch is treated like a malformed transaction and rejected. Rejecting also keeps a typo or a wrong encoding from costing an honest party the dispute, while a party that has no correct value can gain nothing from it and loses at its deadline. Only one party of a dispute is ever due, so the timeout rule needs no case for both parties missing a deadline.

**Several challengers, and acceptance apart from disputes.** A dispute decides only who of its two parties is right about one interval. If the first challenge closed the window, a claimant could open it with a second address and lose at once (an index out of range) or stall until timed out, and a wrong claim would be decided "for the claimant" before any honest party could act; the reference game of an earlier version of this draft could be beaten that way. Keeping the claim's status apart from its disputes removes the attack: winning a dispute only defeats that challenger, the window stays open for everyone, one challenger who wins rejects the claim, and acceptance waits for the end of the window and of every open dispute. Settling bonds per dispute keeps each challenger's risk to its own dispute; a sock puppet costs its owner a bond, gains nothing, and at most delays acceptance by the length of one dispute.

**Bounds on `T` and `K`, and deadlines in time.** The work of a level-2 commit move grows with `K`, the work of a challenger with `T`, and both are chosen by the claimant. Without bounds a claimant could pick `T = 2^50` and `K = 2^40` (1,025 checkpoint leaves, within the leaf cap): no challenger can build a beat tree of 2^40 leaves before its deadline, so the claim could not be contested. Bounds fixed by the game, and a commit deadline that grows with `K`, keep every move feasible for an honest party. Deadlines count time rather than blocks because the off-chain work behind a move (recomputing beats, building trees and witnesses of up to 96 KiB) takes the same time whatever the block interval, which differs between chains by an order of magnitude.

**Reveal checks before bisection.** The first and last tuples of each list are fixed by public data or by the party's own beat leaves. Checking them first makes index 0 agreed, ties the lists to the disputed leaves, and lets a party that publishes an inconsistent list lose at once, without a verifier call.

**Unequal list lengths.** A cheater can commit a list that is longer or shorter than the canonical one. If the lists agree up to the shorter length, the shorter list's last tuple is a final tuple, so the longer list must justify a step past the end of the file or past the last value, which no witness can do. An honest party never has to: a cheater's final tuple must carry the final offset or ordinal, which no earlier honest tuple has. For the same reason the game compares last tuples by value: the leaves of two last tuples always differ when the lists have different lengths, since a leaf carries its index, and comparing leaves would pick the segment list even when both parties agree on `seg_m` and disagree only about outputs or state.

**The witness kind is bound by the game.** The output and state lists share their first tuple `(0, EMPTY)` and their tuple shape, and `verify_beat_end` reads the kind from the witness. If the game did not bind the kind to the picked list, a party could justify a step of one list with the computation of the other, for example offer the first 256 state values as the first output tuple.

**No writable memory, and what RAM costs.** This version has no RAM record: state lives in LATCHes, and a RAM is built from LATCHes, write decoders and read multiplexers. The cartridge demo (Reference Implementation) shows the cost: its 256 bytes of RAM take 98,380 of the file's 104,076 bytes (2,048 LATCHes and 12,884 NANDs), while its program, a 256-entry ROM, takes 540. The all-zero initial state (§1.3) has a second consequence: a table cannot be preloaded into LATCH-based RAM, so a program writes its tables at run time, or the table goes into a ROM. A ROM serves one address per beat, so a design that fetches an instruction and looks up a table in the same beat needs a second ROM record with its own copy of the data. A writable memory record would need its own commitment (a word tree for the memory, with read and write proofs in the witness), its own budget component and its own state-root rule; a ROM with several address ports is simpler but does not remove the need for RAM. Both are left open (Open questions); LATCH-based state keeps the format and the verifiers small, and fits designs that keep their writable state in registers, as the INT-C16 budget does (94,800 LATCH bits).

**Why not on-chain evaluation or validity proofs.** The Eval extension of `TAP-draft-netlist-bound-nft` runs a netlist of up to 49,152 bytes inside one call; at 1.1 million gates per beat and about 98 million beats per generated token, no call can. Succinct proofs of such a run are possible in principle, but the proving cost at this size and the absence of a standard proof system on TapeOut chains make them a later option; a dispute game needs only SHA-256 and a contract that re-evaluates one segment of at most 6,000 gate-equivalents (under the default budget).

**Why dispute economics are out of scope.** Bond sizes, the lengths of timeouts and eligibility depend on the application (a game prize, a collection guarantee, a bounty) and on the chain; the data, the verifiers and the rules by which disputes decide a claim do not. Fixing only the latter lets several games share provers and verifiers, and keeps every conforming game safe against the single-slot attack whatever its economics.

## Backwards Compatibility

This TAP defines a new format and new interfaces and changes nothing that exists: TAP-02 circuits, `tapnet/1` files and their netlist hash keep their meaning. A tool that does not implement this TAP sees a format version byte `0x02` that it does not know and rejects the file, as it rejects any unknown version.

Netlist-bound NFTs (`TAP-draft-netlist-bound-nft`) can use `tapnet/2` netlists only with one hook in that draft; §8.3 lists the places it touches, and the text below is proposed for that draft's discussion. Until that draft adopts such text, a token bound to a `tapnet/2` netlist is outside the letter of its §5.2 and §7.1, and tools that follow that draft alone treat the token's metadata as nonconforming. §1 to §7 are not affected. `TAP-draft-netlist-bound-nft` is named here by its draft name; when editors assign it a number, the references to it, all of them informative or in §8, follow.

The proposed text is not a change made by this TAP and not part of its Specification; in that draft, the lowercase "may" and "must" below would be the RFC 2119 keywords.

> **Proposed text for core, §2.5 Other formats.** A Standards TAP may define another netlist format, identified by a byte 6 other than `0x01` and by its own `tap.netlist.format`, with its own netlist hash rule and metadata members. Its hash rule must be such that no hash it produces equals `SHA-256(f)` for a valid `tapnet/1` file `f` (for example by hashing a preimage that starts with a byte other than `T`). For a netlist in such a format, the netlist value in pool entries (§5.2), `netlistHashOf` and known netlist hashes (§8.2) is that hash, and the requirements of §2.2, §2.4, §5.2, §7.1, §7.3 items 2 and 3, and §7.4 that refer to `tapnet/1`, `SHA-256(file)`, `sha256` or the `bytes` formula apply with that format's validity, metrics, hash and members in their place. A tool that does not support the format must display the token's netlist as unverifiable, not as invalid. `storeNetlist`, the Eval extension and other TAPs written for `tapnet/1` apply to `tapnet/1` only unless they say otherwise.

`netlistHash2` meets the hash-space condition of this proposal: its preimage is 41 bytes starting with `0x03` (Rationale). Old tools are barely affected: a tool that does not know `tapnet/2` already fails on its `format`, and the SHA-256 of a `tapnet/2` file is never a known netlist hash, so `storeNetlist` and Eval stay safe.

## Test Cases

`assets/tap-draft-large-netlists/vectors.json` is generated by `make_vectors.py` from the reference implementation, reproducible byte for byte (`python3 make_vectors.py --check`), with SHA-256 `902afb750facf46f389d4ca20463cf23a004c98d319c4294c4ab73205b096015`. Bit strings list bit `i` at position `i`; segment tuples are `[byteOffset, firstSignal, latchOrdinal, P, D]` and beat-end tuples `[ordinal, root]`. Every witness is given in full as hex in the encoding of §6.3 (`witnessHex`), with its SHA-256 and size; the chunk oracle of a case is a store holding the file of its `example` (`oracle: "file"`), an empty store (`"none"`) or a store holding chunk 0 only (`"chunk0"`). Its sections are:

- `meta`: chunk size, the domain bytes of §4.1, the default budget `(6000, 256, 16)`, a tiny budget `(16, 4, 2)` used to force many segments, and `EMPTY`;
- `files`: four valid `tapnet/2` files with hex, SHA-256, metrics, chunk leaves, chunk root, `netlistHash2`, every chunk proof of multi-chunk files, and, for the small files, the SHA-256, size and gate counts of the canonical expansion (§1.3):

| File | bytes / chunks | nIn / nOut | nand / latch | romRecords / romBits | netlistHash2 |
|---|---|---|---|---|---|
| `sbox` (AES S-box, `a = 8`, `w = 8`) | 300 / 1 | 8 / 8 | 0 / 0 | 1 / 2,048 | `0xccf1241a62105e591275e5ac83c68e9593f59bc84fa7083b88d56cf9d81a570b` |
| `mul4` (4 × 4-bit product table) | 300 / 1 | 8 / 8 | 0 / 0 | 1 / 2,048 | `0xeebe110ecb2416ab0b400aa434cc84df1796e530cf86ea2b58b085ade242eac2` |
| `message` (6-bit counter addressing a ROM text) | 336 / 1 | 1 / 8 | 30 / 6 | 1 / 512 | `0xf5cd90005f48bc1eac7173c4f05c1af9710a1b6bd689d9377709c522627095b0` |
| `large` (synthetic, three ROMs) | 89,509 / 6 | 16 / 16 | 8,016 / 64 | 3 / 264,256 | `0x6abfe829d81de05e2997125e2aa31e24a8b4f3da306e2fee6621ade5e1a0758f` |

- `invalid`: 28 files, each rejected for one rule of §1.2 with the expected reason (bad header fields, no records, REF, unknown opcodes `0x03` and `0x11`, truncation of every record kind, forward references, LATCH beyond the last signal, too few signals for outputs, ROM `a` and `w` out of range, a ROM whose declared data runs past the end, nonzero padding bits); `invalidNotInHex` describes two files too large to include (over 64 MiB; more than 2^24 signals);
- `step`: one-beat results for all four files, including the 40-beat run of `message` that prints `Hello from tapnet/2 ROM!`;
- `segmentations`: for one beat of each file, the canonical boundaries, tuples, tuple-list root and per-segment costs under both budgets, and the output and state tuple lists;
- `verifySegment`: 79 cases (27 expected true), each with `a`, `b`, `stateRoot`, the budget, the oracle and the witness, and for positive cases the number of chunks read. Positive cases include a ROM read from a chunk no structural byte needs and beat 0 with `stateRoot = EMPTY`. Negative cases cover a wrong claimed `P`, `D`, `firstSignal` or `latchOrdinal`; a segment one record short and one record long; tampered `P` words, `P` frontier and state words; a wrong `stateRoot` and a wrong state tree size; `stateRoot = EMPTY` with state words in the witness, and the root of the zero state written out without state words; a file that is not stored or only partly stored; and verification under a different budget;
- `verifyBeatEnd`: 44 cases (6 expected true), each with `nOut`, with negative cases for a wrong claimed root or ordinal, a wrong `P_m` or `D_m`, tampered `P` or `D` words, a wrong `nOut` parameter, and a `seg_m` that does not end at the end of the file;
- `checkpoints`: the `message` run with `T = 40`, `K = 8`: times `0, 8, …, 40`, every leaf (the first is the constant `0x2770ce5f9266ec7331a4479dfdfe1f22f261a49d52d5f17c2f7b64865d4b6d0f`), `checkpointRoot` `0x3e5803cf23eeea6a2a6980cba5775eadc63f029177e3126ee62c834446dd3700`, and the packed inputs, input leaves and `inputRoot` `0x3eef60dacb1c17e3bc5919d3863def3018183fa30d255e79c156800501eb8fd0`;
- `store`: `beginLarge` at the length bounds 16, 17, 67,108,864 and 67,108,865, with an unknown hash and with a length that does not match the hash; an upload of `large` with `putChunk` before `beginLarge`, chunks in the order 5, 2, 0, 0, 4, 1, 3, `largeInfo` after each call, the events, and a tampered chunk;
- `disputes`: 49 simulated disputes on `message` and `large`: honest against honest; each class of cheater as claimant and as challenger (non-canonical segmentation, short and long; a wrong new-state bit; a wrong ROM output; a forged checkpoint; correct values with lied outputs plus an extra segment tuple after the end of the file; a lie in beat 0, decided with `stateRoot = EMPTY`; a claimed `stateRoot = EMPTY` after a beat); a lying claimant that withholds or misreports the inputs of the disputed beat; a forged middle checkpoint at each of `t = 8, 16, 24, 32` with a correct last leaf; and challengers that name an index whose leaves are equal. The honest party wins every dispute between an honest and a dishonest party; disputes that reach a verifier end in exactly one call after 6 to 10 rounds, counted as in Appendix D, the others at a reveal check without a call; honest against honest gives no dispute; a challenger naming an index with equal leaves loses;
- `measurements`: the witness bounds of §6.3, and per-segment costs, chunks read and witness sizes for `large` and for a 21,000-NAND file under the default budget.

Interfaces (selectors computed with `cast sig`, event topics with `cast keccak`; the ERC-165 identifier is the XOR of the function selectors):

| Item | Selector or topic |
|---|---|
| `ITapLargeNetlistStore` (ERC-165) | `0x8230af7f` |
| `beginLarge(bytes32,uint64,bytes32)` | `0xb0fc46a0` |
| `putChunk(bytes32,uint32,bytes,bytes32[])` | `0x806ca833` |
| `largeStored(bytes32)` | `0xe1a06945` |
| `largeChunk(bytes32,uint32)` | `0x75077164` |
| `largeInfo(bytes32)` | `0x260759cd` |
| `segmentBudget()` (verifier or game contract, §5.1) | `0x09683cea` |
| `UnknownNetlist(bytes32)` | `0x1dd49e73` |
| `BadLength(uint64)` | `0xf55c3a0f` |
| `HashMismatch(bytes32)` | `0x7d7e4bfb` |
| `NotBegun(bytes32)` | `0xc7be356f` |
| `BadChunk(uint32)` | `0xa46ef7e6` |
| `ChunkNotStored(uint32)` | `0x23f6aba8` |
| `LargeBegun(bytes32,uint64,bytes32)` | `0x9f1baa4bbf9155c50182876bbfc80a6ae68dc45e89869b743774f6a9148dcd0f` |
| `ChunkStored(bytes32,uint32)` | `0x0b105209a631d6f65bf6ca2cfee3327230af70d7ad1215e49d434cf751b7c7d8` |
| `LargeStored(bytes32)` | `0xbea4f9cc5ffc14f00d94052699882369f432531457ac3211008d81a630c3c573` |

## Reference Implementation

The Python reference implementation is in `assets/tap-draft-large-netlists/` (MIT), dependency-free Python 3.9 (`python3 make_vectors.py --check && python3 test_tapnet2.py`):

- `tapnet2.py`: encoding, validity and metrics of `tapnet/2`; one-beat evaluation; the canonical ROM expansion; the trees, proofs and multiproofs of §0; chunking and `netlistHash2`; a model of `ITapLargeNetlistStore`, which is also the chunk oracle; prefix roots and append proofs; input leaves; budgets and canonical segmentation; witness construction, `verify_segment`, the beat-end verifiers, checkpoints, the output chain and tuple leaves; the witness encoding of §6.3 and the witness size bounds. The verifiers use only their arguments and the chunk oracle, mirroring an on-chain verifier. `step` and `evaluate` accept either a decoded `Netlist` or the file bytes; a caller that runs many beats should decode once and pass the `Netlist` (the cartridge demo measured about 3 ms per beat that way against about 31 ms when passing bytes);
- `bisect_sim.py`: the dispute of §7 between simulated parties, with a referee that holds only on-chain data (the store, the claim and the published leaves) and never looks at a party's private values, including the cheater classes listed in Test Cases; it counts rounds as Appendix D does. Its claim layer (`ClaimGame`) applies the acceptance rules of §7.1 over several disputes, and its referee optionally enforces the bounds on `T` and `K` and the two input cases;
- `examples.py`, `make_vectors.py`, `test_tapnet2.py`: the example files, the vector generator and 49 tests, including equivalence of `tapnet/2` files and their expansions against the independent TAP-02 evaluator `assets/tap-02/reference.py`, verification of every vector from its hex alone, and the claim rules of §7.1 (sock puppets, several challengers, bounds, input cases).

The Solidity reference implementation, the cartridge demo and the measurements are in the reference repository `tap-large-netlists`, which also holds a copy of this TAP and of its assets; it will be linked at a fixed commit before Review (`<REPO_URL>`, commit `<COMMIT>`). It is not deployed and has not been audited. It contains:

- `contracts/src/ITapLargeNetlistStore.sol` and `contracts/src/TapLargeNetlistStore.sol`: the interface of §3 and a store with one SSTORE2 data contract per chunk; which hashes are known is left to the derived contract (`knownNetlist`), and `TapLargeNetlistStoreOwned` keeps an owner-registered set for tests;
- `contracts/src/TapLargeVerifier.sol`: `verifySegment` (§6.1), `verifyBeatEnd` for both kinds (§6.2) and `segmentBudget()` (§5.1), as view functions that read the file only through `largeInfo` and `largeChunk` of the store and take witnesses in the encoding of §6.3; `contracts/src/TapLargeHash.sol`: the hashes of §2.1 and §4;
- `contracts/src/game/TapLargeDisputeGame.sol`: an informative dispute game that wires §7 to the verifiers of §6 (one bond size, no fee; not production code). Disputes are keyed by `(claim, challenger)`; any number of challengers may join within the window, also while other disputes are open; the claim's status is kept apart from its disputes as §7.1 requires (a challenger's win rejects the claim, `accept` succeeds only after the window with no dispute open, disputes left open by a rejection are refunded through `refund`), and bonds are settled per dispute as §7.1 recommends. The game fixes `maxK` and `maxT`, uses timestamp deadlines (`moveTime`, and `moveTime + perBeatTime · K` for commitBeats), and takes its input case from its constructor: case (a) computes `inputRoot` in `claim` from the inputs in calldata, case (b) accepts only triples `(netlistHash2, T, inputRoot)` published by a fixed provider that can never claim. It records `largeInfo` and `segmentBudget()` with each claim, computes `checkpointRoot` from the published list (at most 2,048 leaves) and follows §7.2 to §7.4 as written, the game choosing every midpoint. `Challenged` is also emitted for a challenge that loses in the same transaction, followed by `Resolved`, which carries the reason; a final call emits `VerifierCalled`, with the list kind and index, just before;
- `contracts/test/`: replay of every `verifySegment`, `verifyBeatEnd`, `store` and `checkpoints` vector with the same result as the Python reference (including the number of chunks read by each positive step), a differential corpus of 1,968 random cases generated with the Python reference (`contracts/test/data/gen_diff.py`), fuzz tests, unit tests, and the gas measurements of Appendix B (`contracts/test/data/gen_gas.py` builds the synthetic files with the Python reference and checks every case with its verifiers);
- `contracts/test/game/E2E.t.sol` with `contracts/test/data/game_gen.py`: 76 end-to-end scenarios on the `message` example (case (a)) and the `large` example (case (b)). The generator plays the parties of `bisect_sim.py` against a Python mirror of the game and records every transaction; the test replays each from the party's own address and asserts every dispute's winner and reason, the claim's final status, every bond transfer, the index asked at every reveal and the number of verifier calls (one when a dispute reaches the final call, otherwise none). The scenarios cover every cheater class of Test Cases in both roles, wrong and withheld inputs, wrong checkpoint indices, timeouts at five kinds of move, failed openings and checks at each level, and six multi-challenger scenarios: a cheating claimant's sock puppet that loses at once or stalls, then an honest challenger who wins; two honest challengers in parallel; and an honest claimant against two cheating challengers in parallel;
- `contracts/test/game/MultiChallenger.t.sol`: the second review's two reproductions of the single-slot attack, which now fail as attacks, and the guards of the acceptance rules; `contracts/test/game/GameLimits.t.sol`: the bounds, both input cases, time-based deadlines, a claim with 2,048 leaves whose inputs an input provider published, and the costliest final calls of the gas suite sent through the game;
- `measurements/attacks/`: scripts that re-run the attacks of the first review of this draft against the current rules (`attack1.py` witness size, `attack2.py` state tree size, `attack3.py` hidden middle checkpoint) and measure a near-worst-case witness (`worst_witness.py`); `measurements/l1fee.md`: the L1 data fee snapshot of Appendix B.

**Costs.** Appendix B gives the measured gas of the store, the verifiers and the reference game, and a snapshot of the L1 data fee on OP Stack chains. In short: storing a 16 KiB chunk costs about 3.7 million gas per transaction; the costliest verifier call measured takes about 9.3 million gas of execution and about 10.6 million as the final move of the game; every move of the 76 scenarios stays below 16,777,216 gas. This TAP makes no claim about the monetary cost of uploads or disputes.

**Cartridge demo (informative).** `demo/cartridge/` of the reference repository builds TAPBOY-8, an 8-bit single-cycle CPU, as one `tapnet/2` file: 104,076 bytes (7 chunks), 13,604 NAND, 2,073 LATCH and one ROM of 256 16-bit instructions holding the program, with 256 bytes of LATCH-based RAM; it executes one instruction per beat. Under the default budget its canonical segmentation has 3 segments, with honest segment witnesses of 372 to 968 bytes. A test runs the circuit beat by beat against an independent instruction-level emulator, including all of the RAM. In a small game played on it, a player claims a score after 651 beats with `K = 32`; the score is held on the output pins after the program halts and is opened from the last checkpoint leaf (Appendix C). Claims that inflate the score, by a wrong RAM bit or by a wrong ALU gate, lose in 10 to 12 rounds with exactly one verifier call, and so does a cheating challenger against an honest claim.

## Deployments

This TAP depends on no deployed contract.

## Security Considerations

- **Assumptions.** Soundness of the verifiers and of the dispute rests on the collision resistance of SHA-256, on the domain bytes of §4.1, and on the index inside every leaf. Under those assumptions, on a valid file whose data is available, and with inputs whose published values match `inputRoot`, a party that computed every value correctly and follows §7 wins every dispute it takes part in, and, by the acceptance rules of §7.1, a wrong claim is rejected as soon as one such party challenges it within the window and plays its dispute to the end.
- **Validity cannot be enforced on chain.** No contract checks the validity of a 7.5 MB file, and the verifiers check only the records they touch; `largeStored` says that the chunks match `netlistHash2`, not that the file is valid. On an invalid file, a challenger can steer any dispute into a segment containing an invalid record, where every successor fails; since the checked tuple is the claimant's, any challenger beats any claimant, whether the claimant computed correctly or not. A game must therefore be started only for netlists that the application committed to in advance and whose files were checked off chain (§7.1); an application that commits an invalid file breaks its own commitment (for a netlist-bound NFT collection, core §5.2 with the hook of §8.3), and tools reject the file when they check it (§8.1). A proof of invalidity is an open question.
- **Data availability.** A party without the file, the inputs or the claimant's checkpoint leaves cannot compute values or witnesses, and a claim it cannot compute is unchallengeable in practice. §7.1 therefore requires `largeStored(netlistHash2)` before a claim is accepted, the full checkpoint list with the claim, and the inputs before the challenge window opens. During level 2 and level 3 the claimant's beat leaves and tuples are revealed on chain with their proofs.
- **Inputs from an input provider.** When `inputRoot` is supplied rather than computed by the game (§7.1, case b), a provider can publish inputs `X` while `inputRoot` commits other inputs `Y`. A challenger that computes the run on `X` disagrees with a claimant that computed it correctly on `Y`; at level 3 the claimant reveals `Y_t` against `inputRoot`, the public `seg_0` follows from `Y_t`, and the challenger's tuples, computed from `X`, fail the reveal checks or the final call (the reference implementation reproduces this). A claimant that were its own provider could do this on purpose and make its claim unchallengeable, which is why §7.1 forbids it. Against a third-party provider, a challenger must check the published inputs against `inputRoot` before it challenges, and must not challenge at beat level when they disagree. No proof of the inconsistency exists in this version (Open questions); the application decides what follows from it, for example by naming only input providers it trusts, or by computing `inputRoot` on chain where `T` allows (case a).
- **Liveness and compute.** A dispute only protects those who take part, and a claim nobody checks stands. An honest challenger has to recompute the run to find a wrong leaf: for a design like INT-C16, about 1.1 million gates per beat and about 98 million beats per generated token, that is about 1.1 × 10^14 gate evaluations per generated token, about 30 hours at 10^9 gate evaluations per second. An honest claimant must, when challenged, recompute up to `K` beats of the disputed interval and the tuple lists of one beat, and answer every move in time. The bounds `maxT` and `maxK`, the challenge window, the move deadlines and the bonds must cover this off-chain compute, the on-chain cost of the moves, and witnesses of up to 96 KiB (§6.3); deadlines counted in blocks would shrink with the block interval, which is why §7.1 recommends time.
- **Sock-puppet challenges.** A claimant can challenge its own claim from other addresses. In a game that let one challenge close the window, a sock puppet that loses at once or stalls would settle a wrong claim before any honest challenger could act; §7.1 rules such games out. Under §7.1 a sock puppet only defeats itself: the window stays open, an honest challenger can still challenge, and the claim is accepted only after the window with no dispute open or won by a challenger. Each sock puppet costs its owner a bond (to itself, if bonds are settled as §7.1 recommends) and the gas of its moves, and can delay acceptance by at most the length of one dispute started before the window ends.
- **Delay and parallel challenges.** Every challenger that joins before the window ends can delay acceptance by one full dispute, and disputes run in parallel, so the delay is bounded by the window plus the longest dispute, not by the number of challengers. Each challenger can steer its dispute to a different checkpoint interval, so a claimant may have to recompute up to `K` beats for each of them in parallel; `K` is chosen by the claimant but bounded by the game (`maxK`, §7.1), and bonds make losing challenges costly. An honest claimant has to answer every dispute in time; a game should size its deadlines for the number of disputes it allows in parallel.
- **Who pays for the upload.** Out of scope (§3). Anyone may upload; front-running `putChunk` is harmless, since the front-runner pays and the original call becomes a no-op, and `beginLarge` cannot be front-run with other values, since they must match `netlistHash2`.
- **Canonical segmentation matters only when a value is wrong.** Segment tuples are revealed only after checkpoint leaves differ, that is, after some value is wrong. Then a party whose values are correct but whose segmentation is not canonical can lose, because the checked tuple must be the canonical successor. Provers therefore have to use the canonical segmentation of §5.3 under the claim's budget.
- **Griefing through lists.** A cheater can commit lists of any length or with arbitrary unopened subtrees. Bisection costs grow only with the logarithm of the shorter list, the longer list must justify any step beyond the shorter one (§7.4), and a party that cannot open an index it committed loses. The size of the beat-leaf tree is fixed by the game (§7.3); games can also bound tuple list lengths (indices are 32-bit).
- **`netlistHash2` and `SHA-256(file)`.** The verifiers and the store check only `netlistHash2`. A published `SHA-256(file)` of a `tapnet/2` file is not checked by anything and is not its identity; tools compute `netlistHash2` from the file. `netlistHash2` binds the length and every chunk, so two different files have different `netlistHash2` unless SHA-256 collides.
- **Witness size and parsing.** Verifiers bound their work by the budget, check record lengths against the file length before reading, and fail on any missing data; a crafted file can make segments short but cannot make a verifier exceed its budget, except for one first record, whose size the format bounds (a ROM header has at most 64 structural bytes, a gate cost of at most 276 and at most 20 external words, which §6.3 includes in the witness bound). Witnesses carry no file bytes, and their size is bounded by the budget (§6.3), so a crafted file cannot force an honest witness above the cap a game must accept.
- **No exclusivity.** A stored `tapnet/2` file is public: anyone can read the chunks, evaluate the design and refer to the same file elsewhere. A `netlistHash2`, and a claim or a token that names it, proves which file is meant, not exclusive use of the design or its weights.
- **Budget changes.** A budget that changed during a claim would change the canonical segmentation under the parties' feet; §5.1 fixes it per claim.

### Open questions

1. Should the ROM opcode stay `0x10`, or be coordinated with TAP-02 so that a future TAP-02 version can use the same record?
2. How should a structural hash treat ROM: as one node labelled by its content hash, or by its canonical expansion?
3. The exact wording of the hook in `TAP-draft-netlist-bound-nft` proposed in §8.3, to be settled in that draft's discussion.
4. The budget values and the witness cap: `(6000, 256, 16)` and 96 KiB are chosen from the gas of the Solidity reference verifier (Appendix B). Should they be revisited once other verifiers exist? A first snapshot of the L1 data fee on OP Stack chains (Appendix B) found it small next to L2 execution: below 1% on Base, and zero on X Layer, whose scalars are 0. That snapshot was taken at very low Ethereum blob fees, and should be repeated before the values are fixed.
5. Should the dispute game (claims, bonds, timeouts, eligibility) become a separate Application TAP built on §7?
6. Should `tapnet/2` or a later version add a RAM record (a writable memory with its own word tree, read and write proofs and budget component) or a multi-port ROM (one record that serves several addresses per beat)? LATCH-based RAM dominates the size of small processor designs (Rationale: 98,380 of 104,076 bytes for 256 bytes of RAM in the cartridge demo), and the all-zero initial state forces tables to be written at run time or duplicated in ROM.
7. Invalidity proofs: all validity rules of §1.2 except items 5 and 7 are local to a record, and `S` and `nLatch` follow from an agreed `seg_m`, so the segment framework could prove that a file is invalid and void a claim instead of letting the challenger win. Should a later version define such a proof?
8. Input inconsistency: when `inputRoot` is supplied by an input provider (§7.1, case b), should a later version define a proof that the published inputs do not match `inputRoot`, for example a bisection over the input leaves against data in blobs, so that a claim with inconsistent inputs can be voided on chain?

## Appendix A: Sizing INT-C16 (informative)

The figures are the author's source-level budgets for INT-C16 (Motivation) and are used only to size the problem.

- **File size.** As NAND and LATCH records alone, `16 + 7 · 1,012,501 + 4 · 94,800 = 7,466,723` bytes, before any ROM data: about 152 times the 49,152-byte limit of `TAP-draft-netlist-bound-nft`, or 456 chunks of 16,384 bytes. The ROM data of the weights comes on top.
- **ROM against NAND.** The 256-entry AES S-box of the test vectors takes 300 bytes as one ROM record (`a = 8`, `w = 8`) and 6,288 bytes, 896 NAND records, as its canonical expansion (§1.3).
- **Work per generated token.** One beat per chip clock gives about 98.2 million beats per generated token, each evaluating about `1,012,501 + 94,800 ≈ 1.1` million gates: about `1.1 × 10^6 · 9.8 × 10^7 ≈ 1.1 × 10^14` gate evaluations per generated token, about 30 hours at 10^9 gate evaluations per second.
- **Checkpoints.** With at most 2,048 checkpoint leaves (§7.1), a run of 9.8 × 10^7 beats needs `K ≥ 9.8 × 10^7 / 2,047 ≈ 48,000`. Recomputing one such interval is about `48,000 · 1.1 × 10^6 ≈ 5.3 × 10^10` gate evaluations, under a minute at 10^9 per second, which is the scale `maxK` and the level-2 commit deadline have to allow (§7.1).

## Appendix B: Measurements (informative)

**Setup.** All gas figures are measured with the Solidity reference implementation (Reference Implementation) and Foundry 1.8.3, solc 0.8.28 with the optimizer at 10,000 runs, legacy code generation (no via-IR) and `evm_version` Shanghai; the gas of these operations is the same under the Osaka rules. Execution gas is the gas of the call to the store or verifier made from a separate contract, with the store and the chunk data contracts cold and the precompiles warm. Calldata gas is computed at 16 gas per nonzero and 4 per zero byte, and a transaction estimate adds 21,000; the EIP-7623 calldata floor is never the larger value for these calls. Game figures are per transaction, including the 21,000 intrinsic gas and calldata (in Foundry every top-level call from a test runs as its own transaction).

| Operation | Execution gas | Calldata | Transaction estimate |
|---|---|---|---|
| `beginLarge` | 52,902 | | |
| `putChunk`, one 16,384-byte chunk | 3,368,007 to 3,370,176 (about 206 per byte, mostly the code deposit) | 16,772 bytes, about 267,000 gas | about 3.66 million |
| upload of a 7,466,723-byte file (456 chunks) | 1.54 × 10^9 | | about 1.67 × 10^9 in all |
| `largeChunk`, one 16,384-byte chunk | 15,512 | | |
| `verifySegment`, 6,000 NANDs with local inputs | 4,256,429 (about 700 per NAND) | 612 bytes | 4,281,245 |
| `verifySegment`, 6,000 LATCHes (28 state words) | 5,075,367 (about 840 per LATCH) | 1,572 bytes | 5,115,483 |
| `verifySegment`, 6,864 gate-equivalents of ROMs (`a = 8`, `w = 256`, no chunk limit) | 2,603,746 | 612 bytes | 2,628,574 |
| `verifySegment`, 256 NANDs each reading a different scattered word (deepest signal tree) | 1,756,201 | 67,716 bytes | 2,847,049 |
| `verifySegment`, 6,000 NANDs reading 256 scattered words of the deepest signal tree | 9,304,447 (about 1,290 per NAND) | 75,844 bytes | 10,524,815 |
| the same with 7,000 NANDs (the former default `(7000, 256, 8)`) | 10,588,166 | 75,844 bytes | 11,808,534 |
| `verifyBeatEnd`, 256 outputs | 322,680 | 1,028 bytes | 356,528 |
| `verifyBeatEnd`, 256 LATCHes whose `d` read 256 scattered words | 2,311,541 | 73,156 bytes | 3,485,589 |

The deepest signal tree is that of the measurement file `wideA` (`contracts/test/data/gen_gas.py` of the reference repository), which has 16,777,024 signals, so its signal tree has 65,536 words in 16 levels, the most the format allows; its scattered reads are spread over the whole tree. Each extra chunk a step reads adds about 16,000 gas.

**The default budget.** Under `(6000, 256, 16)` the costliest call measured is the 6,000-NAND segment above, 9.3 million gas of execution. The `chunks` budget of 16 adds at most about 0.3 million to it, and a witness at the bound of §6.3 (84,208 bytes) about 1.35 million of calldata, so a transaction that makes only that call stays below about 11 million gas, about 65% of 16,777,216 and 37% of 30,000,000. The headroom of §5.1 refers to execution gas: a verifier whose execution gas is 1.5 times the reference's (about 14 million in this case), with the same calldata, chunk reads and the game's own work (about 62,000 gas in the reference game), stays at about 15.7 million, below 16,777,216; it does not mean that every transaction may cost 1.5 times as much. The former default `(7000, 256, 8)` took about 11.8 million in the same case (about 12 million with a witness at the bound), which that estimate would push above 16,777,216. `extWords = 256` keeps the witness bound below the 128 KiB transaction size limit of common clients, and since a chunk read costs about 16,000 gas, `chunks = 16` costs little and halves the number of segments of files with many small ROMs compared with 8.

**Witnesses.** Under the default budget the segment witnesses of the test-vector files measured 48 to 1,068 bytes, with at most 5 chunks read per step. The largest honest witness measured, 74,896 bytes (`measurements/attacks/worst_witness.py` of the reference repository), is that of a constructed file of 2^24 signals whose last segments prove 256 scattered words of the 65,535 words before them; the gas measurements used a 75,280-byte witness of the same kind (about 1.2 million gas of calldata at 16 gas per nonzero byte; 98,304 bytes would cost at most about 1.6 million).

**Reference game.** Every move of the 76 end-to-end scenarios stays below 16,777,216 gas. Moves other than the final call cost at most about 398,000 gas: the largest is a case-(a) claim of the `message` example with `K = 1` (41 leaves, and `inputRoot` computed from 40 input vectors); claims of the examples cost 207,831 to 398,494, tuple commitments up to 343,919, `publishInputs` up to 55,440, `accept` up to 54,722 and `refund` 59,048. The largest single move in the scenarios is the final `proveSegment` of the `large` example, 4,454,511 gas. A single dispute that reaches the verifier costs 1.4 to 5.8 million gas for all parties together (1,376,753 to 5,812,990, including the claim, the inputs' publication in case (b), and the `accept` when the claimant wins); disputes decided by a failed check, a failed opening or a timeout cost 0.45 to 1.9 million. Sent through the game, the costliest final call of the gas suite, a default-budget segment of 6,000 NANDs reading 256 scattered words of the deepest signal tree with a 75,280-byte witness, costs 10,586,413 gas, and the costliest state step 3,533,662. A claim with 2,048 leaves costs 3,326,562 gas, after the input provider's `publishInputs` of its 65,504 input bytes (710,220). The per-scenario figures are written to `contracts/test/data/game_gas.csv` of the reference repository.

**L1 data fee (snapshot).** On 2026-10-18 (UTC), `getL1Fee` of the `GasPriceOracle` predeploy was called (`eth_call` only, nothing sent) on unsigned type-2 transactions carrying real calldata. The chains and blocks were Base block 52,780,049 through `mainnet.base.org`, cross-checked against `base-rpc.publicnode.com`, which returned identical results wherever both endpoints answered (the remaining cases agreed at a later block), and X Layer block 73,580,619 through `rpc.xlayer.tech` and `xlayerrpc.okx.com`.

- **Base.** The fee for a `putChunk` of one 16,384-byte chunk with a 9-hash proof (16,868 bytes of calldata) was 6.9 to 9.5 × 10⁻⁸ ETH. The low end is NAND records, which compress somewhat; the high end is incompressible data. The fee for a `verifySegment` with a 75,280-byte witness was 3.9 × 10⁻⁷ ETH, and with a witness at the bound of §6.3 (84,208 bytes) 4.4 × 10⁻⁷ ETH. That is about 0.3 to 0.7% of the L2 execution cost of the same transactions at the L2 gas price of the moment, 0.006 gwei.
- **Upload on Base.** For the 456-chunk upload above, the L1 data fee was about 3 to 4 × 10⁻⁵ ETH, against about 0.01 ETH of L2 execution.
- **X Layer.** X Layer runs the same oracle with both L1 fee scalars set to 0, so it charges no separate L1 data fee; transactions pay only its L2 gas price, which was at its minimum of 0.02 gwei OKB.

These values move with Ethereum's base and blob fees, which were very low at the time (about 0.07 and 0.004 gwei). For example, the Base L1 fee would grow about 6.7-fold if the blob fee rose tenfold. The values also move with the chains' own fee parameters. Method, all cases and the totals are in `measurements/l1fee.md` of the reference repository.

## Appendix C: Opening an output (informative)

A claim does not list its outputs; the output chain inside its checkpoint leaves commits them. To open the outputs of the last beat, `T − 1`, from the last published leaf `L_{n−1}`, an opener gives `stateRoot_T`, `io_{T−1}` and the output bits `out_{T−1}`; anyone recomputes `outRoot_{T−1} = prefixRoot(0x04, out_{T−1}, nOut)`, `io_T = SHA-256(0x08 ‖ io_{T−1} ‖ outRoot_{T−1})` and `checkpointLeaf(T, stateRoot_T, io_T)`, and compares the result with `L_{n−1}`. For `nOut ≤ 256` the output root is a single word leaf, so the opening costs 3 SHA-256 calls; for larger `nOut` the opener can give `outRoot_{T−1}` instead of all the bits and open single output words with Merkle proofs against it. The same works at every checkpoint time `t_k`, with `io_{t_k − 1}` and the outputs of beat `t_k − 1`. Outputs of a beat between two checkpoints also need the output root of every later beat up to the next checkpoint, one hash each.

An opening shows what a claim says, not that it is right; an application should take an opened value only from an accepted claim (§7.1): its window has ended, no dispute about it is open, and none was won by a challenger. A claim whose claimant merely won a dispute is not yet accepted. Applications should therefore design their circuits so that the result they care about (a score, a generated token) is held on the outputs of the last beat, which the leaf at `T` commits, or of the beat before a checkpoint, and should fix how the result is read from the output bits. The cartridge demo (Reference Implementation) keeps its score on its output pins after it halts and opens it this way.

## Appendix D: Round counts and economics (informative)

**Round counts.** Count a round as one move of each party of a dispute, or as one move where only one party moves. Level 1 takes one round (the challenge). Level 2 takes one commit round and at most `⌈log2(t_i − t_{i−1})⌉ ≤ ⌈log2 K⌉` bisection rounds. Level 3 takes one commit round (with the inputs), one round to reveal tuple `h`, then at most `⌈log2 h⌉` bisection rounds, or, when the tuples at `h` are equal, one move to reveal tuple `h + 1`, and finally the call. A dispute that reaches a verifier therefore takes at most `⌈log2 K⌉ + ⌈log2 h⌉ + 5` rounds, or `⌈log2 K⌉ + 6` when the tuples at `h` are equal. Here `h` is at most the number of segments `m` for the segment list, and at most `⌈nOut/256⌉` or `⌈nLatch/256⌉` for the output or state list. The test vectors' disputes that reach a verifier take 6 to 10 rounds; in the reference game a round is one transaction of each party that moves in it. Disputes about one claim run independently, so several challengers do not lengthen any one dispute.

**One economic arrangement.** A claimant posts a claim with a bond; within a challenge window, anyone may challenge with an equal bond, each challenger in its own dispute; each move has a deadline, and a party that misses one loses; bonds are settled per dispute as §7.1 recommends (the claimant takes the bond of each challenger it beats, the first challenger to win takes the claimant's bond and rejects the claim, other open disputes are refunded, and an accepted claim's bond is returned). Such rules belong to an application TAP or to the game's own documentation (Open questions). The final call is usually the most expensive move, and the party whose tuple is checked pays for it. After a bisection that is always the claimant, also when the challenger is the one who cheated; only in the equal-at-`h` case is it the party with the longer list. An honest claimant can therefore spend the most gas of a dispute against a cheating challenger, once per cheating challenger. Applications should size bonds, or reimburse the winner's moves, so that the loser's bond covers at least the winner's on-chain cost including a final call at the budget's limit: in the reference game the final calls of the `large` example took up to about 4.5 million gas and a whole dispute that reached the verifier 1.4 to 5.8 million gas for all parties together, while the costliest final call measured took about 10.6 million (Appendix B). Lazy refinement keeps the off-chain cost of honest claims low: a claim publishes one leaf per `K` beats, beat leaves are computed and published only for the disputed checkpoint interval, and tuple lists and inputs only for the disputed beat. Security Considerations list the risks a game has to price in.

## Copyright

Copyright and related rights waived via [CC0](../LICENSE).
