"""Puts assets/tap-draft-large-netlists (tapnet2.py, bisect_sim.py) and tools/verilog2tapnet on sys.path, read-only use."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
REF_DIR = os.path.join(REPO, "assets", "tap-draft-large-netlists")
TOOL_DIR = os.path.join(REPO, "tools", "verilog2tapnet")
for p in (REF_DIR, TOOL_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import tapnet2 as t2  # noqa: E402,F401
