# verilog2tapnet — Verilog → `tapnet/1` · `tapnet/2` (Yosys)

[English](#english) · [中文](#中文)

## English

`verilog2tapnet.py` synthesizes a Verilog design with [Yosys](https://github.com/YosysHQ/yosys) into a netlist
file of this draft: `tapnet/1` (NAND and LATCH records, version byte `0x01`) or `tapnet/2` (§1 of the TAP, with
ROM records `0x10`, RAM read ports `0x11`, RAM write ports `0x12`, ROM read ports `0x13` and the MEM flag).
`validate.py` simulates every design with [Icarus Verilog](https://github.com/steveicarus/iverilog) and compares
every beat with the reference evaluators of this repository: the outputs, and for RAM banks the whole memory.

It is self-contained in this repository: it imports `assets/tap-draft-large-netlists/tapnet2.py` (validity,
metrics, `evaluate_full`, `netlistHash2`, the canonical ROM expansion, segmentation; `tapnet/1` files are checked
with `tapnet2.decode(data, (1,))`) and loads the TAP-02 evaluator `assets/tap-02/reference.py` by path. It needs
Python 3.9+, `yosys` and, for validation, `iverilog`/`vvp` on `PATH` (or `$YOSYS`).

Minimum Yosys version: **0.69** (`MIN_YOSYS` in `verilog2tapnet.py`; the version the tool is developed and
verified with, Results below). An older Yosys converts and passes the behavioural tests (Ubuntu 24.04's 0.33:
all designs match iverilog beat by beat), but its ABC may map structurally identical designs to equivalent, not
identical, NAND records (`adder8` against `adder8_swapped`), so `test_same_structure_same_records` is skipped
below 0.69 with a message naming both versions. CI installs a pinned [OSS CAD
Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) nightly (`2026-10-07`, Yosys 0.69+, Icarus
Verilog included), see `docs/TESTING.md`.

### Usage

```sh
cd tools/verilog2tapnet
python3 verilog2tapnet.py designs/xor2.v --top xor2                              # -> xor2.tapnet
python3 verilog2tapnet.py designs/rom_wave.v --top rom_wave --format 2 --rom    # ROM record
python3 verilog2tapnet.py designs/regfile16x8.v --top regfile16x8 --format 2 --ram   # RAM ports
python3 verilog2tapnet.py designs/stack_cpu.v --top stack_cpu --format 2 --rom --ram --json
python3 -m unittest discover -s tests -v    # 21 tests, about 10 s
python3 validate.py                         # all designs, ABC variants, LATCH builds; tables; writes out/ (about 90 s)
```

Options: `--format 1|2`; `--rom` (read-only memories → ROM records; a second read port of the same ROM → ROM read
port `0x13`); `--ram` (memories with synchronous write and asynchronous read → RAM banks); `--rom-min-abits`
(default 3), `--ram-min-abits` (default 1); `--no-latch-copy` (see Output tail); `--ports` (port order; the clock
is not a port of the netlist); `--clock`; `-O 0|1|2`; `--best` (also try other ABC scripts, keep the smallest);
`--keep DIR`; `-D`; `--json`.

### Mapping

| Verilog | tapnet |
|---|---|
| input ports except the clock, in port-list order (or `--ports`), LSB first | signals `2 … 2+nIn−1` |
| output ports, same order | the last `nOut` signals |
| rising-edge flip-flops (synchronous reset and enable allowed) | LATCH records right after the inputs (forward `d`) |
| combinational logic | NAND records in topological order |
| read-only memory, asynchronous read, `a ≥ 3` (`--rom`) | one ROM record; each further read port a ROM read port `0x13` naming it |
| memory with synchronous write ports on the clock and asynchronous read ports, zero initial contents (`--ram`) | one RAM bank per group of columns with the same write enables (byte enables split banks), at most 256 bits wide; one RAM read port per read port and bank, one RAM write port per write port and bank, in Yosys priority order (the last write in file order wins) |
| one rising clock edge | one beat; outputs are the values before the edge; RAM reads see the memory before the edge, writes take effect for the next beat |

Flow: Yosys `proc; flatten; opt; fsm; alumacc; share; memory -nomap -nordff` → the tool decides per memory (a
first, short Yosys run lists them) → `memory_map` for the memories it cannot keep → `techmap; setundef -zero;
dfflegalize -cell $_DFF_P_ 0; abc -g NAND` → JSON → Python: constant folding, double negation, structural hashing,
the NOT-sharing rewrite (`NAND(x, NOT c) → NAND(x, NAND(x,c))`, which gives the 4-NAND XOR), duplicate read ports,
reads of never-written banks (zero), dead-element removal (a bank with no live read port goes with its write
ports) → layout → `tapnet2.decode` check of the written file.

A memory that cannot become a record is mapped to flip-flops and logic, and the report says why
(`memory_decisions`): nonzero initial contents (tapnet/2 memory starts all zero, §1.3), a clocked read port, a
read enable or reset, a negative-edge or unclocked write port, write ports on different clocks, wide ports, a
nonzero address offset, an address width outside the limits. The flip-flops then go through the usual checks
(negative edge and two clocks are rejected).

### Output tail (format friction, handled in the tool)

The outputs of a `tapnet` file are its last `nOut` signals, while LATCHes come first and NANDs must read
backwards. For each output bit the tool now uses the cheapest of:

| case | cost | before this change |
|---|---|---|
| `move`: the output's NAND moves to the tail, if only LATCHes and later tail records read it (other moved NANDs included) | 0 | 0 (only if nothing but LATCHes read it) |
| `latch_move`: the output's LATCH moves to the tail, if only LATCHes and later tail records read it (a LATCH `d` may point forward) | 0 | 2 NAND |
| `port_move`: a ROM / RAM read port whose `w` outputs are `w` consecutive output bits moves to the tail whole | 0 | 2 NAND per bit |
| `copy`: a second NAND with the same inputs (the NAND is also read in the body) | 1 NAND | 1 NAND |
| `latch_copy`: a second LATCH with the same `d` (identical value on every beat: both start at 0 and load `V[d]`); one more state bit | 1 LATCH (4 bytes) | 2 NAND (14 bytes; 7 when the NOT already exists) |
| `port_copy`: a second ROM read port `0x13` at the same address (not for RAM: a second RAM read port would add a memory proof, 4 `extWords` and about 25,000 gas, to every segment that holds it, more than the inverters cost) | 1 port record (`8+3a` bytes; gate cost `w+a`) | 2 NAND per bit |
| `const` | 1 NAND | 1 NAND |
| `buf`: two inverters (an input that is also an output; part of a port's bits; LATCHes with `--no-latch-copy`) | 2 NAND (1 if the NOT exists) | same |

Effect (bytes, default vs `--no-latch-copy`, which has the moves but not the LATCH copies): counter4 258 vs 298,
lfsr8 190 vs 230 (old tool 272), acc_cpu 3,930 vs 3,976, rom_wave `--rom` 367 vs 427 (old tool 539), regfile 94
(both read ports moved, 0 NAND). Remaining costs: an output whose NAND is also read in the body (1 NAND), a
register output (1 LATCH and one state bit, or 1–2 NAND), an input or constant that is an output (2 / 1 NAND),
some bits of a port and RAM read ports that are also read in the body (2 NAND per bit, 1 when the NOT exists). Removing these would need a format change (an output list), which this tool
does not make.

### Results (`python3 validate.py`, Yosys 0.69, Icarus Verilog 13.0, macOS)

Every design matches Icarus Verilog on every beat (exhaustive for small combinational designs, otherwise 1,000
random beats with seed 1, and again with seed 2 for the `--best` file); for `tapnet/2` files without RAM the
canonical ROM expansion is also run under the TAP-02 evaluator; for RAM banks the whole memory is compared on
every beat. All files have one canonical segment per beat under the default budget.

| design | format | NAND | LATCH | ROM bits | RAM bits | RAM R/W, ROM ports | bytes | NAND `--best` | tail move/latch/port · copy/latch/port | validation |
|---|---|---|---|---|---|---|---|---|---|---|
| xor2 | 1 | **4** | 0 | – | – | – | 44 | 4 | 1/0/0 · 0/0/0 | 4 beats, exhaustive |
| xor_renamed | 1 | 4 | 0 | – | – | – | 44 | 4 | 1/0/0 · 0/0/0 | 4 beats, exhaustive |
| xor_sop | 1 | 4 | 0 | – | – | – | 44 | 4 | 1/0/0 · 0/0/0 | 4 beats, exhaustive |
| full_adder | 1 | 11 | 0 | – | – | – | 93 | 11 | 2/0/0 · 0/0/0 | 8 beats, exhaustive |
| full_adder_gates | 1 | 11 | 0 | – | – | – | 93 | 11 | 2/0/0 · 0/0/0 | 8 beats, exhaustive |
| adder8 | 1 | 89 | 0 | – | – | – | 639 | 87 | 9/0/0 · 0/0/0 | 1,002 random |
| adder8_swapped | 1 | 89 | 0 | – | – | – | 639 | 87 | 9/0/0 · 0/0/0 | 1,002 random |
| popcount8 | 1 | 57 | 0 | – | – | – | 415 | 57 | 4/0/0 · 0/0/0 | 256, exhaustive |
| counter4 | 1 | 30 | 8 | – | – | – | 258 | 30 | 0/0/0 · 0/4/0 | 1,000 sequence |
| lfsr8 | 1 | 18 | 12 | – | – | – | 190 | 18 | 1/3/0 · 0/4/0 | 1,000 sequence |
| traffic (FSM) | 1 | 51 | 8 | – | – | – | 405 | 49 | 3/0/0 · 2/3/0 | 1,000 sequence |
| alu8 | 1 | 355 | 0 | – | – | – | 2,501 | 324 | 2/0/0 · 8/0/0 | 1,002 random |
| acc_cpu | 1 | 526 | 58 | – | – | – | 3,930 | 471 | 1/0/0 · 0/13/0 | 1,000 sequence |
| rom_wave | 1 | 137 | 12 | – | – | – | 1,023 | 117 | 8/0/0 · 0/6/0 | 1,000 sequence |
| acc_cpu `--rom` | 2 | 582 | 58 | 128 | – | – | 4,354 | 582 | 1/0/0 · 0/13/0 | 1,000 + expansion |
| rom_wave `--rom` | 2 | 31 | 12 | 512 | – | – | 367 | 31 | 0/0/1 · 0/6/0 | 1,000 + expansion |
| counter4 | 2 | 30 | 8 | – | – | – | 258 | 30 | 0/0/0 · 0/4/0 | 1,000 + expansion |
| **rom_dual** `--rom` | 2 | 35 | 12 | 512 | – | 0/0, 1 | 421 | 33 | 0/0/2 · 0/6/0 | 1,000 + expansion |
| **regfile16x8** `--ram` | 2 | **0** | 0 | – | 128 | 2/1, 0 | **94** | 0 | 0/0/2 · 0/0/0 | 1,000 + memory 128 bits every beat |
| **ram_bytes** `--ram` | 2 | 0 | 0 | – | 16 | 2/3, 0 | 98 | 0 | 0/0/2 · 0/0/0 | 1,000 + memory 16 bits every beat |
| **stack_cpu** `--rom --ram` | 2 | 537 | 18 | 160 | 128 | 2/2, 1 | 4,031 | 480 | 1/0/0 · 0/9/0 | 1,000 + memory 128 bits every beat |
| **acc_cpu** `--rom --ram` | 2 | 403 | 26 | 128 | 32 | 1/1, 0 | 3,022 | 368 | 1/0/0 · 0/13/0 | 1,000 + memory 32 bits every beat |
| **acc_ram** `--rom --ram` | 2 | 451 | 26 | 160 | 128 | 1/1, 0 | 3,374 | 410 | 1/0/0 · 0/13/0 | 1,000 + memory 128 bits every beat |
| ram_init `--rom --ram` | 2 | 238 | 32 | – | – | – | 1,810 | 238 | 4/0/0 · 0/0/0 | 1,000 + expansion (memory kept as flip-flops: nonzero initial contents) |

RAM ports against LATCH-built memories (the same source converted with `--format 2 --rom`, so written memories
become flip-flops, multiplexers and decoders; ROMs stay ROM records). Both builds pass the same validation.

| design | memory | RAM-port build: bytes / NAND / LATCH | LATCH-built: bytes / NAND / LATCH | reduction |
|---|---|---|---|---|
| regfile16x8 | 16 × 8, 2 R + 1 W | 94 / 0 / 0 | 9,446 / 1,274 / 128 | 99% (100×) |
| ram_bytes | 4 × 4, 2 W with partial enables | 98 / 0 / 0 | 1,214 / 162 / 16 | 92% (12×) |
| stack_cpu | 16 × 8, 2 R + 2 W, program ROM with a ROM read port | 4,031 / 537 / 18 | 16,321 / 2,237 / 146 | 75% (4.0×) |
| acc_ram | 16 × 8, 2 R + 1 W | 3,374 / 451 / 26 | 9,866 / 1,314 / 154 | 66% (2.9×) |
| acc_cpu | 4 × 8, 1 R + 1 W | 3,022 / 403 / 26 | 4,354 / 582 / 58 | 31% (1.4×) |
| rom_dual | 64 × 8 ROM read twice (ROM read port vs logic, no `--rom`) | 421 / 35 / 12 | 1,296 / 176 / 12 | 68% (3.1×) |

The demo cartridge of this repository shows the same effect at a larger size (5,781 against 104,076 bytes).

### Designs

`designs/*.v`: the combinational and sequential designs above; `regfile16x8.v` (16 × 8, two read ports, one write
port), `ram_bytes.v` (two write ports in one process, one of them writing half a word: two banks, priority kept),
`rom_dual.v` (one table read at two addresses: ROM + ROM read port), `stack_cpu.v` (stack machine: program ROM
read at `pc` and `pc+1`, a 16-entry stack in RAM read at `sp−1` and `sp−2` (the `tos` output reuses the first read through two inverters per bit), SWAP writes two entries; the stack
wraps, so a run writes all entries), `acc_ram.v` (the accumulator CPU with a 16 × 8 data memory addressed from
`imm` and from the input pins), `ram_init.v` (a written memory with initial contents: kept as flip-flops).
`designs/unsupported/*.v`: async reset, negative edge, transparent latch, two clocks, gated clock, tri-state,
combinational loop, a RAM on a second clock, a RAM written on the falling edge; all rejected with a clear message.

### Limitations

- Single clock, rising edge only; the clock must be a 1-bit top-level input. No asynchronous reset/set, tri-state,
  transparent latches or combinational loops.
- Power-on state is all zero (TAP-02). Flip-flops with constant initial values are stored inverted; registers
  without an initial value are taken as 0 (Verilog simulates them as x; the test designs give initial values).
  RAM banks start at zero, so a memory with nonzero initial contents stays flip-flops (open question 6 of the TAP).
- RAM: synchronous write on the design clock, asynchronous read. A registered read (`always @(posedge clk) q <=
  mem[a]`) becomes an asynchronous read port followed by flip-flops (`-nordff`), which is equivalent. Reads that
  Yosys gives a read enable, a reset or transparency logic are not RAM ports (they become flip-flops).
- The memory comparison covers top-level memories whose Yosys width and size equal the Verilog declaration and
  whose banks all stay in the file; a bank the optimizer drops is unobservable and not compared.
- `$readmemh` with a relative path fails in validation (vvp runs in a temporary directory).
- Gate counts are ABC's, not optimal (full adder 11, optimum 9), and depend on the Yosys version; the tests assert
  behaviour and record kinds, not sizes. ROM records cut logic optimization at the ROM boundary (acc_cpu: 582 NAND
  with its 16-word program in ROM against 526 with the program folded into logic).
- `tapnet/2` has no structural hash yet (open question 2), so the tool reports `netlistHash2` and, for files
  without RAM, the metrics of the canonical ROM expansion. The structural hash of `tapnet/1` files (another draft)
  and the comparison with circuits on chain are not part of this repository.

---

## 中文

`verilog2tapnet.py` 用 Yosys 把可综合的 Verilog 设计转成本草案的网表文件：`tapnet/1`（NAND、LATCH）或
`tapnet/2`（ROM `0x10`、RAM 读口 `0x11`、RAM 写口 `0x12`、ROM 读口 `0x13`、MEM 标志）。`validate.py` 用 Icarus
Verilog 逐拍对照仓库内的参考求值器：输出逐拍比较，RAM 存储体的全部内容也逐拍比较。工具只依赖本仓库的
`assets/tap-draft-large-netlists/tapnet2.py` 与 `assets/tap-02/reference.py`。

- `--ram`：同步写、异步读、初值为零的存储器 → RAM 存储体（按写使能分组拆成多个存储体，每组至多 256 位宽；
  多个写口按 Yosys 优先级排序，文件中靠后的写口优先）。初值非零等无法表示的情况自动退回触发器，并说明原因。
- `--rom`：只读存储器 → ROM 记录；同一 ROM 的其余读口 → ROM 读口 `0x13`。
- 输出尾部（格式摩擦，在工具里解决）：能整体移到文件末尾的 NAND、LATCH、ROM/RAM 读口都移过去（0 门）；寄存器
  输出默认用一个复制的 LATCH（4 字节，多一个状态位）代替两个反相器；ROM 读口输出可复制一个 ROM 读口（RAM 读口不复制：
  每个 RAM 读口在争议分段里都要一份存储证明）。剩余成本：在主体中
  也被读取的输出 NAND（1 门）、作为输出的输入或常数（2 / 1 门）、读口的部分位（每位 2 门）。
- 结果：16×8 寄存器堆 94 字节、0 个 NAND（LATCH 实现 9,446 字节，缩小 99%）；栈机 4,031 字节（LATCH 实现 16,321，
  缩小 75%）；16×8 数据存储器的累加器 CPU 3,374 字节（9,866，缩小 66%）。全部设计逐拍通过，RAM 内容逐拍一致。
- 限制：单时钟、上升沿；无异步复位、三态、透明锁存器、组合环；RAM 必须从零开始；门数依赖 Yosys 版本，测试只断言
  行为与记录种类；`tapnet/2` 的结构哈希尚未定义。
- Yosys 最低版本 **0.69**（`verilog2tapnet.py` 的 `MIN_YOSYS`，即开发与验证所用版本）。更旧的 Yosys 也能转换并通过
  逐拍行为测试（Ubuntu 24.04 的 0.33：全部设计与 iverilog 逐拍一致），但其 ABC 可能把结构相同的设计映射成等价
  而非完全相同的 NAND 记录（`adder8` 与 `adder8_swapped`），因此 `test_same_structure_same_records` 在 0.69 以下
  跳过并给出版本说明。CI 安装固定版本的 OSS CAD Suite（`2026-10-07`，含 Yosys 0.69+ 与 Icarus Verilog）。

运行：`cd tools/verilog2tapnet && python3 -m unittest discover -s tests -v`（21 项，约 10 秒），
`python3 validate.py`（全部设计与表格，约 90 秒）。
