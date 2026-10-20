#!/usr/bin/env python3
"""verilog2tapnet: synthesize a Verilog design with Yosys into a tapnet/1 or tapnet/2 netlist file.

Targets
  tapnet/1  header TAPNET || 0x01 || 0x00 || u32be nIn || u32be nOut, then TAP-02 records NAND 0x00 (two
            backward u24 refs) and LATCH 0x01 (one u24 d ref, may be forward).
  tapnet/2  TAP-draft-large-netlists section 1: version 0x02, flags byte (MEM), plus ROM 0x10, RAM read
            port 0x11, RAM write port 0x12 and ROM read port 0x13.

Pipeline
  1. Yosys: read, flatten, coarse synthesis, memories (kept as $mem_v2 when they become ROM or RAM records,
     otherwise mapped to flip-flops and logic), techmap, setundef -zero, dfflegalize to $_DFF_P_ with zero
     init, abc -g NAND, write_json. With --rom or --ram a first, short Yosys run lists the memories and the
     tool decides which ones it can keep (the others are mapped by name in the second run).
  2. Python: read the JSON (only $_NAND_, $_NOT_, $_DFF_P_ and kept $mem_v2 cells are accepted), build a
     NAND/LATCH/port graph, simplify it (constant folding, double negation, structural hashing, a NOT-sharing
     rewrite, duplicate read ports, dead-element removal), lay it out (inputs, LATCHes, NANDs and ports in
     topological order, output tail), encode, and check with the reference implementation tapnet2.py.

Semantics: one beat = one rising edge of the single clock. Outputs of a beat are the values just before the
edge, with that beat's inputs applied; LATCH state = flip-flop contents, all zero at power-on. A flip-flop with
a constant nonzero initial value is stored inverted (dfflegalize inserts the inverters). A RAM bank holds the
contents of a Verilog memory with synchronous write and asynchronous read: a RAM read port reads the memory at
the start of the beat, the write ports write it for the next beat, as the memory does at the clock edge.
Memory starts all zero (tapnet/2 section 1.3), so a memory that becomes RAM must start at zero.

MIT licence. Python 3.9+, needs yosys on PATH (or $YOSYS). Self-contained within the reference repository:
it imports assets/tap-draft-large-netlists/tapnet2.py (validity, evaluation, hashes, also for tapnet/1 files).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
ASSETS = os.path.join(REPO, "assets", "tap-draft-large-netlists")
if ASSETS not in sys.path:
    sys.path.append(ASSETS)

import tapnet2  # noqa: E402  (the repository's reference implementation)

YOSYS = os.environ.get("YOSYS") or shutil.which("yosys") or "yosys"

MAX_SIGNALS = 1 << 24
MAX_PINS = 1 << 16
MAX_W = 256                    # ROM and port word width limit (tapnet/2 section 1.2 items 8, 9)
MAX_A = 20

# Alternative ABC scripts tried by --best (the default `abc -g NAND` script is always tried first).
ABC_SCRIPTS = {
    "dch": "strash; dc2; dch -f; map",
    "deepsyn": "strash; &get -n; &deepsyn -T 3; &put; dch -f; map",
}


class ConversionError(Exception):
    """The design cannot be expressed in the target format (or Yosys failed)."""


def check_file(data: bytes) -> dict:
    """Validity and metrics of a tapnet/1 or tapnet/2 file with the reference implementation.
    For tapnet/1 this is tapnet2.decode with versions=(1,): NAND and LATCH records only, flags byte zero,
    the reference rules of TAP-02 (backward NAND refs, LATCH d < S, S <= 2^24, nOut <= S - 2 - nIn)."""
    return tapnet2.decode(data, (data[6],) if len(data) > 6 and data[6] in (1, 2) else (2,)).metrics()


# ---------------------------------------------------------------- Yosys

# Fine flip-flop cells that dfflegalize can turn into $_DFF_P_ + logic without changing behaviour:
# positive-edge clock; optional clock enable; optional synchronous reset. Everything else is rejected.
ALLOWED_FF = re.compile(r"^\$_(DFF_P|DFFE_P[PN]|SDFF_P[PN][01]|SDFFC?E_P[PN][01][PN])_$")
FF_LIKE = re.compile(r"^\$_(DFF|DFFE|SDFF|SDFFE|SDFFCE|DFFSR|DFFSRE|ALDFF|ALDFFE|DLATCH|DLATCHSR|SR)_")
FF_REASON = [
    (re.compile(r"^\$_DFF_N"), "negative-edge flip-flop (only posedge of a single clock is supported)"),
    (re.compile(r"^\$_DFFE_N|^\$_SDFFC?E_N|^\$_SDFF_N"), "negative-edge flip-flop"),
    (re.compile(r"^\$_DFF_P[PN][01]_$|^\$_DFFE_P[PN][01][PN]_$"), "flip-flop with asynchronous reset"),
    (re.compile(r"^\$_DFFSRE?_"), "flip-flop with asynchronous set/reset"),
    (re.compile(r"^\$_ALDFFE?_"), "flip-flop with asynchronous load"),
    (re.compile(r"^\$_DLATCH"), "level-sensitive (transparent) latch"),
    (re.compile(r"^\$_SR_"), "SR latch"),
]


def _q(p: str) -> str:
    return '"' + p.replace('"', '\\"') + '"'


def yosys_front(sources: Sequence[str], top: str, defines: Sequence[str]) -> List[str]:
    defs = " ".join("-D" + d for d in defines)
    incs = " ".join("-I" + _q(d) for d in sorted({os.path.dirname(os.path.abspath(s)) for s in sources}))
    return [f"read_verilog -sv {defs} {incs} " + " ".join(_q(os.path.abspath(s)) for s in sources),
            f"hierarchy -check -top {top}",
            "proc", "flatten", "opt_expr", "opt_clean", "check",
            "opt -nodffe -nosdff", "fsm", "opt", "wreduce", "peepopt", "opt_clean",
            "alumacc", "share", "opt"]


def yosys_script(sources: Sequence[str], top: str, keep_mems: bool, map_cells: Sequence[str], pre_json: str,
                 out_json: str, defines: Sequence[str] = (), abc_script: Optional[str] = None) -> str:
    lines = yosys_front(sources, top, defines)
    if keep_mems:
        # Memories stay $mem_v2; -nordff keeps a registered read as a flip-flop after an asynchronous read
        # port. The memories the tool cannot keep are mapped to flip-flops and logic by cell name.
        lines += ["memory -nomap -nordff", "opt_clean"]
        lines += [f"memory_map {top}/{c}" for c in map_cells]
        lines += ["opt_clean"]
    else:
        lines += ["memory -nomap", "opt_clean", "memory_map", "opt_clean"]
    abc = "abc -g NAND" + (f" -script {_q(abc_script)}" if abc_script else "")
    lines += ["opt -full", "techmap", "opt -fast",
              "setundef -zero -undriven -init",
              f"write_json {_q(pre_json)}",
              "dfflegalize -cell $_DFF_P_ 0",
              "opt_clean",
              abc,
              "setundef -zero -undriven",
              "opt_clean",
              "stat",
              f"write_json {_q(out_json)}"]
    return "\n".join(lines) + "\n"


def _run(script: str, d: str) -> Tuple[int, str, str]:
    ys = os.path.join(d, "synth.ys")
    with open(ys, "w") as f:
        f.write(script)
    logf = os.path.join(d, "yosys.log")
    p = subprocess.run([YOSYS, "-q", "-l", logf, "-s", ys], capture_output=True, text=True)
    log = ""
    if os.path.exists(logf):
        with open(logf) as f:
            log = f.read()
    return p.returncode, (p.stderr or p.stdout or "").strip(), log


def list_memories(sources: Sequence[str], top: str, defines: Sequence[str] = ()) -> Dict[str, dict]:
    """First Yosys run: the $mem_v2 cells of the design after `memory -nomap -nordff` (cell name -> cell)."""
    with tempfile.TemporaryDirectory(prefix="v2t-mem-") as d:
        js = os.path.join(d, "mem.json")
        script = "\n".join(yosys_front(sources, top, defines) +
                           ["memory -nomap -nordff", "opt_clean", f"write_json {_q(js)}"]) + "\n"
        rc, msg, log = _run(script, d)
        if rc != 0 or not os.path.exists(js):
            raise ConversionError("yosys failed: " + (msg or log[-2000:])[-3000:])
        with open(js) as f:
            j = json.load(f)
    m = j.get("modules", {}).get(top)
    if m is None:
        raise ConversionError(f"top module {top!r} not in Yosys output")
    return {n: c for n, c in m.get("cells", {}).items() if c["type"] == "$mem_v2"}


def run_yosys(sources: Sequence[str], top: str, keep_mems: bool, map_cells: Sequence[str] = (),
              keep_dir: Optional[str] = None, defines: Sequence[str] = (),
              abc_script: Optional[str] = None) -> Tuple[dict, dict, str]:
    """Returns (pre-legalization JSON, final JSON, yosys log)."""
    for s in sources:
        if not os.path.exists(s):
            raise ConversionError(f"no such source file: {s}")
    with tempfile.TemporaryDirectory(prefix="v2t-") as tmp:
        d = keep_dir or tmp
        os.makedirs(d, exist_ok=True)
        pre, out = os.path.join(d, "pre.json"), os.path.join(d, "out.json")
        if os.path.exists(out):
            os.unlink(out)
        rc, msg, log = _run(yosys_script(sources, top, keep_mems, map_cells, pre, out, defines, abc_script), d)
        if rc != 0 or not os.path.exists(out):
            if os.path.exists(pre):
                with open(pre) as f:
                    check_pre_ffs(json.load(f), top)   # a clearer message than dfflegalize's, if it applies
            msg = msg or log[-2000:]
            if "dfflegalize" in log[-4000:] or "dfflegalize" in msg:
                msg = "unsupported flip-flop (async reset/set, latch or negative edge?): " + msg
            raise ConversionError("yosys failed: " + msg[-3000:])
        with open(pre) as f:
            pre_j = json.load(f)
        with open(out) as f:
            out_j = json.load(f)
    return pre_j, out_j, log


def check_pre_ffs(pre: dict, top: str) -> None:
    m = pre["modules"].get(top)
    if m is None:
        return
    bad = {}
    for name, c in m.get("cells", {}).items():
        t = c["type"]
        if FF_LIKE.match(t) and not ALLOWED_FF.match(t):
            reason = next((r for p, r in FF_REASON if p.match(t)), "unsupported storage element")
            bad.setdefault((t, reason), []).append(name)
        if t.startswith("$_TBUF") or t in ("$tribuf", "$_TBUF_"):
            bad.setdefault((t, "tri-state buffer"), []).append(name)
    if bad:
        msg = "; ".join(f"{len(v)} x {t}: {r}" for (t, r), v in sorted(bad.items()))
        raise ConversionError("unsupported cells: " + msg)


# ---------------------------------------------------------------- memories: which ones become records

def _ival(s) -> int:
    if isinstance(s, int):
        return s
    s = s.replace("x", "0").replace("z", "0")
    return int(s, 2) if s else 0


def _bits(s, n: int) -> str:
    """A parameter as an n-character bit string, index 0 = least significant (rightmost in Yosys JSON)."""
    s = s if isinstance(s, str) else format(s, "b")
    return s.rjust(n, "0")[::-1][:n]


def memory_plan(cell: dict, rom: bool, ram: bool, rom_min_abits: int, ram_min_abits: int) -> Tuple[Optional[str], str]:
    """('rom' | 'ram' | None, reason). None: map to flip-flops and logic (the reason says why)."""
    p, con = cell["parameters"], cell["connections"]
    a, w, size = _ival(p["ABITS"]), _ival(p["WIDTH"]), _ival(p["SIZE"])
    nrd, nwr = _ival(p["RD_PORTS"]), _ival(p["WR_PORTS"])
    kind = "ram" if nwr else "rom"
    if kind == "rom" and not rom:
        return None, "read-only memory (no --rom)"
    if kind == "ram" and not ram:
        return None, "written memory (no --ram)"
    if nrd == 0:
        return None, "no read port"
    if not 1 <= a <= MAX_A:
        return None, f"address width {a} outside 1..{MAX_A}"
    if a < (rom_min_abits if kind == "rom" else ram_min_abits):
        return None, f"address width {a} below --{kind}-min-abits"
    if size > 1 << a:
        return None, "more words than 2^ABITS"
    if _ival(p.get("OFFSET", "0")) != 0:
        return None, "nonzero address offset"
    if _ival(p.get("RD_WIDE_CONTINUATION", "0")) or _ival(p.get("WR_WIDE_CONTINUATION", "0")):
        return None, "wide port"
    if _ival(p.get("RD_CLK_ENABLE", "0")):
        return None, "clocked read port"
    for k in ("RD_ARST", "RD_SRST"):
        if any(str(b) not in ("0", "x") for b in con.get(k, [])):
            return None, "read port reset"
    if any(str(b) != "1" for b in con.get("RD_EN", [])):
        return None, "read enable"
    if kind == "ram":
        if _bits(p["WR_CLK_ENABLE"], nwr) != "1" * nwr:
            return None, "unclocked write port"
        if _bits(p["WR_CLK_POLARITY"], nwr) != "1" * nwr:
            return None, "negative-edge write port"
        if len({str(b) for b in con["WR_CLK"]}) != 1:
            return None, "write ports on different clocks"
        init = p.get("INIT", "")
        init = init if isinstance(init, str) else format(init, "b")
        if "1" in init:
            return None, "nonzero initial contents (tapnet/2 memory starts all zero)"
        pm = _bits(p.get("WR_PRIORITY_MASK", "0"), nwr * nwr)
        for j in range(nwr):
            for i in range(nwr):
                if pm[j * nwr + i] == "1" and j < i:
                    return None, "write port priority not in port order"
    return kind, ""


# ---------------------------------------------------------------- graph

@dataclass
class Mem:
    kind: str                       # "rom" | "ram"
    a: int
    w: int
    entries: List[int] = field(default_factory=list)          # ROM: 2^a words
    wports: List["WPort"] = field(default_factory=list)       # RAM: in file order (the last write wins)
    memid: str = ""
    cols: List[int] = field(default_factory=list)             # bit j of this record = bit cols[j] of the word
    bank: int = -1                  # RAM: bank byte
    size: int = 0                   # words of the Verilog memory
    width: int = 0                  # width of the Verilog memory (as Yosys reports it)


@dataclass
class WPort:
    we: int
    addr: List[int]
    data: List[int]


@dataclass
class RPort:
    mem: int
    addr: List[int]                 # node ids, addr[0] = least significant bit
    outs: List[int]                 # node ids of the w output bits


@dataclass
class Graph:
    n_in: int
    kind: Dict[int, tuple] = field(default_factory=dict)   # id -> ("N", a, b) | ("L", d) | ("R", port, j)
    mems: Dict[int, Mem] = field(default_factory=dict)
    rports: Dict[int, RPort] = field(default_factory=dict)
    outputs: List[int] = field(default_factory=list)
    next_id: int = 0

    def new(self, k: tuple) -> int:
        i = self.next_id
        self.next_id += 1
        self.kind[i] = k
        return i

    def is_not(self, x: int) -> Optional[int]:
        k = self.kind.get(x)
        if k and k[0] == "N" and k[1] == k[2]:
            return k[1]
        return None

    def counts(self) -> Tuple[int, int, int, int]:
        nand = sum(1 for k in self.kind.values() if k[0] == "N")
        latch = sum(1 for k in self.kind.values() if k[0] == "L")
        return nand, latch, len(self.rports), sum(len(m.wports) for m in self.mems.values())

    def port_refs(self) -> List[Tuple[tuple, List[int]]]:
        """(unit, references) of every port: ("P", port) read ports, ("W", mem, k) write ports."""
        out = [(("P", p), list(rp.addr)) for p, rp in self.rports.items()]
        for m, mem in self.mems.items():
            for k, wp in enumerate(mem.wports):
                out.append((("W", m, k), [wp.we] + list(wp.addr) + list(wp.data)))
        return out

    def unit_of(self, x: int):
        k = self.kind.get(x)
        if k is None or k[0] == "L":
            return None
        return ("P", k[1]) if k[0] == "R" else x

    def units(self) -> List:
        """Topological order of combinational units: NAND ids, ("P", port) read ports and ("W", mem, k) write
        ports (write ports of one memory stay in their order). LATCHes, inputs and constants are sources.
        Raises ConversionError on a combinational loop."""
        deps: Dict[object, List] = {}
        for x, k in self.kind.items():
            if k[0] == "N":
                deps[x] = [u for u in (self.unit_of(k[1]), self.unit_of(k[2])) if u is not None]
        for u, refs in self.port_refs():
            deps[u] = [v for v in (self.unit_of(r) for r in refs) if v is not None]
            if u[0] == "W" and u[2] > 0:
                deps[u].append(("W", u[1], u[2] - 1))

        def key(u):
            if isinstance(u, tuple):
                return (u[1], 1 if u[0] == "P" else 2, u[2] if u[0] == "W" else 0)
            return (u, 0, 0)
        state: Dict[object, int] = {}
        order: List = []
        for root in sorted(deps, key=key):
            if root in state:
                continue
            stack = [(root, iter(sorted(set(deps[root]), key=key)))]
            state[root] = 1
            while stack:
                u, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    stack.pop()
                    state[u] = 2
                    order.append(u)
                elif state.get(nxt) == 1:
                    raise ConversionError("combinational loop in the synthesized netlist")
                elif nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(sorted(set(deps[nxt]), key=key))))
        return order


def _memid_name(memid: str) -> str:
    return memid[1:] if memid.startswith("\\") else memid


def graph_from_yosys(j: dict, top: str, ports: Optional[Sequence[str]], clock: Optional[str]) -> Tuple[Graph, dict]:
    mods = j.get("modules", {})
    if top not in mods:
        raise ConversionError(f"top module {top!r} not in Yosys output (have {sorted(mods)})")
    m = mods[top]
    cells = m.get("cells", {})
    port_items = list(m["ports"].items())
    # clock: the C net of every flip-flop and the clock of every RAM write port
    clk_bits = set()
    for name, c in cells.items():
        if c["type"] == "$_DFF_P_":
            clk_bits.add(str(c["connections"]["C"][0]))
        elif c["type"] == "$mem_v2":
            for b in c["connections"].get("WR_CLK", []):
                clk_bits.add(str(b))
    clock_port = None
    if clk_bits:
        if len(clk_bits) > 1:
            raise ConversionError(f"more than one clock net ({len(clk_bits)}); only a single clock is supported")
        cb = clk_bits.pop()
        cands = [n for n, p in port_items if p["direction"] == "input" and len(p["bits"]) == 1
                 and str(p["bits"][0]) == cb]
        if clock is not None:
            if clock not in cands:
                raise ConversionError(f"flip-flops are not clocked directly by input port {clock!r} "
                                      "(gated or derived clocks are not supported)")
            clock_port = clock
        elif not cands:
            raise ConversionError("flip-flops are clocked by an internal net, not a 1-bit top-level input "
                                  "(gated, derived or inverted clocks are not supported)")
        else:
            clock_port = cands[0]
    elif clock is not None:
        clock_port = clock if any(n == clock for n, _ in port_items) else None
    ins = [(n, p) for n, p in port_items if p["direction"] == "input" and n != clock_port]
    outs = [(n, p) for n, p in port_items if p["direction"] == "output"]
    if any(p["direction"] == "inout" for _, p in port_items):
        raise ConversionError("inout ports (tri-state) are not supported")
    if ports:
        order = {n: i for i, n in enumerate(ports)}
        missing = [n for n, _ in ins + outs if n not in order]
        if missing:
            raise ConversionError(f"--ports does not list: {', '.join(missing)}")
        unknown = [n for n in ports if n not in dict(port_items)]
        if unknown:
            raise ConversionError(f"--ports names unknown ports: {', '.join(unknown)}")
        ins.sort(key=lambda t: order[t[0]])
        outs.sort(key=lambda t: order[t[0]])

    in_map = [(n, i) for n, p in ins for i in range(len(p["bits"]))]
    out_map = [(n, i) for n, p in outs for i in range(len(p["bits"]))]
    n_in = len(in_map)
    if n_in > MAX_PINS:
        raise ConversionError(f"{n_in} input bits > 65536")
    if not 1 <= len(out_map) <= MAX_PINS:
        raise ConversionError(f"{len(out_map)} output bits; need 1..65536")
    g = Graph(n_in, next_id=2 + n_in)
    drv: Dict[str, int] = {}
    for k, (n, i) in enumerate(in_map):
        b = dict(ins)[n]["bits"][i]
        if isinstance(b, str):
            continue
        if str(b) in drv:
            raise ConversionError(f"input bit {n}[{i}] aliases another input bit")
        drv[str(b)] = 2 + k
    cbit = str(dict(port_items)[clock_port]["bits"][0]) if clock_port else None

    warnings: List[str] = []
    pending: List[Tuple[object, str, List]] = []
    stats = {"yosys_nand": 0, "yosys_not": 0, "yosys_dff": 0, "yosys_rom": 0, "yosys_ram": 0}
    memories: List[dict] = []
    n_bank = 0
    for name, c in sorted(cells.items()):
        t, con = c["type"], c["connections"]
        if t == "$scopeinfo":
            continue
        if t == "$_NAND_":
            stats["yosys_nand"] += 1
            y = g.new(("N", None, None))
            pending.append((y, "N", [con["A"][0], con["B"][0]]))
            drv[str(con["Y"][0])] = y
        elif t == "$_NOT_":
            stats["yosys_not"] += 1
            y = g.new(("N", None, None))
            pending.append((y, "N", [con["A"][0], con["A"][0]]))
            drv[str(con["Y"][0])] = y
        elif t == "$_BUF_":
            y = g.new(("B", None))
            pending.append((y, "B", [con["A"][0]]))
            drv[str(con["Y"][0])] = y
        elif t == "$_DFF_P_":
            stats["yosys_dff"] += 1
            y = g.new(("L", None))
            pending.append((y, "L", [con["D"][0]]))
            drv[str(con["Q"][0])] = y
        elif t == "$mem_v2":
            prm = c["parameters"]
            a, w, size = _ival(prm["ABITS"]), _ival(prm["WIDTH"]), _ival(prm["SIZE"])
            nrd, nwr = _ival(prm["RD_PORTS"]), _ival(prm["WR_PORTS"])
            memid = prm.get("MEMID", name)
            kind = "ram" if nwr else "rom"
            stats["yosys_" + kind] += 1
            info_m = {"memid": _memid_name(memid), "kind": kind, "abits": a, "width": w, "size": size,
                      "read_ports": nrd, "write_ports": nwr, "records": []}
            memories.append(info_m)
            rd_addr = [con["RD_ADDR"][r * a:(r + 1) * a] for r in range(nrd)]
            rd_data = [con["RD_DATA"][r * w:(r + 1) * w] for r in range(nrd)]
            groups: List[Tuple[tuple, List[int]]] = []
            if kind == "rom":
                init = prm.get("INIT", "")
                init = init if isinstance(init, str) else format(init, "b")
                init = init.replace("x", "0").replace("z", "0").rjust(size * w, "0")
                words = [int(init[len(init) - (k + 1) * w:len(init) - k * w] or "0", 2) for k in range(size)]
                words += [0] * ((1 << a) - size)
                groups = [((), list(range(w)))]
                chunk = MAX_W
            else:
                # one bank per group of columns written under the same enables (byte enables split banks)
                en = con["WR_EN"]
                bykey: Dict[tuple, List[int]] = {}
                for col in range(w):
                    key = tuple(str(en[p * w + col]) for p in range(nwr))
                    bykey.setdefault(key, []).append(col)
                groups = list(bykey.items())
                chunk = min(MAX_W, (1 << 24) >> a)       # 2^a * stride <= 2^24 (section 1.2 item 9)
            for key, cols_all in groups:
                for lo in range(0, len(cols_all), chunk):
                    cols = cols_all[lo:lo + chunk]
                    mid = len(g.mems)
                    mem = Mem(kind, a, len(cols), memid=_memid_name(memid), cols=cols, size=size, width=w)
                    if kind == "rom":
                        mem.entries = [sum(((e >> col) & 1) << jj for jj, col in enumerate(cols)) for e in words]
                    else:
                        if n_bank > 255:
                            raise ConversionError("more than 256 RAM banks")
                        mem.bank = n_bank
                        n_bank += 1
                    g.mems[mid] = mem
                    info_m["records"].append({"mem": mid, "cols": cols, "bank": mem.bank})
                    for r in range(nrd):
                        pid = len(g.rports)
                        rp = RPort(mid, [], [])
                        g.rports[pid] = rp
                        for jj, col in enumerate(cols):
                            y = g.new(("R", pid, jj))
                            rp.outs.append(y)
                            drv[str(rd_data[r][col])] = y
                        pending.append((pid, "P", list(rd_addr[r])))
                    for p in range(nwr if kind == "ram" else 0):
                        we = key[p]
                        if we in ("0", "x", "z"):
                            continue
                        wa = con["WR_ADDR"][p * a:(p + 1) * a]
                        wd = [con["WR_DATA"][p * w + col] for col in cols]
                        pending.append((mid, "W", [we] + list(wa) + list(wd)))
        else:
            raise ConversionError(f"unsupported cell {t} ({name}) after synthesis")

    def ref(b) -> int:
        s = str(b)
        if s == "0":
            return 0
        if s == "1":
            return 1
        if s in ("x", "z"):
            warnings.append(f"undefined bit '{s}' read as 0")
            return 0
        if cbit is not None and s == cbit:
            raise ConversionError(f"clock {clock_port!r} is used as data (only as the flip-flop clock)")
        if s not in drv:
            warnings.append(f"undriven net {s} read as 0")
            return 0
        return drv[s]

    for y, k, bits in pending:
        if k == "N":
            g.kind[y] = ("N", ref(bits[0]), ref(bits[1]))
        elif k == "B":
            g.kind[y] = ("B", ref(bits[0]))
        elif k == "L":
            g.kind[y] = ("L", ref(bits[0]))
        elif k == "P":
            g.rports[y].addr = [ref(b) for b in bits]
        else:
            mem = g.mems[y]
            r = [ref(b) for b in bits]
            mem.wports.append(WPort(r[0], r[1:1 + mem.a], r[1 + mem.a:]))
    out_bits = [dict(outs)[n]["bits"][i] for n, i in out_map]
    g.outputs = [ref(b) for b in out_bits]
    # resolve buffers
    bufs = {y: k[1] for y, k in g.kind.items() if k[0] == "B"}
    if bufs:
        def rb(x):
            seen = 0
            while x in bufs:
                x = bufs[x]
                seen += 1
                if seen > len(bufs):
                    raise ConversionError("buffer loop")
            return x
        for y in list(bufs):
            del g.kind[y]
        for y, k in list(g.kind.items()):
            if k[0] == "N":
                g.kind[y] = ("N", rb(k[1]), rb(k[2]))
            elif k[0] == "L":
                g.kind[y] = ("L", rb(k[1]))
        for rp in g.rports.values():
            rp.addr = [rb(x) for x in rp.addr]
        for mem in g.mems.values():
            for wp in mem.wports:
                wp.we, wp.addr, wp.data = rb(wp.we), [rb(x) for x in wp.addr], [rb(x) for x in wp.data]
        g.outputs = [rb(x) for x in g.outputs]
    info = {"inputs": [f"{n}[{i}]" for n, i in in_map], "outputs": [f"{n}[{i}]" for n, i in out_map],
            "clock": clock_port, "warnings": sorted(set(warnings)), **stats,
            "input_ports": [(n, len(p["bits"])) for n, p in ins],
            "output_ports": [(n, len(p["bits"])) for n, p in outs],
            "memories": memories}
    return g, info


# ---------------------------------------------------------------- optimization

def simplify(g: Graph) -> Graph:
    """Constant folding, double negation, NAND(x, NOT x), structural hashing, ROM address constant folding
    (single-port ROMs), constant ROM columns, duplicate read ports, reads of never-written RAM (zero),
    write ports with we = 0, dead-element removal. Returns a new graph with the same ids for kept nodes."""
    g = dce(g)
    alias: Dict[int, int] = {}

    def r(x: int) -> int:
        while x in alias:
            x = alias[x]
        return x

    # a RAM bank without write ports holds zero forever
    for p, rp in g.rports.items():
        mem = g.mems[rp.mem]
        if mem.kind == "ram" and not mem.wports:
            for y in rp.outs:
                alias[y] = 0
    order = g.units()
    nports: Dict[int, int] = {}
    for rp in g.rports.values():
        nports[rp.mem] = nports.get(rp.mem, 0) + 1

    ng = Graph(g.n_in, next_id=g.next_id)
    strash: Dict[Tuple[int, int], int] = {}
    pstrash: Dict[Tuple[int, tuple], int] = {}
    for x, k in g.kind.items():
        if k[0] == "L":
            ng.kind[x] = ("L", k[1])
    for m, mem in g.mems.items():
        ng.mems[m] = Mem(mem.kind, mem.a, mem.w, list(mem.entries), [], mem.memid, list(mem.cols), mem.bank,
                         mem.size, mem.width)

    def nand(x: int, a: int, b: int) -> None:
        if a > b:
            a, b = b, a
        if a == 0:
            alias[x] = 1
            return
        if a == 1:
            if b == 1:
                alias[x] = 0
                return
            a = b
        if a == b:
            inner = ng.is_not(a)
            if inner is not None:
                alias[x] = inner
                return
        else:
            if ng.is_not(a) == b or ng.is_not(b) == a:
                alias[x] = 1
                return
        if (a, b) in strash:
            alias[x] = strash[(a, b)]
            return
        strash[(a, b)] = x
        ng.kind[x] = ("N", a, b)

    for u in order:
        if not isinstance(u, tuple):
            k = g.kind[u]
            nand(u, r(k[1]), r(k[2]))
            continue
        if u[0] == "W":
            wp = g.mems[u[1]].wports[u[2]]
            we = r(wp.we)
            if we == 0:
                continue
            ng.mems[u[1]].wports.append(WPort(we, [r(a) for a in wp.addr], [r(d) for d in wp.data]))
            continue
        p = u[1]
        rp = g.rports[p]
        mem = ng.mems[rp.mem]
        addr = [r(a) for a in rp.addr]
        if all(y in alias for y in rp.outs):
            continue
        if mem.kind == "rom":
            entries = mem.entries
            if nports[rp.mem] == 1:
                # single read port: fold constant address bits into a smaller ROM
                keep = []
                for a in addr:
                    if a in (0, 1):
                        entries = _select_bit(entries, len(keep), a)
                    else:
                        keep.append(a)
                addr = keep
                if keep:
                    mem.a, mem.entries = len(keep), entries
            if all(a in (0, 1) for a in addr):      # also an empty address (all bits folded)
                e = entries[sum(a << i for i, a in enumerate(addr))]
                for jj, y in enumerate(rp.outs):
                    alias[y] = (e >> jj) & 1
                continue
            for jj, y in enumerate(rp.outs):
                col = {(e >> jj) & 1 for e in entries}
                if len(col) == 1:
                    alias[y] = col.pop()
            if all(y in alias for y in rp.outs):
                continue
        key = (rp.mem, tuple(addr))
        if key in pstrash:      # the same memory read at the same address in the same beat
            for y, z in zip(rp.outs, ng.rports[pstrash[key]].outs):
                alias[y] = z
            continue
        pstrash[key] = p
        ng.rports[p] = RPort(rp.mem, addr, list(rp.outs))
        for jj, y in enumerate(rp.outs):
            ng.kind[y] = ("R", p, jj)
    for x, k in list(ng.kind.items()):
        if k[0] == "L":
            ng.kind[x] = ("L", r(k[1]))
    ng.outputs = [r(o) for o in g.outputs]
    return dce(ng)


def _select_bit(entries: List[int], pos: int, val: int) -> List[int]:
    """Entries of the sub-ROM obtained by fixing address bit `pos` (among the remaining bits) to val."""
    return [e for k, e in enumerate(entries) if ((k >> pos) & 1) == val]


def dce(g: Graph) -> Graph:
    """Keep what the outputs depend on. A live RAM read port keeps every write port of its bank; a memory
    with no live read port disappears with its write ports (its contents are unobservable)."""
    live = set()
    live_ports = set()
    live_mems = set()
    stack = list(g.outputs)
    while stack:
        x = stack.pop()
        if x in live or x not in g.kind:
            continue
        live.add(x)
        k = g.kind[x]
        if k[0] == "N":
            stack += [k[1], k[2]]
        elif k[0] == "L":
            stack.append(k[1])
        else:
            p = k[1]
            if p in live_ports:
                continue
            live_ports.add(p)
            rp = g.rports[p]
            stack += list(rp.addr) + list(rp.outs)
            if rp.mem not in live_mems:
                live_mems.add(rp.mem)
                for wp in g.mems[rp.mem].wports:
                    stack += [wp.we] + list(wp.addr) + list(wp.data)
    ng = Graph(g.n_in, next_id=g.next_id)
    ng.kind = {x: k for x, k in g.kind.items() if x in live}
    ng.rports = {p: rp for p, rp in g.rports.items() if p in live_ports}
    ng.mems = {m: mem for m, mem in g.mems.items() if m in live_mems}
    ng.outputs = list(g.outputs)
    return ng


def share_nots(g: Graph) -> int:
    """Rewrite NAND(x, NOT c) as NAND(x, NAND(x, c)) when it does not add gates
    (x AND NOT c == x AND NOT (x AND c)). This finds the 4-NAND XOR. Returns the number of rewrites."""
    fan: Dict[int, int] = {}

    def add(x: int, d: int = 1) -> None:
        fan[x] = fan.get(x, 0) + d

    strash: Dict[Tuple[int, int], int] = {}
    for x, k in g.kind.items():
        if k[0] == "N":
            for s in {k[1], k[2]}:
                add(s)
            strash[(min(k[1], k[2]), max(k[1], k[2]))] = x
        elif k[0] == "L":
            add(k[1])
    for _, refs in g.port_refs():
        for s in set(refs):
            add(s)
    for o in g.outputs:
        add(o)
    n = 0
    for u in g.units():
        if isinstance(u, tuple) or g.kind.get(u, ("?",))[0] != "N":
            continue
        _, a, b = g.kind[u]
        if a == b:
            continue
        for x, y in ((a, b), (b, a)):
            c = g.is_not(y)
            if c is None or c == x or x in (0, 1) or g.is_not(x) == c:
                continue
            key = (min(x, c), max(x, c))
            m = strash.get(key)
            gain = (1 if fan.get(y, 0) == 1 else 0) - (0 if m is not None else 1)
            if gain < 0 or m == u:
                continue
            if m is None:
                m = g.new(("N", key[0], key[1]))
                strash[key] = m
                add(x)
                add(c)
            add(m)
            add(y, -1)
            del strash[(min(a, b), max(a, b))]
            g.kind[u] = ("N", x, m)
            nk = (min(x, m), max(x, m))
            strash.setdefault(nk, u)
            if fan.get(y, 0) == 0:
                del g.kind[y]
                strash.pop((c, c), None)
                add(c, -1)
            n += 1
            break
    return n


def optimize(g: Graph, level: int = 2) -> Graph:
    if level <= 0:
        return dce(g)
    g = simplify(g)
    if level < 2:
        return g
    for _ in range(20):
        before = g.counts()
        changed = share_nots(g)
        g = simplify(g)
        if not changed and g.counts() == before:
            break
    return g


# ---------------------------------------------------------------- layout and encoding

@dataclass
class Layout:
    # ("N", x, y) / ("L", d) / ("ROMRD", mem, addr, w, entries) / ("RAMR", bank, addr, w) /
    # ("RAMW", bank, a, we, addr, data), all references are signal indices
    records: List[tuple]
    n_in: int
    n_out: int
    tail: Dict[str, int] = field(default_factory=dict)


TAIL_KINDS = ("move", "latch_move", "port_move", "copy", "latch_copy", "port_copy", "const", "buf")


def layout(g: Graph, latch_copy: bool = True) -> Layout:
    """Lay the graph out as records. The outputs must be the last nOut signals; for each output bit the tail
    uses, in this order of preference:
      move        its NAND, if nothing but LATCHes and later tail records reads it        (0 extra records)
      latch_move  its LATCH, same condition (a LATCH d may be a forward reference)        (0)
      port_move   its ROM / RAM read port, if the port's w outputs are w consecutive output bits in order
                  and nothing but LATCHes and later tail records reads them               (0)
      copy        a second NAND with the same inputs                                      (1 NAND)
      latch_copy  a second LATCH with the same d (same value on every beat, one more state bit)   (1 LATCH)
      port_copy   a second ROM read port 0x13 with the same address, if the port's outputs are consecutive
                  output bits in order (not for RAM: a RAM read port is a memory proof)  (1 port record)
      const       NAND(1,1) or NAND(0,0)                                                  (1 NAND)
      buf         two inverters (inputs, or LATCH / port bits that cannot use the above)  (2 NAND, 1 may be shared)"""
    n_in = g.n_in
    outs = g.outputs
    n_out = len(outs)
    count: Dict[int, int] = {}
    for o in outs:
        count[o] = count.get(o, 0) + 1
    # users in the body (NANDs and ports; LATCH d references do not constrain the order)
    users: Dict[int, List] = {}
    for x, k in g.kind.items():
        if k[0] == "N":
            for s in {k[1], k[2]}:
                users.setdefault(s, []).append(x)
    for u, refs in g.port_refs():
        for s in set(refs):
            users.setdefault(s, []).append(u)
    # candidates for moving: slot range and the nodes they produce
    cands: Dict[object, Tuple[int, int, List[int]]] = {}
    j = 0
    while j < n_out:
        o = outs[j]
        k = g.kind.get(o)
        if k and count[o] == 1 and k[0] == "N":
            cands[o] = (j, j, [o])
        elif k and count[o] == 1 and k[0] == "L":
            cands[o] = (j, j, [o])
        elif k and k[0] == "R" and k[2] == 0:
            rp = g.rports[k[1]]
            w = len(rp.outs)
            if outs[j:j + w] == rp.outs and all(count[y] == 1 for y in rp.outs):
                cands[("P", k[1])] = (j, j + w - 1, list(rp.outs))
                j += w
                continue
        j += 1

    while True:
        covered = {}
        for c, (s, e, nodes) in cands.items():
            for t in range(s, e + 1):
                covered[t] = c
        # the other tail records and what they read, by slot
        # (copies read the inputs of their NAND, port copies the address of their port; LATCH copies may
        # read forward, buffer pairs read a NOT in the body, constants nothing)
        tail_refs: List[Tuple[int, List[int]]] = []
        for t, o in enumerate(outs):
            if t in covered:
                continue
            k = g.kind.get(o)
            if k and k[0] == "N":
                tail_refs.append((t, [k[1], k[2]]))
            elif k and k[0] == "R" and k[2] == 0 and g.mems[g.rports[k[1]].mem].kind == "rom":
                rp = g.rports[k[1]]
                if outs[t:t + len(rp.outs)] == rp.outs:
                    tail_refs.append((t, list(rp.addr)))
        tail_users: Dict[int, List[int]] = {}
        for t, refs in tail_refs:
            for s in refs:
                tail_users.setdefault(s, []).append(t)
        bad = []
        for c, (s, e, nodes) in cands.items():
            ok = True
            for x in nodes:
                for u in users.get(x, []):
                    uc = u if not isinstance(u, tuple) else (u if u[0] == "P" else None)
                    if uc is None or uc not in cands or cands[uc][0] <= e:
                        ok = False
                        break
                if ok and any(t <= e for t in tail_users.get(x, [])):
                    ok = False
                if not ok:
                    break
            # (a candidate that reads another candidate at the same or a later slot is a user of it, so the
            # other one fails this test and leaves the tail)
            if not ok:
                bad.append(c)
        if not bad:
            break
        for c in bad:
            del cands[c]

    covered = {}
    for c, (s, e, nodes) in cands.items():
        for t in range(s, e + 1):
            covered[t] = c
    plan: List[tuple] = []
    t = 0
    while t < n_out:
        if t in covered:
            c = covered[t]
            s, e, nodes = cands[c]
            if isinstance(c, tuple):
                plan.append(("port_move", c[1]))
            elif g.kind[c][0] == "L":
                plan.append(("latch_move", c))
            else:
                plan.append(("move", c))
            t = e + 1
            continue
        o = outs[t]
        k = g.kind.get(o)
        if o in (0, 1):
            plan.append(("const", o))
        elif k and k[0] == "N":
            plan.append(("copy", k[1], k[2]))
        elif k and k[0] == "L" and latch_copy:
            plan.append(("latch_copy", k[1]))
        elif (k and k[0] == "R" and k[2] == 0 and g.mems[g.rports[k[1]].mem].kind == "rom"
              and outs[t:t + len(g.rports[k[1]].outs)] == g.rports[k[1]].outs):
            # ROM read port 0x13 only: a second RAM read port would cost a memory proof in every segment
            # that holds it (4 extWords, about 25,000 gas in the reference verifier), more than 2w NANDs
            plan.append(("port_copy", k[1]))
            t += len(g.rports[k[1]].outs)
            continue
        else:
            plan.append(("buf", o))
        t += 1
    moved = {p[1] for p in plan if p[0] in ("move", "latch_move")}
    moved_ports = {p[1] for p in plan if p[0] == "port_move"}
    # NOT gates needed in the body for buffer pairs
    strash = {(min(k[1], k[2]), max(k[1], k[2])): x for x, k in g.kind.items() if k[0] == "N" and x not in moved}
    body_extra: Dict[int, int] = {}
    for p in plan:
        if p[0] == "buf":
            o = p[1]
            if (o, o) in strash:
                body_extra[o] = strash[(o, o)]
            elif o not in body_extra:
                body_extra[o] = g.new(("N", o, o))
    # signal numbering
    sig: Dict[int, int] = {0: 0, 1: 1}
    for i in range(n_in):
        sig[2 + i] = 2 + i
    nxt = 2 + n_in
    records: List[tuple] = []
    for x in sorted(x for x, k in g.kind.items() if k[0] == "L" and x not in moved):
        sig[x] = nxt
        nxt += 1
        records.append(("L", x))
    for u in g.units():
        if isinstance(u, tuple) and u[0] == "P":
            if u[1] in moved_ports:
                continue
            records.append(("P", u[1]))
            for y in g.rports[u[1]].outs:
                sig[y] = nxt
                nxt += 1
        elif isinstance(u, tuple):
            records.append(("W", u[1], u[2]))
        elif u not in moved:
            sig[u] = nxt
            nxt += 1
            records.append(("N", u))
    for o, nid in sorted(body_extra.items(), key=lambda t: t[1]):
        if nid not in sig:
            sig[nid] = nxt
            nxt += 1
            records.append(("N", nid))
    tail = []
    for p in plan:
        if p[0] in ("move", "latch_move"):
            sig[p[1]] = nxt
            tail.append(("N", p[1]) if p[0] == "move" else ("L", p[1]))
            nxt += 1
        elif p[0] in ("port_move", "port_copy"):
            rp = g.rports[p[1]]
            if p[0] == "port_move":
                for y in rp.outs:
                    sig[y] = nxt
                    nxt += 1
            else:
                nxt += len(rp.outs)
            tail.append(("P", p[1]))
        else:
            if p[0] == "copy":
                tail.append(("NS", p[1], p[2]))
            elif p[0] == "latch_copy":
                tail.append(("LS", p[1]))
            elif p[0] == "const":
                tail.append(("NC", 1 - p[1]))   # NAND(1,1) = 0, NAND(0,0) = 1
            else:
                tail.append(("NB", body_extra[p[1]]))
            nxt += 1
    final: List[tuple] = []
    S = lambda x: sig[x]  # noqa: E731
    for rec in records + tail:
        if rec[0] == "L":
            final.append(("L", S(g.kind[rec[1]][1])))
        elif rec[0] == "LS":
            final.append(("L", S(rec[1])))
        elif rec[0] == "N":
            k = g.kind[rec[1]]
            final.append(("N", S(k[1]), S(k[2])))
        elif rec[0] == "NS":
            final.append(("N", S(rec[1]), S(rec[2])))
        elif rec[0] in ("NC", "NB"):
            final.append(("N", S(rec[1]), S(rec[1])))
        elif rec[0] == "P":
            rp = g.rports[rec[1]]
            mem = g.mems[rp.mem]
            addr = [S(a) for a in rp.addr]
            if mem.kind == "rom":
                final.append(("ROMRD", rp.mem, addr, mem.w, list(mem.entries)))
            else:
                final.append(("RAMR", mem.bank, addr, mem.w))
        else:  # ("W", mem, k)
            mem = g.mems[rec[1]]
            wp = mem.wports[rec[2]]
            final.append(("RAMW", mem.bank, mem.a, S(wp.we), [S(a) for a in wp.addr], [S(d) for d in wp.data]))
    if nxt > MAX_SIGNALS:
        raise ConversionError(f"{nxt} signals > 2^24")
    tail_counts = {k: 0 for k in TAIL_KINDS}
    for p in plan:
        tail_counts[p[0]] += 1
    return Layout(final, n_in, n_out, tail_counts)


def encode(lay: Layout, version: int) -> bytes:
    rec = bytearray()
    rom_off: Dict[int, int] = {}
    has_ram = False
    for r in lay.records:
        off = 16 + len(rec)
        if r[0] == "N":
            rec += tapnet2.enc_nand(r[1], r[2])
        elif r[0] == "L":
            rec += tapnet2.enc_latch(r[1])
        else:
            if version != 2:
                raise ConversionError("ROM and RAM records need tapnet/2")
            if r[0] == "ROMRD":
                _, m, addr, w, entries = r
                if m not in rom_off:
                    rom_off[m] = off
                    rec += tapnet2.enc_rom(addr, w, tapnet2.pack_rom_data(entries, len(addr), w))
                else:
                    rec += tapnet2.enc_romp(rom_off[m], addr, w)
            elif r[0] == "RAMR":
                has_ram = True
                rec += tapnet2.enc_ramr(r[1], r[2], r[3])
            else:
                has_ram = True
                rec += tapnet2.enc_ramw(r[1], r[4], r[5], r[3])
    data = tapnet2.make_file(bytes(rec), lay.n_in, lay.n_out, version, tapnet2.FLAG_MEM if has_ram else 0)
    try:
        check_file(data)
    except tapnet2.Invalid as e:  # never write an invalid file
        raise ConversionError(f"internal error: generated file is invalid: {e}")
    return data


# ---------------------------------------------------------------- driver

@dataclass
class Result:
    data: bytes
    version: int
    info: dict


def convert(sources: Sequence[str], top: str, version: int = 1, rom: bool = False, ram: bool = False,
            ports: Optional[Sequence[str]] = None, clock: Optional[str] = None, opt: int = 2,
            rom_min_abits: int = 3, ram_min_abits: int = 1, keep_dir: Optional[str] = None,
            defines: Sequence[str] = (), best: bool = False, latch_copy: bool = True) -> Result:
    """Synthesize and encode. best=True also tries the ABC_SCRIPTS and keeps the smallest file."""
    if (rom or ram) and version != 2:
        raise ConversionError("--rom and --ram need --format 2 (tapnet/1 has no ROM or RAM records)")
    keep_mems = rom or ram
    map_cells: List[str] = []
    mem_decisions: Dict[str, str] = {}
    mem_warnings: List[str] = []
    if keep_mems:
        for name, cell in sorted(list_memories(sources, top, defines).items()):
            kind, why = memory_plan(cell, rom, ram, rom_min_abits, ram_min_abits)
            memid = _memid_name(cell["parameters"].get("MEMID", name))
            if kind is None:
                map_cells.append(name)
                mem_decisions[memid] = "flip-flops: " + why
            else:
                mem_decisions[memid] = kind.upper()
                init = cell["parameters"].get("INIT", "")
                if kind == "ram" and isinstance(init, str) and "x" in init:
                    mem_warnings.append(f"memory {memid}: no initial value for some words, taken as 0 "
                                        "(tapnet/2 memory starts all zero; Verilog simulates x)")
    cands = [("default", None)] + (sorted(ABC_SCRIPTS.items()) if best else [])
    chosen = None
    for label, script in cands:
        with tempfile.NamedTemporaryFile("w", suffix=".abc", delete=False) as sf:
            sf.write((script or "") + "\n")
        try:
            pre, j, _log = run_yosys(sources, top, keep_mems, map_cells, keep_dir=keep_dir, defines=defines,
                                     abc_script=sf.name if script else None)
        finally:
            os.unlink(sf.name)
        check_pre_ffs(pre, top)
        g, info = graph_from_yosys(j, top, ports, clock)
        g = optimize(g, opt)
        lay = layout(g, latch_copy)
        data = encode(lay, version)
        info["abc_script"] = label
        info["memory_decisions"] = mem_decisions
        info["warnings"] = sorted(set(info["warnings"] + mem_warnings))
        # the records each memory became (after optimization; a dropped bank is unobservable)
        for mi in info["memories"]:
            for rr in mi["records"]:
                rr["kept"] = rr["mem"] in g.mems
        if chosen is None or len(data) < len(chosen[0]):
            chosen = (data, info, lay)
    data, info, lay = chosen
    metrics = check_file(data)
    if version == 2:
        info["netlistHash2"] = tapnet2.hx(tapnet2.netlist_hash2(data))
        if not data[7] & tapnet2.FLAG_MEM:
            exp = tapnet2.expand_rom_to_tapnet1(data)
            info["expandedTapnet1"] = {"bytes": len(exp), **tapnet2.decode(exp, (1,)).metrics()}
    info.update(metrics)
    info.update({"format": f"tapnet/{version}", "bytes": len(data), "tail": lay.tail, "top": top})
    return Result(data, version, info)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="Verilog source files")
    ap.add_argument("--top", required=True, help="top module")
    ap.add_argument("-o", "--output", help="output file (default <top>.tapnet or .tapnet2)")
    ap.add_argument("--format", type=int, choices=(1, 2), default=1, help="tapnet version (default 1)")
    ap.add_argument("--rom", action="store_true", help="tapnet/2: read-only memories become ROM records "
                    "(a second read port of the same ROM becomes a ROM read port 0x13)")
    ap.add_argument("--ram", action="store_true", help="tapnet/2: memories with synchronous write and "
                    "asynchronous read become RAM banks (read ports 0x11, write ports 0x12)")
    ap.add_argument("--rom-min-abits", type=int, default=3, help="smallest ROM address width kept as ROM")
    ap.add_argument("--ram-min-abits", type=int, default=1, help="smallest RAM address width kept as RAM")
    ap.add_argument("--no-latch-copy", action="store_true",
                    help="output bits that are flip-flops: two inverters instead of a second LATCH")
    ap.add_argument("--ports", help="comma-separated port order (inputs and outputs; clock excluded)")
    ap.add_argument("--clock", help="clock port name (default: detected from the flip-flops)")
    ap.add_argument("-O", "--opt", type=int, default=2, choices=(0, 1, 2), help="post-Yosys optimization")
    ap.add_argument("-D", "--define", action="append", default=[], help="Verilog define")
    ap.add_argument("--keep", help="keep Yosys files in this directory")
    ap.add_argument("--best", action="store_true", help="also try other ABC scripts, keep the smallest")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    try:
        res = convert(a.sources, a.top, a.format, a.rom, a.ram, a.ports.split(",") if a.ports else None,
                      a.clock, a.opt, a.rom_min_abits, a.ram_min_abits, a.keep, a.define, a.best,
                      not a.no_latch_copy)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = a.output or f"{a.top}.tapnet" + ("2" if a.format == 2 else "")
    with open(out, "wb") as f:
        f.write(res.data)
    if a.json:
        print(json.dumps(res.info, indent=2))
    else:
        i = res.info
        line = (f"{out}: {i['format']} {i['bytes']} bytes, nIn={i['nIn']} nOut={i['nOut']} "
                f"nand={i['nand']} latch={i['latch']}")
        if a.format == 2:
            line += f" romRecords={i['romRecords']} romBits={i['romBits']}"
            if "ramBanks" in i:
                line += (f" ramBanks={i['ramBanks']} ramBits={i['ramBits']} ramReadPorts={i['ramReadPorts']}"
                         f" ramWritePorts={i['ramWritePorts']} romPorts={i['romPorts']}")
        print(line)
        print("inputs : " + " ".join(i["inputs"]) + (f"   (clock {i['clock']})" if i["clock"] else ""))
        print("outputs: " + " ".join(i["outputs"]))
        for memid, d in sorted(i["memory_decisions"].items()):
            print(f"memory {memid}: {d}")
        print("tail   : " + " ".join(f"{k}={v}" for k, v in i["tail"].items() if v))
        if "netlistHash2" in i:
            print(f"netlistHash2: {i['netlistHash2']}")
        for w in i["warnings"]:
            print("warning: " + w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
