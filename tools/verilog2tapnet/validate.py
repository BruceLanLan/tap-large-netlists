#!/usr/bin/env python3
"""Beat-by-beat validation of verilog2tapnet output against Icarus Verilog.

For a design and its tapnet file, generate input vectors (exhaustive for small combinational designs, random
otherwise), write a testbench that applies one vector per clock cycle and prints the outputs just before the
rising edge (and the contents of every memory that became RAM), and compare every beat with:
  - the TAP-02 reference evaluator assets/tap-02/reference.py (tapnet/1 files),
  - tapnet2.evaluate_full of the repository's reference implementation (tapnet/2 files), with the memory
    carried from beat to beat; for files without RAM ports also the canonical ROM expansion
    (tapnet2.expand_rom_to_tapnet1) under the TAP-02 evaluator.
Both sides start from the all-zero / declared-initial state.

    python3 validate.py            # convert and validate all designs, print the results tables
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verilog2tapnet as v2t  # noqa: E402
from verilog2tapnet import tapnet2  # noqa: E402

IVERILOG = shutil.which("iverilog") or "iverilog"
VVP = shutil.which("vvp") or "vvp"


def _load_tap02():
    path = os.environ.get("TAP02_REFERENCE") or os.path.join(v2t.REPO, "assets", "tap-02", "reference.py")
    spec = importlib.util.spec_from_file_location("tap02_reference", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tap02_reference"] = mod     # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


tap02 = _load_tap02()


class Mismatch(AssertionError):
    pass


def make_vectors(n_in: int, sequential: bool, beats: int, seed: int, exhaustive_limit: int = 12) -> List[List[int]]:
    rnd = random.Random(seed)
    if not sequential and n_in <= exhaustive_limit:
        return [[(v >> i) & 1 for i in range(n_in)] for v in range(1 << n_in)]
    vecs = [[rnd.getrandbits(1) for _ in range(n_in)] for _ in range(beats)]
    if not sequential:
        vecs = [[0] * n_in, [1] * n_in] + vecs
    return vecs


def observable_memories(info: dict) -> List[dict]:
    """Memories that became RAM banks and can be dumped from the testbench: a plain top-level name (not a
    flattened or generated one), and every bank still in the file (a bank the optimizer dropped is
    unobservable at the pins, and the dump would compare bits the file does not hold)."""
    out = []
    for m in info.get("memories", []):
        if m["kind"] != "ram" or not m["records"]:
            continue
        if not m["memid"].isidentifier():
            continue
        if not all(r.get("kept") and r["bank"] >= 0 for r in m["records"]):
            continue
        out.append(m)
    return out


def testbench(info: dict, n_beats: int, vec_file: str, mems: Sequence[dict]) -> str:
    top, clock = info["top"], info["clock"]
    n_in = len(info["inputs"])
    w = max(n_in, 1)
    lines = ["`timescale 1ns/1ps", "module tapnet_tb;",
             f"  reg [{w - 1}:0] vec [0:{n_beats - 1}];",
             f"  reg [{w - 1}:0] iv;",
             "  reg clk = 1'b0;",
             "  integer i;"]
    conns = []
    # input ports: map input bit k of the tapnet to iv[k]
    k = 0
    for name, width in info["input_ports"]:
        bits = [f"iv[{k + b}]" for b in range(width)]
        k += width
        conns.append(f".{name}({{{', '.join(reversed(bits))}}})")
    for name, width in info["output_ports"]:
        lines.append(f"  wire [{width - 1}:0] o_{name};")
        conns.append(f".{name}(o_{name})")
    if clock:
        conns.append(f".{clock}(clk)")
    lines.append(f"  {top} dut({', '.join(conns)});")
    outs = []
    for name, width in info["output_ports"]:
        outs += [f"o_{name}[{b}]" for b in range(width)]
    lines += ["  initial begin",
              f"    $readmemb(\"{vec_file}\", vec);",
              f"    for (i = 0; i < {n_beats}; i = i + 1) begin",
              "      iv = vec[i];",
              "      #1;",
              f"      $display(\"%b\", {{{', '.join(reversed(outs))}}});"]
    for mi, m in enumerate(mems):
        words = ", ".join(f"dut.{m['memid']}[{a}]" for a in reversed(range(m["size"])))
        lines.append(f"      $display(\"M{mi} %b\", {{{words}}});")
    lines += ["      clk = 1'b1; #1; clk = 1'b0; #1;",
              "    end",
              "    $finish;",
              "  end",
              "endmodule"]
    return "\n".join(lines) + "\n"


def simulate_verilog(sources: Sequence[str], info: dict, vecs: List[List[int]], defines: Sequence[str] = (),
                     mems: Sequence[dict] = ()) -> Tuple[List[List[int]], List[List[Optional[str]]]]:
    """Returns (outputs per beat, memory dumps per beat: per memory a bit string, MSB first, or None)."""
    n_in = len(info["inputs"])
    with tempfile.TemporaryDirectory(prefix="v2t-sim-") as d:
        vf = os.path.join(d, "vec.txt")
        with open(vf, "w") as f:
            for v in vecs:
                f.write("".join(str(b) for b in reversed(v)) if n_in else "0")
                f.write("\n")
        tb = os.path.join(d, "tb.v")
        with open(tb, "w") as f:
            f.write(testbench(info, len(vecs), vf, mems))
        exe = os.path.join(d, "sim")
        cmd = [IVERILOG, "-g2012", "-o", exe, "-s", "tapnet_tb"] + ["-D" + x for x in defines] + [tb] + \
            [os.path.abspath(s) for s in sources]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError("iverilog failed: " + p.stderr + p.stdout)
        p = subprocess.run([VVP, "-n", exe], capture_output=True, text=True, cwd=d)
        if p.returncode:
            raise RuntimeError("vvp failed: " + p.stderr)
    n_out = len(info["outputs"])
    out: List[List[int]] = []
    dumps: List[List[Optional[str]]] = []
    for ln in p.stdout.splitlines():
        ln = ln.strip()
        if ln.startswith("M") and " " in ln and dumps:
            mi, bits = ln[1:].split(" ", 1)
            dumps[-1][int(mi)] = bits
            continue
        if not ln or not set(ln) <= set("01xzXZ") or len(ln) != n_out:
            continue
        if set(ln) - set("01"):
            raise Mismatch(f"Verilog simulation printed undefined output bits {ln!r}")
        out.append([int(c) for c in reversed(ln)])
        dumps.append([None] * len(mems))
    if len(out) != len(vecs):
        raise RuntimeError(f"expected {len(vecs)} output rows from vvp, got {len(out)}:\n{p.stdout[:2000]}")
    return out, dumps


def simulate_tapnet(data: bytes, vecs: List[List[int]], with_mem: bool = False):
    """Outputs per beat; with_mem=True also the memory at the start of every beat (bank -> entries)."""
    rows, mems = [], []
    if data[6] == 1:
        n_in = int.from_bytes(data[8:12], "big")
        n_out = int.from_bytes(data[12:16], "big")
        c = tap02.load(data[16:], n_in, n_out)
        state = [0] * c.n_state
        for v in vecs:
            state, outs = tap02.beat(c, state, v)
            rows.append(outs)
            mems.append({})
    else:
        nl = tapnet2.decode(data)
        state = [0] * nl.n_latch
        mem: Dict[int, List[int]] = {}
        for v in vecs:
            mems.append(tapnet2.mem_copy(mem))
            _, outs, state, mem, _ = tapnet2.evaluate_full(nl, state, mem, v)
            rows.append(outs)
    return (rows, mems) if with_mem else rows


def _word(m: dict, mem: Dict[int, List[int]], addr: int) -> int:
    v = 0
    for r in m["records"]:
        ent = mem.get(r["bank"])
        e = ent[addr] if ent else 0
        for jj, col in enumerate(r["cols"]):
            v |= ((e >> jj) & 1) << col
    return v


def validate(sources: Sequence[str], res: "v2t.Result", beats: int = 300, seed: int = 1,
             exhaustive_limit: int = 12) -> dict:
    info = res.info
    sequential = info["latch"] > 0 or info["clock"] is not None
    vecs = make_vectors(len(info["inputs"]), sequential, beats, seed, exhaustive_limit)
    mems = observable_memories(info) if res.version == 2 else []
    ref, dumps = simulate_verilog(sources, info, vecs, mems=mems)
    got, got_mem = simulate_tapnet(res.data, vecs, with_mem=True)
    for t, (a, b) in enumerate(zip(ref, got)):
        if a != b:
            bad = [info["outputs"][j] for j in range(len(a)) if a[j] != b[j]]
            raise Mismatch(f"{info['top']}: beat {t} inputs {vecs[t]}: verilog {a} tapnet {b} (differs on {bad})")
    extra: dict = {}
    mem_checked = []
    for mi, m in enumerate(mems):
        width, size = m["width"], m["size"]
        cols = sorted(c for r in m["records"] for c in r["cols"])
        compared = 0
        for t in range(len(vecs)):
            bits = dumps[t][mi]
            if bits is None or len(bits) != width * size:
                compared = -1          # the Verilog memory is not the Yosys memory (narrowed): not compared
                break
            for addr in range(size):
                word = bits[len(bits) - (addr + 1) * width:len(bits) - addr * width][::-1]   # LSB first
                tv = _word(m, got_mem[t], addr)
                for col in cols:
                    vb = word[col]
                    if vb not in "01":
                        raise Mismatch(f"{info['top']}: beat {t}: Verilog memory {m['memid']}[{addr}] bit {col} "
                                       f"is {vb!r} (RAM must start at zero)")
                    if int(vb) != (tv >> col) & 1:
                        raise Mismatch(f"{info['top']}: beat {t}: memory {m['memid']}[{addr}] bit {col}: "
                                       f"verilog {vb} tapnet {(tv >> col) & 1}")
            compared += 1
        mem_checked.append({"memid": m["memid"], "beats": compared, "bits": len(cols) * size})
    if mem_checked:
        extra["memory_checked"] = mem_checked
    if res.version == 2 and not res.data[7] & tapnet2.FLAG_MEM:
        # the canonical ROM expansion (a tapnet/1 file) must behave the same under the TAP-02 evaluator
        exp = tapnet2.expand_rom_to_tapnet1(res.data)
        if simulate_tapnet(exp, vecs) != ref:
            raise Mismatch(f"{info['top']}: ROM expansion differs from the Verilog simulation")
        extra["expansion_checked"] = True
    return {"beats": len(vecs), "mode": "exhaustive" if (not sequential and len(info["inputs"]) <= exhaustive_limit)
            else ("random sequence" if sequential else "random"), **extra}


# ---------------------------------------------------------------- the design suite

D = lambda n: os.path.join(HERE, "designs", n)  # noqa: E731
V2ROM = {"version": 2, "rom": True}
V2MEM = {"version": 2, "rom": True, "ram": True}

# name, sources, top, options
DESIGNS: List[Tuple[str, List[str], str, dict]] = [
    ("xor2", [D("xor2.v")], "xor2", {}),
    ("xor_renamed", [D("xor_renamed.v")], "xor_renamed", {}),
    ("xor_sop", [D("xor_sop.v")], "xor_sop", {}),
    ("full_adder", [D("full_adder.v")], "full_adder", {}),
    ("full_adder_gates", [D("full_adder_gates.v")], "full_adder_gates", {}),
    ("adder8", [D("adder8.v")], "adder8", {}),
    ("adder8_swapped", [D("adder8_swapped.v")], "adder8_swapped", {"ports": ["a", "b", "cin", "s", "cout"]}),
    ("popcount8", [D("popcount8.v")], "popcount8", {}),
    ("counter4", [D("counter4.v")], "counter4", {}),
    ("lfsr8", [D("lfsr8.v")], "lfsr8", {}),
    ("traffic", [D("traffic.v")], "traffic", {}),
    ("alu8", [D("alu8.v")], "alu8", {}),
    ("acc_cpu", [D("acc_cpu.v")], "acc_cpu", {}),
    ("rom_wave", [D("rom_wave.v")], "rom_wave", {}),
    ("acc_cpu.v2rom", [D("acc_cpu.v")], "acc_cpu", V2ROM),
    ("rom_wave.v2rom", [D("rom_wave.v")], "rom_wave", V2ROM),
    ("counter4.v2", [D("counter4.v")], "counter4", {"version": 2}),
    # ROM read ports and RAM ports
    ("rom_dual.v2rom", [D("rom_dual.v")], "rom_dual", V2ROM),
    ("regfile16x8.v2ram", [D("regfile16x8.v")], "regfile16x8", V2MEM),
    ("ram_bytes.v2ram", [D("ram_bytes.v")], "ram_bytes", V2MEM),
    ("stack_cpu.v2ram", [D("stack_cpu.v")], "stack_cpu", V2MEM),
    ("acc_cpu.v2ram", [D("acc_cpu.v")], "acc_cpu", V2MEM),
    ("acc_ram.v2ram", [D("acc_ram.v")], "acc_ram", V2MEM),
    ("ram_init.v2ram", [D("ram_init.v")], "ram_init", V2MEM),
]

# RAM designs and the same source with LATCH-built memory (ROM records kept, written memories as flip-flops)
RAM_COMPARE = [("regfile16x8.v2ram", "regfile16x8.v2latch"), ("ram_bytes.v2ram", "ram_bytes.v2latch"),
               ("stack_cpu.v2ram", "stack_cpu.v2latch"), ("acc_cpu.v2ram", "acc_cpu.v2rom"),
               ("acc_ram.v2ram", "acc_ram.v2latch")]
LATCH_BUILDS = {
    "regfile16x8.v2latch": ([D("regfile16x8.v")], "regfile16x8", V2ROM),
    "ram_bytes.v2latch": ([D("ram_bytes.v")], "ram_bytes", V2ROM),
    "stack_cpu.v2latch": ([D("stack_cpu.v")], "stack_cpu", V2ROM),
    "acc_ram.v2latch": ([D("acc_ram.v")], "acc_ram", V2ROM),
}
# ROM read port vs a second ROM record (rom_dual with the second read built from logic is not comparable
# directly; the reduction is reported against the LATCH/logic build without --rom)
ROM_COMPARE = [("rom_dual.v2rom", "rom_dual.v2logic")]
LATCH_BUILDS["rom_dual.v2logic"] = ([D("rom_dual.v")], "rom_dual", {"version": 2})

# Pairs whose record multisets must be equal (same structure, different source text / names / port order)
MUST_MATCH = [("xor2", "xor_renamed"), ("adder8", "adder8_swapped")]


def records_multiset(data: bytes) -> List[bytes]:
    """The 7-byte NAND records of a NAND-only tapnet/1 file, sorted."""
    body = data[16:]
    return sorted(body[k:k + 7] for k in range(0, len(body), 7))


def segments(data: bytes) -> int:
    """Number of canonical segments per beat under the default budget (tapnet/2 section 5)."""
    if data[6] != 2:
        return -1
    return len(tapnet2.segment_boundaries(tapnet2.decode(data))) - 1


def run_suite(out_dir: Optional[str] = None, beats: int = 1000, best: bool = True) -> dict:
    results = {}
    for name, srcs, top, opts in DESIGNS:
        res = v2t.convert(srcs, top, **opts)
        val = validate(srcs, res, beats=beats)
        results[name] = {"info": res.info, "validation": val, "segments": segments(res.data)}
        if best:
            rb = v2t.convert(srcs, top, best=True, **opts)
            validate(srcs, rb, beats=beats, seed=2)
            results[name]["best"] = {"nand": rb.info["nand"], "latch": rb.info["latch"], "bytes": rb.info["bytes"],
                                     "abc_script": rb.info["abc_script"]}
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            ext = ".tapnet2" if res.version == 2 else ".tapnet"
            with open(os.path.join(out_dir, name + ext), "wb") as f:
                f.write(res.data)
    latch = {}
    for name, (srcs, top, opts) in LATCH_BUILDS.items():
        res = v2t.convert(srcs, top, **opts)
        val = validate(srcs, res, beats=beats)
        latch[name] = {"info": res.info, "validation": val, "segments": segments(res.data)}
    pairs = {}
    for a, b in MUST_MATCH:
        pairs[f"{a} / {b}"] = records_multiset(v2t.convert(*next(d[1:3] for d in DESIGNS if d[0] == a)).data) == \
            records_multiset(v2t.convert(*next(d[1:3] for d in DESIGNS if d[0] == b),
                                         **next(d[3] for d in DESIGNS if d[0] == b)).data)
    return {"designs": results, "latch_builds": latch, "pairs": pairs}


def _get(s: dict, name: str) -> dict:
    return s["designs"].get(name) or s["latch_builds"][name]


def table(s: dict) -> str:
    rows = ["| design | format | nIn | nOut | NAND | LATCH | ROM bits | RAM bits | ports R/W/ROMP | bytes | segments | "
            "NAND --best | tail move/latch/port · copy/latch/port · const/buf | validation |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in s["designs"].items():
        i, v = r["info"], r["validation"]
        t = i["tail"]
        tail = (f"{t['move']}/{t['latch_move']}/{t['port_move']} · {t['copy']}/{t['latch_copy']}/{t['port_copy']}"
                f" · {t['const']}/{t['buf']}")
        ports = (f"{i.get('ramReadPorts', 0)}/{i.get('ramWritePorts', 0)}/{i.get('romPorts', 0)}"
                 if "ramBanks" in i else "–")
        val = f"{v['beats']} beats {v['mode']}"
        if v.get("memory_checked"):
            val += " + memory " + ", ".join(f"{m['bits']} bits × {m['beats']}" for m in v["memory_checked"])
        if v.get("expansion_checked"):
            val += " + expansion"
        rows.append(f"| {name} | {i['format']} | {i['nIn']} | {i['nOut']} | {i['nand']} | {i['latch']} | "
                    f"{i.get('romBits', 0) or '–'} | {i.get('ramBits', 0) or '–'} | {ports} | {i['bytes']} | "
                    f"{r['segments'] if r['segments'] >= 0 else '–'} | "
                    f"{r['best']['nand'] if 'best' in r else '–'} | {tail} | {val} OK |")
    return "\n".join(rows)


def compare_table(s: dict) -> str:
    rows = ["| design | RAM / ROM-port build: bytes, NAND, LATCH, segments | LATCH-built: bytes, NAND, LATCH, segments | bytes saved |",
            "|---|---|---|---|"]
    for a, b in RAM_COMPARE + ROM_COMPARE:
        ia, ib = _get(s, a), _get(s, b)
        x, y = ia["info"], ib["info"]
        rows.append(f"| {a.split('.')[0]} | {x['bytes']}, {x['nand']}, {x['latch']}, {ia['segments']} | "
                    f"{y['bytes']}, {y['nand']}, {y['latch']}, {ib['segments']} | "
                    f"{100 * (1 - x['bytes'] / y['bytes']):.0f}% ({y['bytes'] / x['bytes']:.1f}×) |")
    return "\n".join(rows)


def main() -> int:
    out = os.path.join(HERE, "out")
    s = run_suite(out)
    print(table(s))
    print()
    print(compare_table(s))
    print()
    for k, eq in s["pairs"].items():
        print(f"records {k}: {'equal' if eq else 'different'}")
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(s, f, indent=1, default=str)
    return 0 if all(s["pairs"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
