"""Two-pass assembler for TAPBOY-8 (ISA in isa.py).

Syntax, one statement per line, `;` starts a comment:
    label:                     defines a label (may share a line with an instruction)
    .equ NAME expr             defines a constant
    .print "text"              expands to LD #c / OUT for every character (escapes: \\n \\t \\\\ \\")
    LD #expr                   immediate operand
    LD [expr]                  memory operand  RAM[expr]
    LD [expr+B]                indexed operand RAM[(expr + B) mod 256]
    ST [expr] / ST [expr+B]
    JMP expr / JZ expr / JNZ expr
    NOP / IN / OUT / SWAP / HALT
An expr is a sum/difference of numbers (decimal, 0x.., 0b.., 'c') and names (labels, .equ constants).

    python3 assembler.py programs/dodge.asm      # prints a listing
"""
from __future__ import annotations

import re
import sys
from typing import Dict, List, Tuple

from isa import ADDRESS_OPS, BARE_OPS, HALT_WORD, JUMP_OPS, OPERAND_OPS, OPS, ROM_WORDS, disassemble, encode


class AsmError(ValueError):
    pass


_TOKEN = re.compile(r"\s*([+-])?\s*('(?:\\.|[^'])'|0x[0-9a-fA-F]+|0b[01]+|\d+|[A-Za-z_][A-Za-z0-9_]*)")


def _unescape(s: str) -> str:
    return bytes(s, "utf-8").decode("unicode_escape")


def _value(tok: str, names: Dict[str, int], strict: bool) -> int:
    if tok.startswith("'"):
        ch = _unescape(tok[1:-1])
        if len(ch) != 1:
            raise AsmError(f"bad character literal {tok}")
        return ord(ch)
    if tok[0].isdigit():
        return int(tok, 0)
    if tok in names:
        return names[tok]
    if strict:
        raise AsmError(f"unknown name {tok!r}")
    return 0


def eval_expr(expr: str, names: Dict[str, int], strict: bool = True) -> int:
    expr = expr.strip()
    pos, total, first = 0, 0, True
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or (m.group(1) is None and not first):
            raise AsmError(f"cannot parse expression {expr!r}")
        v = _value(m.group(2), names, strict)
        total += -v if m.group(1) == "-" else v
        pos, first = m.end(), False
    if first:
        raise AsmError("empty expression")
    return total


def _split_comment(line: str) -> str:
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q and (len(out) < 2 or out[-2] != "\\"):
                q = None
        elif ch in "'\"":
            q = ch
            out.append(ch)
        elif ch == ";":
            break
        else:
            out.append(ch)
    return "".join(out).strip()


def _statements(src: str) -> List[Tuple[int, str]]:
    """(line number, statement) with labels split off as their own statements."""
    out = []
    for n, raw in enumerate(src.splitlines(), 1):
        line = _split_comment(raw)
        while True:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
            if not m:
                break
            out.append((n, m.group(1) + ":"))
            line = m.group(2)
        if line:
            out.append((n, line))
    return out


def _expand(stmts):
    """Expands .print into LD/OUT pairs; returns (line, mnemonic, operand-text) items and .equ/labels."""
    items = []
    for n, s in stmts:
        if s.endswith(":") and " " not in s:
            items.append((n, "LABEL", s[:-1]))
            continue
        parts = s.split(None, 1)
        mn = parts[0].upper() if not parts[0].startswith(".") else parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if mn == ".print":
            m = re.fullmatch(r'"((?:\\.|[^"])*)"', arg)
            if not m:
                raise AsmError(f"line {n}: .print needs a quoted string")
            for ch in _unescape(m.group(1)):
                items.append((n, "LD", f"#{ord(ch)}"))
                items.append((n, "OUT", ""))
        else:
            items.append((n, mn, arg))
    return items


def _encode(n: int, mn: str, arg: str, names: Dict[str, int], strict: bool) -> int:
    if mn not in OPS:
        raise AsmError(f"line {n}: unknown mnemonic {mn}")
    if mn in BARE_OPS:
        if arg:
            raise AsmError(f"line {n}: {mn} takes no operand")
        return encode(mn)
    if mn in JUMP_OPS:
        return encode(mn, _byte(n, eval_expr(arg, names, strict), strict))
    if arg.startswith("#"):
        if mn not in OPERAND_OPS:
            raise AsmError(f"line {n}: {mn} takes no immediate")
        return encode(mn, _byte(n, eval_expr(arg[1:], names, strict), strict))
    m = re.fullmatch(r"\[(.*?)(\+\s*B)?\]", arg, re.IGNORECASE)
    if not m:
        raise AsmError(f"line {n}: bad operand {arg!r}")
    if mn not in OPERAND_OPS and mn not in ADDRESS_OPS:
        raise AsmError(f"line {n}: {mn} takes no memory operand")
    imm = _byte(n, eval_expr(m.group(1), names, strict), strict)
    return encode(mn, imm, m=mn in OPERAND_OPS, x=m.group(2) is not None)


def _byte(n: int, v: int, strict: bool) -> int:
    if strict and not -128 <= v <= 255:
        raise AsmError(f"line {n}: value {v} does not fit in a byte")
    return v & 0xFF


def assemble(src: str):
    """Returns (rom words padded to 256 with HALT, symbols, listing lines)."""
    items = _expand(_statements(src))
    names: Dict[str, int] = {}
    pc = 0
    for n, mn, arg in items:                     # pass 1: labels and constants
        if mn == "LABEL":
            if arg in names:
                raise AsmError(f"line {n}: {arg} defined twice")
            names[arg] = pc
        elif mn == ".equ":
            k, v = arg.split(None, 1)
            names[k] = eval_expr(v, names)
        elif mn.startswith("."):
            raise AsmError(f"line {n}: unknown directive {mn}")
        else:
            pc += 1
    if pc > ROM_WORDS:
        raise AsmError(f"program has {pc} instructions; the ROM holds {ROM_WORDS}")
    words, listing = [], []
    for n, mn, arg in items:                     # pass 2: encode
        if mn == "LABEL":
            listing.append(f"{'':12s}{arg}:")
        elif not mn.startswith("."):
            w = _encode(n, mn, arg, names, True)
            listing.append(f"  {len(words):02X}  {w:04X}    {disassemble(w)}")
            words.append(w)
    used = len(words)
    words += [HALT_WORD] * (ROM_WORDS - used)
    return words, names, listing


def assemble_file(path: str):
    with open(path, encoding="utf-8") as f:
        return assemble(f.read())


if __name__ == "__main__":
    words, names, listing = assemble_file(sys.argv[1])
    print("\n".join(listing))
    print(f"; {sum(1 for l in listing if not l.endswith(':'))} instructions")
