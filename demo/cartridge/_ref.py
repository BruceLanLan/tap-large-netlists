"""Puts ../../assets/tap-draft-large-netlists on sys.path so that `import tapnet2` and `import bisect_sim` resolve (read-only use)."""
import os
import sys

REF_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "tap-draft-large-netlists"))
if REF_DIR not in sys.path:
    sys.path.insert(0, REF_DIR)

import tapnet2 as t2  # noqa: E402,F401
