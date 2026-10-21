"""Converts rtl/mlp_digits.v into build/mlp_digits.tapnet2 with tools/verilog2tapnet (needs yosys on PATH) and
writes the converter's report to build/convert.json.

    python3 mlp_build.py [--no-best]

By default the converter also tries its other ABC scripts and keeps the smallest file (`--best`; the committed
file came from the `dch` script, see build/convert.json); `--no-best` uses the default Yosys `abc -g NAND` only.
The bytes of the file depend on the Yosys/ABC version (gate counts differ between releases); the committed file is
the one the README and build/numbers.json describe. Any rebuild must still pass test_inference.py, which compares
the netlist with mlp_model.py beat by beat.
"""
from __future__ import annotations

import json
import os
import sys

import _paths  # noqa: F401
import verilog2tapnet as v2t

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "rtl", "mlp_digits.v")
OUT = os.path.join(HERE, "build", "mlp_digits.tapnet2")
REPORT = os.path.join(HERE, "build", "convert.json")


def build(best: bool = True) -> v2t.Result:
    return v2t.convert([SRC], "mlp_digits", version=2, rom=True, ram=False, best=best)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    res = build(best="--no-best" not in argv)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(res.data)
    info = {k: v for k, v in res.info.items() if k not in ("inputs", "outputs")}
    with open(REPORT, "w") as f:
        json.dump(info, f, indent=1, sort_keys=True)
    m = res.info
    print(f"wrote {OUT}: {len(res.data):,} bytes, NAND {m['nand']:,}, LATCH {m['latch']}, ROM records "
          f"{m['romRecords']} ({m['romBits']:,} bits), signals {m['signals']:,}, abc script {m['abc_script']}")
    print("memories:", m["memory_decisions"], " warnings:", m["warnings"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
