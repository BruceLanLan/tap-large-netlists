---
tap: TBD
title: Large Netlists and Verifiable Execution
description: A netlist format with mask-ROM records and RAM ports, a chunked netlist hash with a staged on-chain upload, and the commitments, one-step verifiers and dispute rules that let a contract settle disagreements about runs of netlists far larger than one transaction.
author: Bruce (@BruceLanLan)
discussions-to: https://github.com/TapeOutProtocol/TAPs/issues/71
status: Draft
type: Standards
created: 2026-10-19
updated: 2026-10-21
requires: TAP-02, ERC-165
license: CC0-1.0
---

本文是 `TAP-draft-large-netlists.md` 的中文译文，仅供参考；与英文不一致时以英文为准（TAP-01 §6.2）。

# TAP-TBD：大网表与可验证执行

## 概要

本 TAP 让非常大的电路，例如一块有数百万个门和内置数据表的芯片，可以分块存储在链上，并让任何人都能在链上裁决关于这种电路计算结果的分歧。

## 摘要

本 TAP 定义 `tapnet/2`：在 TAP-02 的 NAND 与 LATCH 记录之外增加掩模 ROM 记录、ROM 读口和可写存储器（RAM 读口与写口）的网表格式；以及 `netlistHash2`：对文件 16,384 字节分块求出的哈希，使最大 64 MiB 的文件可以用一个值标识，并通过可选接口 `ITapLargeNetlistStore` 逐块存储在链上。对远超任何一笔交易容量的运行，本 TAP 规定承诺、文件的规范分段、对照已存储文件核对某一拍某一段的单步核对函数，以及争议博弈的数据、声明成立规则与二分规则，使得在安全考虑所述的前提下（SHA-256 抗碰撞、文件有效、数据可获取、输入的承诺一致），只要有一个正确计算的一方及时挑战，关于一次运行的错误声明就会被否决。保证金数额与超时的长短留给应用。一个信息性章节说明网表绑定 NFT（`TAP-draft-netlist-bound-nft`）可以如何使用这类网表。

## 动机

TapeOut 网表是一串 NAND 与 LATCH 记录（TAP-02）。要标识、存储这样的文件或核对它的计算结果，合约受限于一笔交易：在 BNB Smart Chain 上一笔交易至多 16,777,216 gas，只够对几十 KB 的文件求哈希、存储和校验。例如网表绑定 NFT 草案（`TAP-draft-netlist-bound-nft`）只允许合约存储和运行至多 49,152 字节的网表，这是其参考实现在一笔这样的交易内能存储并校验有效性的最大文件；这么大的文件约容纳 7,000 条 NAND 记录。这个上限不是 NAND 门或 LATCH 的性质，而是存储与交易的上限，而它恰好排除了最值得拥有链上身份和链上核对的那些设计：

- **可验证的 AI 推理。** 写成电路、权重放在 ROM 中的语言模型，每个输出 token 都由一次电路运行产生；任何人都能重算这次运行，双方有分歧时由合约裁决（见下文 INT-C16 的例子）。
- **可证明的游戏结果。** 写成电路的游戏，让玩家在给定按键输入的一次运行之后声明分数；任何不同意的人都可以挑战，由合约判定谁对。参考实现中的卡带演示就把这样一个游戏，一颗程序放在 ROM 中、带 256 字节 RAM 的 8 位 CPU，搭成一个 5,781 字节、只占一个分块的 `tapnet/2` 文件（见参考实现）。
- **面向 TapeOut 处理器的大型设计。** 用 TAP-02 记录搭建的设计，例如处理器、加速器与查找表，一旦包含存储器和数据表就会超出一笔交易。一种带 ROM 记录、RAM 端口与分块哈希的通用格式，让工具、浏览器和合约都能用一个值指代这样的设计。
- **NFT。** 网表绑定 NFT 可以承载大型设计，并仍然可以被工具和合约核对（§8）。

一个具体例子是作者发布在 https://jogjohgoeg.github.io/nand-llama-research/ 的 INT-C16 设计：一个在 NAND 层面描述的语言模型芯片，作者给出的预算约为 1,012,501 个 NAND 门和 94,800 个 LATCH 位，模型权重放在 60 个掩模 ROM 存储体中，每生成一个 token 约需 9,820 万个芯片时钟，SKY130 面积为 3.17 × 4.77 mm。作者说明，时钟数与面积是源级预算，尚无整机 RTL 时序或流片结果；本 TAP 只用这些数字估算问题规模。仅按 NAND 与 LATCH 记录写出、尚未计入任何 ROM 数据，这些数量就约占 7.5 MB，约为上述 49,152 字节上限的 152 倍（附录 A）。要在链上标识、存储并核对这样的设计，缺少三样东西：

1. **写出权重与存储器的方法。** 把 ROM 展开成 NAND 多路选择器会成倍放大体积（测试向量中的 AES S 盒：作为一条 ROM 记录 300 字节，展开后 6,288 字节）。掩模 ROM 也正是硬件所用的结构。用 LATCH 搭的可写存储器同样昂贵：卡带演示中，用 LATCH、多路选择器和写译码器搭的 256 字节 RAM 占了 104,076 字节中的 98,380 字节，而用 RAM 端口，文件降到 5,781 字节。
2. **适合交易的哈希与上传方式。** 7.5 MB 的 SHA-256 无法在一笔交易里算完，仅内存扩张就超过 gas 上限。固定大小分块上的 Merkle 根可以每笔交易核对一块。
3. **信任求值结果的方法。** INT-C16 每生成一个 token 约需 9,800 万拍，每拍约 110 万个门（附录 A）。没有哪条链能跑完，因此没有合约能像 `TAP-draft-netlist-bound-nft` 的 Eval 扩展对小文件那样在一次调用中求值这个网表。合约能做的是裁决对一次运行的*分歧*：如果双方对 `T` 拍的输出有分歧，交互式二分把分歧缩小到某一拍中至多约 6,000 个门的一段，由合约重新求值。这需要各实现逐字节一致的承诺、分段与核对函数，所以它们在这里规定，而不是留给每个博弈各自决定。

本 TAP 作为一份独立标准补上这三样东西。它不改变 TAP-02 或 `tapnet/1` 的任何内容；除 §2.2 中只适用于网表绑定 NFT 的元数据成员外，它不依赖任何 NFT 标准；信息性的 §8 说明网表绑定 NFT 可以如何使用它。

ERC-7007 规范了可核验的 AI 生成内容，其正确性由 zkML 或 opML 证明裁定，后者设有挑战期，任何人都可在其中对结果提出争议。本 TAP 为 TapeOut 网表的运行（包括推理电路）提供同类的裁定方式：一笔交易容纳不下的运行，通过挑战、二分和单步验证器来裁定。因此，作者申请编号 7007（提案名称 TAP-7007，与 ERC-7007 相呼应）；由于 TAP-01 §6.1 原本分配 10 以上最小的空闲编号，这需要编辑同意。在分配编号之前，本草案保留文件名 `TAP-draft-large-netlists.md` 和 `tap: TBD`；最终编号由编辑分配，若与所申请的不同，本草案改用该编号。

## 规范

本文中的关键词 "MUST"、"MUST NOT"、"REQUIRED"、"SHALL"、"SHALL NOT"、"SHOULD"、"SHOULD NOT"、"RECOMMENDED"、"NOT RECOMMENDED"、"MAY" 与 "OPTIONAL"，当且仅当以全大写出现时，按 RFC 2119 与 RFC 8174 的描述解释。

### 0. 记号与范围

| 记号 | 含义 |
|---|---|
| `‖` | 字节串拼接 |
| `u8(x)`、`u16be(x)`、`u24(x)`、`u32be(x)`、`u64be(x)` | 把 `x` 写成 1、2、3、4 或 8 字节的无符号大端整数 |
| `SHA-256` | FIPS 180-4 |
| `C` | 分块大小，16,384 字节 |
| `EMPTY` | 32 个零字节 |
| `⌈x⌉`、`⌊x⌋` | 上取整与下取整 |
| `tree(leaves)` | 下文定义的、对非空叶列表的 Merkle 根：`node = SHA-256(0x01 ‖ left ‖ right)`，奇数个时最后一个节点原样上移，只有一片叶的树以该叶为根 |
| `tapnet/1` | `TAP-draft-netlist-bound-nft` 的网表文件格式（格式版本字节 `0x01`），只在信息性文字（包括 §8）中提及 |

**树与证明。** `tree(leaves)` 从叶开始逐层构建：一层中的节点 `2j` 与 `2j+1` 合并为下一层的节点 `j`，即 `SHA-256(0x01 ‖ left ‖ right)`；某层节点数为奇数时，最后一个节点原样上移到下一层。根是最后一层的唯一节点，只有一片叶的树以该叶为根。树大小为 `size` 时，第 `index` 片叶的证明是从叶层向上的兄弟节点列表，节点原样上移的那些层省略。核对证明需要根、树大小、下标、叶与证明：

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

（信息性：这就是 `TAP-draft-netlist-bound-nft` §5.3 池树的树与证明，在此重述，使本 TAP 不依赖该草案。池叶以 `0x00` 开头，本 TAP 的叶都不用它，见 §4.1。）

本 TAP 一次证明同一棵树中多片叶时，使用下面的**多重证明**格式，§6.3 编码中的见证 MUST 使用它（以其他形式接收字段的合约，见 §6.3，MAY 使用任何能让核对者仅凭给定的叶与树大小重算出根的多重证明）。对一棵有 `size` 片叶的树和一组已知的叶下标，证明从叶层向上逐层、对每个已知节点按下标递增，列出其兄弟节点的哈希，除非该兄弟本身已知或不存在（已知节点是本层奇数个时的最后一个节点，原样上移）；下一层中，子节点有任一已知的节点即为已知。单叶的多重证明恰是上面的证明。核对按同样的顺序消耗证明：

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

证明提前用尽、有剩余哈希、或某个下标不小于 `size` 时，核对者 MUST 失败。例：在 5 片叶 `h0 … h4` 的树中，叶 1、2、4 的多重证明是 `(h0, h3)`：第 0 层，叶 1 需要 `h0`，叶 2 需要 `h3`，叶 4 是奇数个时的最后一个节点，原样上移；第 1 层（宽 3），已知节点 0、1、2 什么也不需要（节点 2 再次上移）；第 2 层两个节点都已知。

本 TAP 增加：

- §1，`tapnet/2` 格式及其有效性、语义与指标；
- §2，网表哈希 `netlistHash2` 以及 `tapnet/2` 文件的 `tap.netlist` 成员；
- §3，OPTIONAL 的链上存储接口 `ITapLargeNetlistStore`，它同时充当核对函数的分块来源；
- §4–§6，承诺、规范分段、单步核对函数及其见证的编码；
- §7，针对 `tapnet/2` 求值的争议博弈 MUST 使用的数据、数据可获取性要求与规则。

§8 位于向后兼容之后、本规范之外，是信息性的：它说明网表绑定 NFT（`TAP-draft-netlist-bound-nft`）可以如何使用 `tapnet/2` 网表。§1 至 §7 不依赖 §8；除只适用于网表绑定 NFT 的 §2.2 外，它们也不依赖任何 NFT 标准。

### 1. `tapnet/2` 格式

#### 1.1 布局

| 偏移 | 长度 | 内容 |
|---|---|---|
| 0 | 6 | ASCII `TAPNET`（`0x5441504e4554`） |
| 6 | 1 | 格式版本，`0x02` |
| 7 | 1 | 标志：第 0 位是 **MEM**（文件含 RAM 端口），第 1–7 位为 0 |
| 8 | 4 | `nIn`，`u32be` |
| 12 | 4 | `nOut`，`u32be` |
| 16 | 其余 | 记录 |

记录是以下之一：

| 记录 | 操作码 | 编码 | 长度 | 新信号数 |
|---|---|---|---|---|
| NAND | `0x00` | `0x00 ‖ u24(x) ‖ u24(y)` | 7 | 1 |
| LATCH | `0x01` | `0x01 ‖ u24(d)` | 4 | 1 |
| ROM | `0x10` | `0x10 ‖ u8(a) ‖ u16be(w) ‖ u24(addr[0]) ‖ … ‖ u24(addr[a−1]) ‖ data` | `4 + 3a + ⌈2^a · w / 8⌉` | `w` |
| RAM 读口 | `0x11` | `0x11 ‖ u8(bank) ‖ u8(a) ‖ u16be(w) ‖ u24(addr[0]) ‖ … ‖ u24(addr[a−1])` | `5 + 3a` | `w` |
| RAM 写口 | `0x12` | `0x12 ‖ u8(bank) ‖ u8(a) ‖ u16be(w) ‖ u24(we) ‖ u24(addr[0]) ‖ … ‖ u24(addr[a−1]) ‖ u24(wdata[0]) ‖ … ‖ u24(wdata[w−1])` | `8 + 3a + 3w` | 0 |
| ROM 读口 | `0x13` | `0x13 ‖ u32be(romOffset) ‖ u8(a) ‖ u16be(w) ‖ u24(addr[0]) ‖ … ‖ u24(addr[a−1])` | `8 + 3a` | `w` |

NAND 与 LATCH 的编码同 TAP-02 §2。TAP-02 的 REF 记录（操作码 `0x02`）不属于 `tapnet/2`。RAM 读口、RAM 写口与 ROM 读口统称**端口**；RAM 读口与写口称为 **RAM 端口**。

**信号。** 信号 0 是常量 0，信号 1 是常量 1，信号 `2 … 2 + nIn − 1` 是输入。每条记录按文件顺序在下一个空闲编号处产生新信号：NAND 或 LATCH 产生一个，ROM、RAM 读口或 ROM 读口产生 `w` 个，RAM 写口不产生信号。`S` 表示信号总数。输出是最后 `nOut` 个信号；输出 `j` 是信号 `S − nOut + j`。LATCH 按文件顺序编号为 `0, 1, …`（**LATCH 序号**）；`nLatch` 是 LATCH 的个数，`d_l` 是 LATCH `l` 的 `d` 引用。

**ROM 数据。** `data` 是按 LSB 优先打包的位流（TAP-02 §5）：位流第 `i` 位是第 `⌊i/8⌋` 字节的第 `i mod 8` 位。第 `k` 项（`0 ≤ k < 2^a`）占位流第 `k·w … k·w + w − 1` 位，其第 `j` 位在位流第 `k·w + j` 位。地址按 LSB 优先：`addr[0]` 是最低位；端口的地址同样如此。ROM 读口没有自己的数据：它读取操作码位于文件第 `romOffset` 字节的那条 ROM 记录的数据。

**存储体。** 文件的存储器由若干**存储体**（bank）组成，按其 RAM 端口的 `bank` 字节编号（0 到 255）。存储体 `b` 有 `2^a` 项、每项 `w` 位，`a` 与 `w` 取自指向 `b` 的 RAM 端口，它们全都一致（§1.2）。存储体的**步长** `s` 是不小于 `w` 的最小 2 的幂。

#### 1.2 有效性

一个字节串是有效的 `tapnet/2` 文件，当且仅当下列各条全部成立：

1. 长度至少 16、至多 67,108,864 字节（64 MiB）；第 0–5 字节是 `TAPNET`，第 6 字节是 `0x02`，第 7 字节是 `0x00` 或 `0x01`；
2. `nIn ≤ 65,536` 且 `1 ≤ nOut ≤ 65,536`（TAP-02 §3 的界）；
3. 记录区非空，且从偏移 16 开始解析恰好在文件末尾结束。每个操作码都是 `0x00`、`0x01`、`0x10`、`0x11`、`0x12` 或 `0x13`；其他任何操作码（包括 `0x02`）都使文件无效；
4. 每个 NAND 输入、每个 ROM 地址引用和每个端口的每个引用（包括 RAM 写口的 `we` 与 `wdata` 引用）都小于该记录处下一个空闲信号的编号（只能是常量、输入和更早记录的信号）；
5. 每个 LATCH 的 `d` 小于 `S`；
6. `S ≤ 2^24`；
7. `S − 2 − nIn ≥ nOut`；
8. 每个 ROM：`1 ≤ a ≤ 20`，`1 ≤ w ≤ 256`，并且若 `2^a · w` 不是 8 的倍数，最后一个数据字节中未用的高位为 0；
9. 每个端口：`1 ≤ a ≤ 20` 且 `1 ≤ w ≤ 256`；每个 RAM 端口满足 `2^a · s ≤ 2^24`（`s` 为 §1.1 的步长），且 `bank` 相同的 RAM 端口的 `a` 与 `w` 都相同；
10. 每个 ROM 读口的 `romOffset` 是一条更早的、`a` 与 `w` 相同的 ROM 记录的操作码偏移；
11. 第 7 字节的第 0 位（MEM）置位，当且仅当文件至少含一个 RAM 端口。

不含 ROM 记录或端口的文件，只要满足这些规则，就是有效的 `tapnet/2` 文件。（信息性：`tapnet/1` 文件即版本字节 `0x01` 的文件（见 §0），不是 `tapnet/2` 文件，且 `tapnet/1` 文件永远不能含 ROM。）

工具 MUST 拒绝无效文件，且 MUST NOT 产生无效文件。工具在读取某条记录的引用或数据之前、在为它分配内存之前，MUST 先用剩余文件长度核对该记录声明的长度；特别地，ROM 的数据长度 `⌈2^a · w / 8⌉` 由其记录头算出，并在读取该记录的其他任何部分之前与文件长度比较；端口的长度（`5 + 3a`、`8 + 3a + 3w` 或 `8 + 3a`）由其 5 或 8 字节的记录头算出，同样先行比较。工具 MUST 在读取记录之前检查文件长度（第 1 条）。

有效性是整个文件的性质，在应用承诺某个文件时（例如在登记表、博弈的配置或网表绑定 NFT 集合的清单中，见 §8）以及工具对照 `netlistHash2` 核对文件时，在链下检查。§6 的单步核对函数只解析它们需要的记录，不确立有效性。

#### 1.3 语义

一拍把状态向量 `state[0 … nLatch−1]`、存储器 `mem`（每个存储体 `b` 有项 `mem_b[0 … 2^a − 1]`，每项 `w` 位）和输入向量 `inputs[0 … nIn−1]`（均为位）映射为信号值 `V[0 … S−1]`、输出、新状态与新存储器。`V[0] = 0`，`V[1] = 1`，`V[2 + i] = inputs[i]`，然后按文件顺序：

- NAND `(x, y)`：`V = 1 − (V[x] ∧ V[y])`；
- 第 `l` 个 LATCH：`V = state[l]`；
- ROM：`addr = Σ_{i<a} V[addr[i]] · 2^i`；输出 `j`（`0 ≤ j < w`）是 `data` 位流第 `addr · w + j` 位；
- ROM 读口：同 ROM，用它自己的地址和位于 `romOffset` 的 ROM 记录的 `data`；
- RAM 读口：`addr = Σ_{i<a} V[addr[i]] · 2^i`；输出 `j` 是 `mem_bank[addr]` 的第 `j` 位，即本拍开始时存储器中的该项；
- RAM 写口：不产生信号。

输出是 `V[S − nOut … S − 1]`，`newState[l] = V[d_l]`。新存储器是本拍开始时的存储器按文件顺序施加各 RAM 写口之后的结果：`V[we] = 1` 的 RAM 写口把其存储体的第 `Σ_{i<a} V[addr[i]] · 2^i` 项置为 `Σ_{j<w} V[wdata[j]] · 2^j`，`V[we] = 0` 的写口不改变任何东西；一拍中有多个写口写同一项时，文件顺序中最后一个生效。写入对下一拍生效，正如 LATCH 的 `d`：RAM 读口无论在文件中的什么位置，读到的都是本拍开始时的存储器。ROM 与 ROM 读口是组合元件，没有状态。一次运行的初始状态是全零状态，同 TAP-02 §4，初始存储器也全为零；其状态根为 `EMPTY`（§4.3）。

**规范展开（信息性）。** 每个 ROM 和每个 ROM 读口都等价于一棵以其地址位为选择信号的 NAND 多路选择树，因此每个不含 RAM 端口的 `tapnet/2` 文件都有一个只含 NAND 与 LATCH 记录的等价网表，`nIn`、`nOut`、LATCH 顺序与行为都相同，参考实现把它写成 `tapnet/1` 文件（§0）。参考实现（`expand_rom_to_tapnet1`）按顺序复制 NAND 与 LATCH 记录，并把每个 ROM 或 ROM 读口（连同它读取的数据）的每个输出位 `j` 替换为一棵多路选择树：从该列的 `2^a` 个常量位出发，逐层两两合并，`addr[0]` 先用，`mux(s, x0, x1)` 在 `x0 = x1` 时为 `x0`，否则为 `NAND(NAND(x0, NOT s), NAND(x1, s))`，其中 `NOT x = NAND(x, x)`；常量被折叠（`NAND(0, y) = 1`，`NAND(1, y) = NOT y`，`NAND(x, x) = NOT x`），同一 ROM 内相同的 NAND 与 NOT 门共享。随后每个输出位得到一个新门 `NAND(NOT v, NOT v)`，使 `w` 个输出成为连续信号；LATCH 引用重新编号；若展开后的输出不是最后 `nOut` 个信号，则为每个输出追加两个反相器。测试向量给出每个小样例展开结果的 SHA-256 和大小。本 TAP 依赖的是行为等价，而不是展开结果的确切字节；`tapnet/2` 的结构哈希是待定问题。含 RAM 端口的文件没有规范展开（其存储器可以用 LATCH、多路选择器和译码器重建，代价见设计理由）。

#### 1.4 指标

有效 `tapnet/2` 文件的指标是 `bytes`（长度）、`nIn`、`nOut`、`nand`（NAND 记录数）、`latch`（LATCH 记录数，等于 `nLatch`，也等于状态大小）、`romRecords`（ROM 记录数）以及对所有 ROM 记录求和的 `romBits = Σ 2^a · w`。含端口的文件另有指标 `ramBanks`（不同存储体的个数）、对其存储体求和的 `ramBits = Σ 2^a · w`、`ramReadPorts`、`ramWritePorts` 与 `romPorts`（RAM 读口、RAM 写口与 ROM 读口的个数）；不含端口的文件这些指标为零，工具 MAY 省略它们（参考实现即省略）。

### 2. 网表哈希与元数据

#### 2.1 分块与 `netlistHash2`

长度为 `len` 的文件切成 `n = ⌈len / C⌉` 块，`chunk_i` 是第 `i·C … min((i+1)·C, len) − 1` 字节；只有最后一块可以短于 `C`。然后

```
chunkLeaf(i)  = SHA-256(0x02 ‖ u32be(i) ‖ chunk_i)
chunkRoot     = tree(chunkLeaf(0), …, chunkLeaf(n − 1))
netlistHash2  = SHA-256(0x03 ‖ u64be(len) ‖ chunkRoot)
```

`netlistHash2` 是 `tapnet/2` 文件的网表哈希。它不是文件的 SHA-256，工具 MUST NOT 把二者相互比较。对哈希为 `h` 的文件，第 `i` 块被接受，当且仅当：对声明的 `len` 与 `chunkRoot` 有 `SHA-256(0x03 ‖ u64be(len) ‖ chunkRoot) = h`；`i < n`；`i < n − 1` 时长度为 `C`，`i = n − 1` 时长度为 `len − (n − 1)·C`；并且其 Merkle 证明在大小为 `n`、根为 `chunkRoot` 的树中于下标 `i` 处验证 `chunkLeaf(i)`。

文件是自描述的：第 6 字节是其格式版本，`netlistHash2` 是版本 `0x02` 的网表哈希。（信息性：`tapnet/1` 文件即版本 `0x01`，在 `TAP-draft-netlist-bound-nft` §2.3 中以 `SHA-256(file)` 标识。两种哈希为何永不碰撞见设计理由，这正是该草案 §2.5 对其他格式的要求（见向后兼容）。）

#### 2.2 元数据成员

网表为 `tapnet/2` 文件的网表绑定 NFT（`TAP-draft-netlist-bound-nft`）按该草案的 §2.5（其它网表格式）使用本格式；该节要求一种格式定义其有效性、指标、网表哈希规则，以及 `tap.netlist` 中替代 `sha256` 与 `bytes` 的成员。`tapnet/2` 的有效性即 §1.2，其指标即 §1.4 的指标，其网表哈希规则即 `netlistHash2`（§2.1）。在这种代币 `tap` 对象的 `netlist` 成员中：

1. `format` MUST 为字符串 `"tapnet/2"`。
2. 成员 `netlistHash2` 替代 `sha256`：它 MUST 出现，并等于该文件的 `netlistHash2`，写作 `0x` 加 64 个小写十六进制数字；`sha256` MUST NOT 出现。
3. `bytes` MUST 等于 §1.4 的指标 `bytes`，即文件长度，取代该草案 §7.3 第 2 条的大小公式。
4. `nIn`、`nOut`、`nand` 与 `latch` MUST 等于 §1.4 中同名的指标。以 §1.4 其他指标命名的成员 MAY 出现，出现时 MUST 等于该指标。

本小节只适用于这类代币；§1 至 §7 的其余部分不依赖它。§8（信息性）说明该草案的池、合约与工具将如何处理这类代币。

### 3. 链上存储（可选）

合约 MAY 存储 `tapnet/2` 文件。若这样做，它 MUST 实现 `ITapLargeNetlistStore` 并报告其 ERC-165 标识符 `0x8230af7f`。

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

1. 除非 `netlistHash2` 是该合约的已知哈希（第 7 条），`beginLarge` MUST 以 `UnknownNetlist` 回滚；除非 `16 < length ≤ 67,108,864`，以 `BadLength` 回滚；除非 `SHA-256(0x03 ‖ u64be(length) ‖ chunkRoot) = netlistHash2`，以 `HashMismatch` 回滚。否则它为 `netlistHash2` 记录 `length` 与 `chunkRoot` 并发出 `LargeBegun`。对同一 `netlistHash2` 再次成功调用不产生效果、不发事件。任何人 MAY 调用。
2. 若 `beginLarge` 尚未对 `netlistHash2` 成功，`putChunk` MUST 以 `NotBegun` 回滚；若该块按 §2.1 不被接受，以 `BadChunk` 回滚。否则它存储该块、发出 `ChunkStored`，若这是最后一个缺失的块，再发出 `LargeStored`。存储已存在的块不产生效果、不发事件。任何人 MAY 以任意块顺序调用。
3. `largeStored` 当且仅当 `netlistHash2` 的全部 `n` 块都已存储时返回 true。
4. `largeChunk` 原样返回被接受的第 `index` 块；若该块未存储（包括文件从未开始），MUST 以 `ChunkNotStored` 回滚。
5. `largeInfo` 返回：`length`，即 `beginLarge` 记录的长度（尚未成功时为 0）；`nIn` 与 `nOut`，即第 0 块第 8–11 与 12–15 字节按 `u32be` 读出的值（第 0 块未存储时为 0）；`chunks`，已存储的不同块的个数；`stored`，等于 `largeStored(netlistHash2)`。合约存储第 0 块时 MUST 记录 `nIn` 与 `nOut`。
6. 块的存储方式由实现决定（信息性：每块一个 SSTORE2 数据合约；16,384 字节在 24,576 字节代码大小上限之下留有余量）。谁为上传付费不在本 TAP 范围内；由于任何人都可以调用 `beginLarge` 与 `putChunk`，上传可以由应用、声明者或其他任何人付费。
7. 哪些网表哈希是**已知**的，由实现存储接口的合约规定，例如由所有者登记的哈希或由应用承诺的哈希。（信息性：参考存储合约把这条规则交给派生合约；网表绑定 NFT 的情形见 §8.2。）

`largeStored(netlistHash2)` 为 true 的存储合约，就是 `netlistHash2` 的分块来源（§6）。函数选择器、错误选择器与事件主题列在测试用例一节。

### 4. 承诺

#### 4.1 域分隔

本 TAP 中每个哈希都以一个域字节开头，唯一例外是输入叶内部的摘要 `SHA-256(packedInputs_t)`（§4.4），它本身从不用作叶或节点：

| 字节 | 用途 | 定义处 |
|---|---|---|
| `0x00` | 本 TAP 不使用（`TAP-draft-netlist-bound-nft` §5.3 的池叶） | |
| `0x01` | Merkle 节点（本 TAP 的每棵树） | §0 |
| `0x02` | 分块叶 | §2.1 |
| `0x03` | `netlistHash2` | §2.1 |
| `0x04` | 信号值与输出值的字叶 | §4.2、§4.3 |
| `0x05` | LATCH 状态的字叶 | §4.3 |
| `0x06` | 检查点叶与拍叶 | §7.1 |
| `0x07` | 元组叶（后跟一个种类字节） | §4.5 |
| `0x08` | 输出哈希链 | §4.4 |
| `0x09` | LATCH `d` 表的字叶 | §4.3 |
| `0x0A` | 输入叶 | §4.4 |
| `0x0B` | 存储字叶 | §4.6 |
| `0x0C` | 带 MEM 文件的状态根 | §4.6 |

`0x07` 之后的种类字节是 `0x00`（段元组）、`0x01`（输出元组）或 `0x02`（状态元组）。（信息性：草案 `TAP-draft-netlist-nft-bridging` 使用带字符串域的 keccak-256，草案 `TAP-draft-netlist-structural-hash` 使用 ASCII 前缀 `TAPSH1` 与 `tap-structural-hash/1`；本地的设计署名与不可转让凭证草案使用 EIP-712 域和以 NUL 结尾的 ASCII 前缀；它们与这些都不冲突。）

#### 4.2 字与前缀根

**位字**容纳 256 位：第 `o` 位（`0 ≤ o < 256`）是 32 字节串第 `⌊o/8⌋` 字节的第 `o mod 8` 位。**u32 字**容纳 8 个值：第 `o` 个值（`0 ≤ o < 8`）是第 `4o … 4o + 3` 字节上的 `u32be`。对位序列（或 u32 值序列）`x[0 …]`，第 `k` 字容纳 `x[k·256 + o]`（或 `x[k·8 + o]`）。

对域字节 `dom`、序列 `x` 与长度 `s`，**前缀根**为

```
prefixRoot(dom, x, s) = EMPTY                                    if s = 0
                      = tree(wordLeaf(0), …, wordLeaf(m − 1))     otherwise
wordLeaf(k) = SHA-256(dom ‖ u32be(k) ‖ word_k),   m = ⌈s / per⌉
```

其中位字的 `per` 为 256，u32 字为 8；第 `m − 1` 字只容纳 `x` 到下标 `s − 1` 为止，未用的槽为零。树随 `s` 增长，从不补齐到固定大小。

**附加证明。** 对长度为 `s` 的前缀，令 `full = ⌊s / per⌋`，`p = s mod per`。附加证明是**峰**的列表，即按 `full` 的二进制分解得到的各完整字的满子树之根（从大到小，`full` 每个为 1 的位对应一个），以及在 `p > 0` 时的**部分字**（第 `full` 字，未用槽为零）。根由 `fold(h_1, …, h_r)` 还原，其中 `h_1 … h_r` 是各峰，若有部分字则其后再接部分字的 `wordLeaf(full)`；`fold(h_r) = h_r`，`fold(h_i, …) = SHA-256(0x01 ‖ h_i ‖ fold(h_{i+1}, …))`；空列表给出 `EMPTY`。其结果等于 `prefixRoot`。接受附加证明的核对者 MUST 检查：峰的个数与 `full` 相符，部分字是否存在与 `p` 相符，部分字的每个未用槽为零，以及还原出的根等于期望的根。从被接受的附加证明出发，可以逐个追加值并重算新的前缀根，§6 就是这样计算 `P`、`D` 与拍末各根的。

#### 4.3 一拍的承诺

对信号值为 `V`、输出为 `out`、状态为 `state`、新状态为 `newState` 的一拍：

| 名称 | 值 | 字的种类 |
|---|---|---|
| `P(s)` | `prefixRoot(0x04, V, s)` | 位字 |
| `stateRoot` | `prefixRoot(0x05, state, nLatch)`；第 0 拍为 `EMPTY`；带 MEM 的文件见 §4.6 | 位字 |
| `D(l)` | `prefixRoot(0x09, (d_0, d_1, …), l)` | u32 字 |
| `outRoot` | `prefixRoot(0x04, out, nOut)` | 位字 |

`D` 只取决于文件，不取决于拍。拍后的状态根是 `prefixRoot(0x05, newState, nLatch)`，即下一拍的 `stateRoot`。对带 MEM 的文件，状态根还承诺存储器，见 §4.6 的定义；此时 `prefixRoot(0x05, state, nLatch)` 称为它的 **LATCH 根**。

**初始状态。** 作为状态根，`EMPTY` 表示任意长度的全零状态。一次运行第 0 拍的状态全为零（§1.3），其状态根对任何 `nLatch` 都是 `EMPTY`，而不是 `prefixRoot(0x05, 0^nLatch, nLatch)`。此后每一拍的状态根都是上表的前缀根，即使该拍的状态恰好全为零。没有 LATCH 的网表在每一拍都有 `stateRoot = EMPTY`，与两条规则都一致，除非它带 MEM（§4.6）。

#### 4.4 输出哈希链与输入承诺

```
io_0         = EMPTY
io_{t+1}     = SHA-256(0x08 ‖ io_t ‖ outRoot_t)
inputLeaf(t) = SHA-256(0x0A ‖ u64be(t) ‖ SHA-256(packedInputs_t))
inputRoot    = tree(inputLeaf(0), …, inputLeaf(T − 1))
```

其中 `outRoot_t` 是第 `t` 拍的输出根（拍从 0 编号），`packedInputs_t` 是第 `t` 拍的输入向量按 LSB 优先打包成的 `⌈nIn/8⌉` 字节（输入 `i` 是第 `⌊i/8⌋` 字节的第 `i mod 8` 位），未用的高位为零；`nIn = 0` 时为空串。`io_t` 承诺第 `0 … t − 1` 拍的输出；`inputRoot` 承诺一次运行全部 `T` 拍的输入（§7.1）。

#### 4.5 元组与元组叶

**段元组**是 `seg = (byteOffset, firstSignal, latchOrdinal, P, D, M)`：文件中的一个记录边界、在该边界及其后产生的第一个信号的编号、该边界之前的 LATCH 个数、该拍的 `P(firstSignal)`、`D(latchOrdinal)`，以及 `M`，即本拍开始时的存储器施加该边界之前所有记录中的 RAM 写口（§1.3）之后的存储器根（§4.6）。不带 MEM 的文件，每个元组的 `M = ZERO_MEM`，且 `M` 不被承诺：其叶只哈希其余五个字段，它们就是这种元组的**承诺字段**（带 MEM 的文件为全部六个字段，输出元组与状态元组为两个字段都是），博弈只按承诺字段比较与检查元组（§7.4）。**输出元组**是 `(ordinal, root)`，其中 `root = prefixRoot(0x04, out, ordinal)`；**状态元组**是 `(ordinal, root)`，其中 `root = prefixRoot(0x05, newState, ordinal)`。

```
segLeaf(j, seg)  = SHA-256(0x07 ‖ 0x00 ‖ u32be(j) ‖ u64be(byteOffset) ‖ u32be(firstSignal)
                           ‖ u32be(latchOrdinal) ‖ P ‖ D)            不带 MEM 的文件
                 = SHA-256(0x07 ‖ 0x00 ‖ u32be(j) ‖ u64be(byteOffset) ‖ u32be(firstSignal)
                           ‖ u32be(latchOrdinal) ‖ P ‖ D ‖ M)        带 MEM 的文件
endLeaf(kind, k, (ordinal, root)) = SHA-256(0x07 ‖ u8(kind) ‖ u32be(k) ‖ u32be(ordinal) ‖ root)
```

输出元组 `kind = 1`，状态元组 `kind = 2`。元组列表以其叶上的 `tree` 连同列表长度来承诺。

#### 4.6 存储器

带 MEM 的文件的存储器由一棵高 24 的满二叉树承诺，其叶是 256 位的**存储字**：

- 存储体 `b`（维度 `a`、`w`，步长 `s`，§1.1）的第 `k` 项占该存储体第 `j` 个存储字的第 `o … o + w − 1` 位，其中 `k·s = 256·j + o`（`0 ≤ o < 256`）；字的第 `o` 位是第 `⌊o/8⌋` 字节的第 `o mod 8` 位，同位字（§4.2）。因为 `s` 是 2 的幂，一项永不跨两个字；因为 `2^a · s ≤ 2^24`，`j < 2^16`；
- 存储体 `b` 的第 `j` 个存储字是树的第 `pos = b · 2^16 + j` 片叶；不含任何项的字，以及文件没有的存储体的字，都为零；
- 叶是 `memLeaf(word) = SHA-256(0x0B ‖ word)`，不含下标（位置由路径确定），节点是 `SHA-256(0x01 ‖ left ‖ right)`，同本 TAP 的每棵树；
- `memRoot` 是树根。全零存储器的根是常量 `ZERO_MEM = Z_24`，其中 `Z_0 = memLeaf(0^32)`，`Z_{h+1} = SHA-256(0x01 ‖ Z_h ‖ Z_h)`；它对每个文件都相同（测试向量）；
- 位置 `pos` 的**存储证明**是该字及其从叶层向上的 24 个兄弟节点；若把叶与各兄弟逐层折叠（在高度 `h` 处当且仅当 `pos` 的第 `h` 位为 0 时叶一侧为左子节点）得到根 `r`，则它对照 `r` 验证通过。

**带 MEM 文件的状态根。** 对带 MEM 的文件，每一拍 `t ≥ 1` 的状态根为

```
stateRoot_t = SHA-256(0x0C ‖ latchRoot_t ‖ memRoot_t),   latchRoot_t = prefixRoot(0x05, state_t, nLatch)
```

第 0 拍的状态根与所有文件一样是 `EMPTY`（§4.3）。这种文件的状态根 `r ≠ EMPTY` 的**打开**是满足 `SHA-256(0x0C ‖ latchRoot ‖ memRoot) = r` 的二元组 `(latchRoot, memRoot)`。第 0 拍的 LATCH 根为 `EMPTY`，存储器根为 `ZERO_MEM`。不带 MEM 的文件，状态根就是 §4.3 的 LATCH 根，存储器根在每一拍都是 `ZERO_MEM`。

### 5. 规范分段与预算

#### 5.1 预算

**预算**是三元组 `(gates, extWords, chunks)`。争议博弈合约（§7）MUST 提供

```solidity
function segmentBudget() external view returns (uint32 gates, uint32 extWords, uint32 chunks);
```

一个声明的预算是提出该声明时博弈的 `segmentBudget()` 的返回值。博弈 MUST 把这个值随声明记录下来，并在该声明的每次核对调用中使用记录的值，使它对该声明不会改变，即使 `segmentBudget()` 之后返回别的值（同时 RECOMMENDED 使用不可变值）。核对合约 MAY 也提供 `segmentBudget()` 作为其默认预算；使用这种核对合约的博弈 MAY 在自己的 `segmentBudget()` 中返回核对合约的值，但仍按声明记录（参考博弈两者都做）。`gates` 约束一次核对调用的求值工作量，`extWords` 约束它要证明的字，`chunks` 约束它从分块来源读取的块（§6）。RECOMMENDED 的默认值是 `(6000, 256, 16)`，依据 Solidity 参考核对实现的实测选定：实测最贵的调用约需 930 万执行 gas，见证取 §6.3 的上界时，只做这一次调用的交易仍低于约 1,100 万 gas，为 16,777,216 gas 的交易留出余量，用于博弈自身的工作，也容许执行 gas 至多约为参考实现 1.5 倍的核对实现；`extWords = 256` 使 §6.3 的见证上界低于常见客户端 128 KiB 的交易大小限制（附录 B）。

#### 5.2 段的成本

段从偏移为 `o`、首信号为 `fs`、LATCH 序号为 `l0` 的记录边界开始，由连续记录 `r_1 … r_k` 组成。其成本有三个分量，每个都只由文件算出，从不依赖信号值：

- **gates** `= Σ g(r)`，NAND 与 LATCH 的 `g = 1`，ROM、ROM 读口与 RAM 读口的 `g = w + a`，RAM 写口的 `g = 1 + a + w`；
- **extWords** = 对所有记录求并集后不同元素的个数，其中：对 NAND、ROM 或端口，每个满足 `2 ≤ x < fs` 的引用 `x` 贡献 `("P", ⌊x/256⌋)`；对序号为 `l` 的 LATCH，贡献 `("S", ⌊l/256⌋)`；对偏移为 `r.off` 的 RAM 端口，贡献 `("M", r.off, 0)` 到 `("M", r.off, 3)` 四个元素，不与任何其他记录共享（一次存储访问按四个字计，见设计理由）。常量（信号 0 与 1）不算读取，引用 `x ≥ fs` 是内部读取，不计数。字 `⌊(fs − 1)/256⌋` 与其他 `P` 字一样计数，尽管见证以附加证明中的部分字提供它（§6.1）；
- **chunks** `= |⋃ chunkSet(r.off, r.structEnd)| + Σ_{ROM 或 ROM 读口 r} min(|chunkSet(r.dataStart, r.dataEnd)|, 2)`，其中 `r.off` 是操作码的偏移，`r.end` 是记录之后的偏移，ROM 的 `r.dataStart = r.off + 4 + 3a`、ROM 读口的 `r.dataStart = romOffset + 4 + 3a`，`r.dataEnd = r.dataStart + ⌈2^a · w / 8⌉`，ROM 的 `r.structEnd = r.dataStart`、其他记录为 `r.end`（端口的结构字节是整条记录），`chunkSet(s, e) = {⌊s/C⌋, …, ⌊(e − 1)/C⌋}`。第二项既不在 ROM 与 ROM 读口之间去重，也不与第一项去重；它是一次 ROM 读取所需块数的上界，因为一次读取至多 33 字节。

若每个分量都不超过预算的对应分量，则称成本**在预算之内**。

#### 5.3 规范段

从 `(o, fs, l0)` 开始的**规范段**按贪心方式构造：位于 `o` 的记录总是纳入，即使它单独的成本就超过预算；然后只要纳入下一条记录后段的成本仍在预算之内，就依次纳入。段在文件末尾结束，或在第一条纳入后会超出预算的记录之前结束。因此每段至少有一条记录，而不在文件末尾结束的段是**极大的**：再加入下一条记录就会超出预算。

文件的规范分段是从 `(16, 2 + nIn, 0)` 开始、每段从上一段结束处开始、直到文件末尾的规范段序列。其边界 `(byteOffset_j, firstSignal_j, latchOrdinal_j)`（`j = 0 … m`）在每一拍都相同；`byteOffset_m` 是文件长度，`firstSignal_m = S`，`latchOrdinal_m = nLatch`。在某一拍中，**段元组**为 `seg_j = (byteOffset_j, firstSignal_j, latchOrdinal_j, P(firstSignal_j), D(latchOrdinal_j), M_j)`，`M_j` 如 §4.5；`M_m` 是拍后的存储器根。特别地

```
seg_0 = (16, 2 + nIn, 0, prefixRoot(0x04, (0, 1, inputs…), 2 + nIn), EMPTY, memRoot_t)
```

由该拍的输入与该拍的存储器根 `memRoot_t` 决定：博弈对照 `inputRoot` 取得这些输入，从双方同意的 `stateRoot_t` 的打开取得 `memRoot_t`（§4.6、§7.4；不带 MEM 的文件与第 0 拍为 `ZERO_MEM`）。

**信息性。** 一步（§6.1）从分块来源读取段内结构字节与 ROM 读取所在的块（受 `chunks` 预算约束），外加下一条记录结构字节所在的至多两块，因此只要段的第一条记录单独就在预算之内（默认预算下总是如此），至多 `chunks + 2` 块。见证不携带这些字节。参考实现在默认预算下，对测试向量中的文件实测每段见证为 48 至 1,068 字节，每步至多读取 5 块（`measurements`）；最坏情况见 §6.3。

### 6. 单步核对函数

核对函数是其参数与 `netlistHash2` 的分块来源的确定性函数，不读取任何其他状态。"失败"指函数返回 false。遇到以下情况，核对函数 MUST 失败：任何解析错误；无法取得所需的任何字节、字或证明元素；引用的信号既不是常量、也不在见证覆盖范围内、也不是本段已算出的信号；任何格式错误的见证元素。

**分块来源（chunk oracle）。** 核对函数只通过 `netlistHash2` 的分块来源读取文件：它提供 `fileLen`，并对每个 `i < ⌈fileLen / C⌉` 提供 `netlistHash2`（§2.1）为给定值的那个文件的 `chunk_i`。在链上，分块来源是 `largeStored(netlistHash2)` 为 true 的 §3 存储合约：`fileLen` 是 `largeInfo(netlistHash2).length`，`chunk_i` 是 `largeChunk(netlistHash2, i)`；每一块都已由 `putChunk` 对照 `netlistHash2` 核对过。若分块来源不能提供整个文件（对存储合约而言：`largeStored(netlistHash2)` 为 false），或提供的块长度不符合 §2.1，核对函数 MUST 失败。因此见证只含树证明，从不含文件字节。（信息性：没有存储合约的实现，可以把块与分块多重证明作为额外输入、按 §2.1 接受它们，以此实现同一个分块来源；每次核对调用的结果相同，但这种输入要付出这些块的 calldata。）

#### 6.1 `verify_segment`

```
verify_segment(netlistHash2, latchRoot, memRoot, a, b, witness, budget) → bool
```

`a` 是双方一致的段元组，`b` 是声明的下一个元组，`latchRoot` 与 `memRoot` 是该拍开始时的 LATCH 根与存储器根（§4.6），`budget` 是声明的预算（§5.1）。不带 MEM 的文件，`latchRoot` 是双方一致的状态根，`memRoot = ZERO_MEM`；带 MEM 的文件，它们是双方一致的状态根之打开的两个根，若该状态根为 `EMPTY` 则为 `EMPTY` 与 `ZERO_MEM`（§7.4）。核对函数不需要知道文件是否带 MEM。见证包含：

- `P` 在长度 `a.firstSignal` 处的附加证明（峰与可选的部分字）；
- 一组 `P` 字及其下标，以及它们在 `a.P` 之树中的多重证明；
- `D` 在长度 `a.latchOrdinal` 处的附加证明；
- `stateSize`（状态树的字数）、一组状态字及其下标，以及它们在 `latchRoot` 之树中的多重证明；
- 一个**读证明**列表和一个**写证明**列表，每项都是一个存储证明（§4.6）；不含 RAM 访问的段，两个列表都为空。

核对函数返回 true，当且仅当下列各步按顺序全部成功：

1. 分块来源提供 `netlistHash2` 的文件，且 `fileLen ≤ 67,108,864`；
2. `16 ≤ a.byteOffset < fileLen`；
3. `P` 的附加证明在长度 `fs = a.firstSignal`、根 `a.P` 下被接受，`D` 的附加证明在长度 `a.latchOrdinal`、根 `a.D` 下被接受（§4.2）；
4. 若 `P` 的附加证明含部分字，其下标 `⌊fs/256⌋` 不在 `P` 字之中；`P` 字以树大小 `⌈fs/256⌉`、叶域 `0x04` 对 `a.P` 验证。若 `latchRoot = EMPTY`，则 `stateSize` 为 0，且没有状态字、没有状态证明；否则状态字以树大小 `stateSize`、叶域 `0x05` 对 `latchRoot` 验证。空的字集合 MUST 配空证明；
5. 在 `budget` 下从 `(a.byteOffset, fs, a.latchOrdinal)` 开始的规范段（§5.3）由从分块来源读取的块确定；除非该段在 `fileLen` 处结束，这包括解析段后那条记录的结构字节；段后那条记录只用来对照预算：它的操作数值从不读取，所以缺少该记录引用的字时，核对 MUST NOT 因此失败；
6. 运行中的存储器根 `R` 从 `a.M` 开始，按顺序求值该段的记录，其中信号 `x` 的值为：`x < 2` 时为 `x` 本身；`fs ≤ x` 时为本段先前算出的值；否则为第 `⌊x/256⌋` 字的第 `x mod 256` 位，该字若是部分字的下标则取自部分字，否则取自 `P` 字：
   - NAND `(x, y)`：`1 − (value(x) ∧ value(y))`；
   - 序号为 `l` 的 LATCH：`latchRoot = EMPTY` 时为 0，否则为第 `⌊l/256⌋` 个状态字的第 `l mod 256` 位；其 `d` 追加到 `D` 累加器；
   - ROM 与 ROM 读口：`addr = Σ value(addr[i]) · 2^i`；通过分块来源读取第 `dataStart + ⌊addr·w/8⌋ … dataStart + ⌊(addr·w + w − 1)/8⌋` 字节，输出 `j` 是这段字节串（LSB 优先）的第 `(addr·w mod 8) + j` 位；ROM 读口的 `dataStart = romOffset + 4 + 3a`；
   - RAM 读口：`addr = Σ value(addr[i]) · 2^i`，`(pos, o)` 是其存储体第 `addr` 项的位置与位偏移（§4.6）。若 `memRoot = ZERO_MEM`，每个输出都是 0，不使用读证明。否则核对函数取下一个读证明，它 MUST 是对照 `memRoot` 验证通过的 `pos` 的存储证明，输出 `j` 是其字的第 `o + j` 位；
   - RAM 写口：若 `value(we) = 0`，什么也不做。否则，`addr` 与 `(pos, o)` 同读口，核对函数取下一个写证明，它 MUST 是对照 `R` 验证通过的 `pos` 的存储证明；把其字的第 `o … o + w − 1` 位替换为 `value(wdata[0]) … value(wdata[w − 1])`，并令 `R` 为由新字与同一组兄弟节点折叠出的根；
   - 每个产生的值追加到 `P` 累加器（RAM 写口不产生值）；
   - 需要读证明或写证明而已无剩余时，核对失败；最后一条记录之后若读证明或写证明有剩余，核对也失败；
7. `b` 等于该段的 `(结束偏移, 结束信号, 结束 LATCH 序号, P 根, D 根, R)`。

`b.byteOffset` 只在第 7 步比较，从不用来确定段的结束位置。由于段是规范段，提前一条记录或推迟一条记录结束的 `b` 都会失败，无论其取值如何。第 4 步的 `EMPTY` 规则只由参数 `latchRoot` 选择，从不由状态字的缺失选择：对其他任何 LATCH 根，缺少状态字的 LATCH 会使核对失败。同样，第 6 步的 `ZERO_MEM` 规则只由参数 `memRoot` 选择。不带 MEM 的文件没有 RAM 端口，所以 `R` 保持为 `a.M`，第 7 步要求 `b.M = a.M`，在诚实的列表中它是 `ZERO_MEM`。

#### 6.2 拍末核对函数

输出与新状态按每步 256 个值、对照最后一个段元组 `seg_m` 核对；`seg_m` 的 `P` 是 `P(S)`，其 `D` 是整张 `d` 表的根：

```
verify_beat_end(netlistHash2, nOut, seg_m, a, b, witness) → bool
```

`nOut` 是文件的 `nOut`，是声明的公开参数（§7.1，`largeInfo`）。`a` 是输出列表或状态列表中双方一致的元组，`b` 是声明的下一个元组。见证包含 `kind`（输出或状态）、所需的 `P` 字及其在 `seg_m.P` 之树中的多重证明、该列表在长度 `a.ordinal` 处附加证明的峰，以及在 `kind` 为状态时，所需的 `D` 字（u32 字）及其在 `seg_m.D` 之树中的多重证明。核对函数返回 true，当且仅当：

1. 分块来源提供 `netlistHash2` 的文件，且 `seg_m.byteOffset = fileLen`；
2. 令 `S = seg_m.firstSignal`，`P` 字以树大小 `⌈S/256⌉`、叶域 `0x04` 对 `seg_m.P` 验证；
3. `kind` 为输出时：见证中没有 `D` 字和 `D` 证明，且 `1 ≤ nOut ≤ S − 2`；于是 `total = nOut`，`dom = 0x04`。`kind` 为状态时：`total = seg_m.latchOrdinal`，`dom = 0x05`，且 `D` 字以树大小 `⌈total/8⌉`、叶域 `0x09` 对 `seg_m.D` 验证；
4. `a.ordinal` 是 256 的倍数且 `0 ≤ a.ordinal < total`；
5. 附加证明（只有峰，没有部分字）在长度 `a.ordinal`、域 `dom`、根 `a.root` 下被接受；
6. 令 `hi = min(a.ordinal + 256, total)`，对 `i = a.ordinal … hi − 1`，追加信号 `S − nOut + i`（输出）或信号 `d_i`（状态，`d_i` 取自第 `⌊i/8⌋` 个 `D` 字的第 `i mod 8` 槽）的值；信号的值在 `x < 2` 时为 `x`，在 `2 ≤ x < S` 时为第 `⌊x/256⌋` 个 `P` 字的第 `x mod 256` 位，`x ≥ S` 时核对失败；
7. `b = (hi, 追加后的根)`。

一拍的输出列表与状态列表都从 `(0, EMPTY)` 开始，每步 256 个值，分别以 `(nOut, outRoot)` 与 `(nLatch, 下一拍的 LATCH 根)` 结束；不带 MEM 的文件，LATCH 根就是下一拍的状态根，带 MEM 的文件，下一拍的状态根是 `SHA-256(0x0C ‖ root ‖ seg_m.M)`（§4.6），`root` 取最后一个状态元组的根。拍末核对函数不读取存储器。没有 LATCH 的网表，其状态列表只有一个元素 `((0, EMPTY))`。

**种类由调用方绑定。** 核对函数从见证中取 `kind`，并不知道 `a` 与 `b` 属于哪个列表；两个列表都从 `(0, EMPTY)` 开始，因此若不绑定，就可以拿状态步去冒充输出元组，反之亦然。所以调用方 MUST 在调用核对函数之前检查见证的种类等于 `a` 与 `b` 所属列表的种类；在争议中，这就是博弈选中的列表（§7.4），种类不符的见证按该节的规定被拒绝。

#### 6.3 见证的编码与大小

见证按以下编码交换和公布（测试向量使用此编码）。`hashes8` 与 `hashes16` 是 32 字节哈希的列表，前面以 `u8` 或 `u16be` 写出个数；`words` 是 `u16be(个数)` 之后若干项 `u32be(index) ‖ word`（32 字节），下标严格递增；`frontier` = `u8(峰数) ‖ 各峰（从大到小） ‖ flag ‖ [部分字]`，`flag = 0x00`（无部分字）或 `0x01`（其后跟 32 字节部分字）：

```
segment witness  = frontier(P) ‖ words(P) ‖ hashes16(P multiproof) ‖ frontier(D)
                   ‖ u32be(stateSize) ‖ words(state) ‖ hashes16(state multiproof) ‖ [memory]
memory           = u16be(nRead) ‖ access × nRead ‖ u16be(nWrite) ‖ access × nWrite
access           = word ‖ u32be(zeroMask) ‖ sibling × (24 − popcount(zeroMask))
beat-end witness = u8(kind: 0x01 output, 0x02 state) ‖ words(P) ‖ hashes16(P multiproof)
                   ‖ words(D) ‖ hashes16(D multiproof) ‖ hashes8(peaks)
```

`memory` 部分按核对函数使用的顺序携带 §6.1 的读证明与写证明。它当且仅当 `nRead + nWrite > 0` 时出现；不含存储证明的见证在状态多重证明之后结束，因此不含 RAM 端口的文件，其见证不变。在 `access` 中，`word` 是存储字（32 字节），`zeroMask` 的第 `h` 位置位当且仅当高度 `h` 处的兄弟节点等于 `Z_h`（§4.6），其位为 0 的兄弟节点从叶层向上依次给出；`zeroMask` 的第 24 至 31 位为 0，等于 `Z_h` 的兄弟节点 MUST 用其掩码位表示、不得写出，使每个见证只有一种编码。

此编码中的多重证明 MUST 采用 §0 的格式。接受编码见证的核对函数，在编码被截断、有多余尾部字节、flag 不是 `0x00` 或 `0x01`、kind 不是 `0x01` 或 `0x02`、或字的下标不严格递增时，或在 `memory` 部分 `nRead = nWrite = 0`、`zeroMask` 有高于第 23 位的位、或写出了等于 `Z_h` 的兄弟节点时，MUST 失败。合约 MAY 以其他形式（例如 ABI 编码的数组）接收同样的字段；核对结果只取决于这些字段。

**大小。** 每棵信号树或状态树至多有 2^16 个字（`S ≤ 2^24`；恰有 2^24 个信号的文件有 65,536 个信号字），在这样的树中 `k` 个字的多重证明至多有 `Σ_{l=1}^{15} min(k, 2^{16−l}) + 2 − k` 个哈希，`k = 256` 时为 2,048；`D` 树至多有 2^21 个字。一段证明的 `P` 字与状态字合计至多 `max(extWords, 20)` 个：此后的记录使段保持在 `extWords` 之内（§5.3），但第一条记录总是纳入，单独就可能读取至多 20 个字（`a = 20` 的 ROM、ROM 读口或 RAM 读口；NAND 至多读 2 个，LATCH 读 1 个）。因此默认预算下按此编码，不含 RAM 端口的文件，其段见证至多 84,208 字节，拍末见证至多 92,810 字节（读取 256 个分散 `P` 字的状态步）；实测最大的诚实见证为 74,896 字节（附录 B）。含 RAM 端口时，在预算之内的段，其 `k_P` 个 `P` 字、`k_S` 个状态字与 `k_M` 次存储访问满足 `k_P + k_S + 4 · k_M ≤ extWords`，每次访问至多占 `32 + 4 + 24 · 32 = 804` 字节；单独就超出预算的第一条记录，或是至多 `1 + 20 + 256 = 277` 个 `P` 字加一次访问的 RAM 写口，或是至多 20 个字加至多一次访问的记录。存储部分连同其两个计数只在 `k_M ≥ 1` 时出现，而每次访问挤掉四个 `P` 字或状态字，它们连同多重证明的哈希所占的空间多于这次访问：默认预算下带存储证明的见证至多 83,848 字节，所以 84,208 字节的上界对含 RAM 端口的文件同样成立。博弈 MUST 接受按此编码至多 98,304 字节（96 KiB）的见证，或以其他形式给出的等价字段；预算不同于默认值的博弈，MUST 至少接受由这些式子对其预算得出的上界。博弈 MAY 拒绝更长的见证。

### 7. 争议数据与规则

本节规定关于 `tapnet/2` 文件求值的争议博弈 MUST 使用的内容，使各自独立的博弈与证明者在每个值上一致，并使得只要有诚实挑战者愿意挑战，错误的声明就不能成立。经济层（谁可以提出或挑战声明、保证金数额、在 §7.1 规则之内挑战窗口与各步截止时间的长短、奖励）不在范围内；附录 D 描述了一种安排。多个挑战者的争议如何决定声明是否成立不属于经济层，由 §7.1 规定。

#### 7.1 声明与数据可获取性

**声明**是 `(netlistHash2, T, K, inputRoot, checkpointRoot)`，`1 ≤ T ≤ maxT` 且 `1 ≤ K ≤ maxK`（上界见下文）。它断言：用 `inputRoot` 承诺的输入（§4.4），从全零状态运行 `netlistHash2` 的文件 `T` 拍，得到 `checkpointRoot` 承诺的检查点叶。声明的公开参数是取自 `largeInfo(netlistHash2)`（§3）的 `fileLen`、`nIn` 与 `nOut`，MEM 标志（第 0 块第 7 字节的第 0 位，博弈在声明提出时用 `largeChunk(netlistHash2, 0)` 读取并随声明记录），预算（§5.1），以及其 `T` 拍的输入向量；输入由应用定义，并按下文的要求由 `inputRoot` 承诺。

**检查点时刻**是 `0, K, 2K, …`（所有小于 `T` 的 `K` 的倍数）并总是包括 `T`，按升序排列；`n` 是其个数。对第 `k` 个检查点时刻 `t_k`，**检查点叶**是 `L_k = checkpointLeaf(t_k, stateRoot_{t_k}, io_{t_k})`，其中

```
checkpointLeaf(t, stateRoot_t, io_t) = SHA-256(0x06 ‖ u64be(t) ‖ stateRoot_t ‖ io_t)
checkpointRoot = tree(L_0, …, L_{n−1})
```

由于 `stateRoot_0 = EMPTY`（§4.3）且 `io_0 = EMPTY`，`L_0 = checkpointLeaf(0, EMPTY, EMPTY)` 是常量。

**开示输出（信息性）。** 声明不列出它的输出；输出由检查点叶中的输出链承诺，任何人都可以用几次哈希，从某个检查点叶开示该检查点时刻之前那一拍的输出（附录 C）。开示只说明声明说了什么，不说明它是对的：应用应只采信已成立的声明（成立规则见下文）中开示的值，并应把电路设计成让它关心的结果保持在最后一拍、或某个检查点之前那一拍的输出上。

除非下列各条都成立，博弈 MUST NOT 接受声明：

1. 在博弈读取的存储合约中 `largeStored(netlistHash2)` 为 true（§3）；该存储合约就是这个声明每次核对调用的分块来源；
2. 声明者在 calldata 或博弈指定的其他媒介中公布完整列表 `L_0 … L_{n−1}`，并且博弈自己从该列表算出 `checkpointRoot`，或以其他方式把二者绑定，使 `tree(公布的列表) = checkpointRoot` 按构造成立。叶列表未公布的 `checkpointRoot` MUST NOT 被接受；
3. 列表恰好每个检查点时刻一片叶，且 `L_0` 是上述常量；
4. `inputRoot` 以下文两种方式之一取得，且第 `0 … T − 1` 拍全部的输入向量在挑战窗口开始之前可以获取；
5. `T ≤ maxT` 且 `K ≤ maxK`（上界见下文）。

**输入。** 声明针对哪些输入由应用定义（例如"玩家按下的按键"，或"先是提示 token，此后全为零"）；它们是声明的公开参数。博弈 MUST 指明 `inputRoot` 以下列两种方式中的哪一种取得：

- (a) **由博弈计算。** 全部 `T` 拍的输入向量随声明公布在链上（博弈读取的 calldata 中），或由博弈的确定性规则固定，博弈自己从中算出 `inputRoot`（§4.4），除非每个向量都是 `⌈nIn/8⌉` 字节且未用位为零，否则拒绝该声明。公布的输入与 `inputRoot` 因而按构造一致。这适用于 `T` 较小的情形；`maxT` 限制其工作量。
- (b) **由输入提供者给出。** `inputRoot` 由博弈指名的输入提供者（运营者、预言机或某个合约）给出，提供者在任何声明能使用它们之前，以 calldata、blob 或博弈指定的其他媒介公布全部 `T` 拍的输入向量。输入提供者 MUST NOT 是声明者：博弈 MUST 拒绝由提供者提出的声明，应用 MUST NOT 指名一个也对其所提供的输入提出声明的一方为提供者（控制声明者的提供者，就本规则而言即是声明者）。否则声明者可以承诺输入 `Y` 而公布 `X`，使自己的声明无法被挑战（见安全考虑）。以 calldata 接收输入的博弈 SHOULD 检查其长度为 `T · ⌈nIn/8⌉` 字节。

两种情形下，输入向量都 MUST 在挑战窗口开始之前可以获取。在情形 (b) 中，链上没有任何东西检查公布的输入与 `inputRoot` 是否一致，本 TAP 也不提供输入不一致的证明（见待定问题）。因此挑战者在计算这次运行之前先在链下检查这种一致性。发现公布的输入与 `inputRoot` 不一致的挑战者不需要、也不应该在拍层面挑战：拍层面的争议只决定在 `inputRoot` 承诺的输入上的运行是否正确，而在第 3 层声明者公布的正是承诺的输入。输入不一致是输入提供者的数据可获取性失败，由应用按自己的规则处理。

**规范输入（信息性）。** `inputRoot` 为每一拍承诺一个输入向量，包括电路忽略其输入的那些拍（例如只在执行输入指令时才读按键的 CPU）。这些拍上取不同的值，会使同一次有意义的运行得到不同的 `inputRoot`，于是两个输入提供者、或提供者与核查者，可能在所有要紧之处都一致却对 `inputRoot` 有分歧。因此应用应为电路忽略的输入规定规范值（例如零），输入提供者应使用这些值。

`K` SHOULD 选得使列表保持较小；RECOMMENDED 至多 2,048 片叶（叶哈希共 65,536 字节，一笔交易即可放下，低于常见客户端交易池 128 KiB 的上限）。接受更长列表的博弈 MUST 规定如何跨多笔交易累加计算根。信息性：在链上从 2,048 片叶算出 `checkpointRoot`，约需调用 2,047 次 SHA-256 预编译合约；对约 9.8 × 10^7 拍的 INT-C16 规模运行，相当于 K 约为 48,000 拍。

**`T` 与 `K` 的上界。** `T` 与 `K` 由声明者选择，不设上界会让声明者提出无人能核查或争辩的声明：极大的 `K` 迫使每一方在一步的截止时间内重算至多 `K` 拍并构造 `K + 1` 片叶的拍树（§7.3），极大的 `T` 要求一次无人能在挑战窗口内重算的运行。因此博弈 MUST 规定上界 `maxK` 与 `maxT`，并 MUST NOT 接受 `K > maxK` 或 `T > maxT` 的声明。`maxK` SHOULD 小到诚实方能在第 2 层承诺这一步的截止时间（见下文）内重算 `maxK` 拍并构造 `maxK + 1` 片拍叶的树，`maxT` 小到诚实挑战者能在挑战窗口内重算 `maxT` 拍。检查点叶至多 `maxLeaves` 片时，`T ≤ maxK · (maxLeaves − 1)` 已经成立，所以 `maxT` 不必超过这个值。应用 MAY 直接固定 `T` 与 `K`。

**挑战者与声明成立。** 声明有一个挑战窗口，在声明提出时开始。每一次挑战开启声明者与该挑战者之间独立的一场争议，以 `(声明, 挑战者)` 为键，只按 §7.2 至 §7.4 判定；任何一场争议的赢家都不取决于另一场争议。博弈 MUST 遵守以下规则：

1. 在窗口届满之前持续接受新的挑战者，无论该声明的其他争议结果如何，除非声明已被否决；窗口内的挑战永远不会因为该声明的另一场争议未决或已结束而被拒绝。博弈 MAY 在该声明的其他争议未决时开启新的争议（参考博弈如此）；
2. 声明者输掉任何一场争议，声明即被否决；被否决的声明不再接受挑战，永不成立，关于它的仍未决的争议无赢家地结束；
3. 声明者赢得一场争议，并不因此使声明成立；
4. 声明只在其挑战窗口届满、且不存在关于它的未决争议或挑战者胜的争议时才成立。

单槽博弈（第一个挑战就关闭窗口）不符合本节：声明者可以用第二个地址挑战自己的错误声明，故意输掉或拖延，把所有诚实挑战者挡在门外。使用保证金的博弈 SHOULD 按争议逐场结算，使任何一场争议的结果都不依赖另一场：声明者随声明交一份保证金，每个挑战者随挑战交一份；声明者赢得一场争议时，得到该挑战者的保证金；第一个赢得争议的挑战者得到自己的与声明者的保证金，声明被否决；无赢家地结束的争议，其挑战者取回保证金；声明成立时，声明者的保证金退还。

**行动与超时。** 争议是一串行动，任何时刻都恰好有其双方中的一方该行动，顺序如下：挑战者走第 1 层的一步（§7.2）；此后在 §7.3 与 §7.4 的每一轮中，声明者先走，挑战者后走；只由一方走的两步，即公布第 `h + 1` 个元组与最终调用（§7.4），由该节指定的一方走。博弈 MUST 为每一步设定截止时间，并 MUST 在截止时间过去而该行动的一方没有有效行动时判该方输。由于一场争议任何时刻只有一方该行动，双方不可能同时超时。截止时间与挑战窗口 SHOULD 按时间（区块时间戳）而不是区块数计算，因为一步的链下工作量不随链的出块间隔缩短；第 2 层承诺这一步（§7.3）的截止时间 SHOULD 随 `K` 增长，取 `base + perBeat · K`，其中 `base` 覆盖任意一步，`perBeat` 覆盖重算一拍及其叶。在这些规则之内，长短留给博弈。在一轮之内，后走的一方能看到先走一方的行动，但这不给它任何好处：一方公布的每个值都只对照它自己的承诺、它自己更早的叶以及公开数据核对，从不对照对方在同一轮的行动。顺序只在第 3 层要紧：挑战者的公布检查第 1 条用到公开的 `seg_0`，而它由声明者公布的输入得出。

**失败的行动。** 行动失败分两种，博弈 MUST 按如下方式处理。

- **该方立即输**：若它没能按要求打开一个承诺，或没能通过 §7.2 至 §7.4 的某项检查。没能打开承诺是指：它公布的值的 Merkle 证明对照该值所属的承诺（它更早或在同一步承诺的根，或在第 1 层时公布的检查点列表）不通过，公布的叶所带时刻不是被问的时刻，或它指定的下标在承诺的列表之外。检查是：第 1 层的下标范围、`L_{i−1}` 与 `L_i` 的证明、公布的叶等于 `L_i`（§7.2）；第 2 层拍树的端点（§7.3）；第 3 层声明者的输入、带 MEM 文件的 `stateRoot_t` 之打开、公布检查第 1 至 5 条连同首尾元组的证明，以及返回 false 的最终调用（§7.4）。
- **该行动被拒绝**（不产生任何效果，该方仍然该行动，直到截止时间）：若它格式错误、不在自己的回合或已过截止时间，或它重新提供博弈已持有其哈希的值，而这些值的哈希不符：被争议拍 `t` 处一致拍叶与该方自己在 `t + 1` 处拍叶的原像，以及传给最终调用的元组（§7.4）。见证种类与选中列表的种类不同的最终调用也被拒绝（§6.2、§7.4）。正确计算的一方总能提供这些值，因为可接受的值只有一个，博弈已持有其哈希；提供不了的一方因超时而输。（在合约中，被拒绝的行动即 revert。）

本 TAP 无法在链上强制文件的有效性（§1.2）。声明者 MUST 在提出声明之前于链下检查文件有效，博弈 MUST 只为应用事先承诺过（例如在博弈的配置、登记表或网表绑定 NFT 集合的清单中，见 §8）、且其文件已在链下检查过的网表启动：对无效文件，任何挑战者都能击败任何声明者（见安全考虑）。

#### 7.2 第 1 层：检查点

1. 挑战者把自己的检查点叶与公布的列表比较，指定一个下标 `i`（`1 ≤ i < n`）：自己的叶与公布的叶第一次不同的下标。它公布自己在时刻 `t_i` 的值 `(stateRoot, io)`。指定 `i` 即表示挑战者接受 `L_{i−1}`。
2. 在同一步中，挑战者以原像 `(stateRoot_{t_{i−1}}, io_{t_{i−1}})` 的形式提供 `L_{i−1}`（它知道这个原像，因为 `L_{i−1}` 等于它自己的叶），并提供叶 `L_i`，以及二者对照 `checkpointRoot` 的 Merkle 证明（任何人都能从公布的列表构造这些证明）。博弈自己计算 `L_{i−1} = checkpointLeaf(t_{i−1}, …)`，因此该叶带着正确的时刻。若下标越界、任一证明不通过（参考博弈中为 `BadCheckpointProof`），或挑战者所公布值的 `checkpointLeaf(t_i, stateRoot, io)` 等于 `L_i`，挑战者输。否则争议区间为 `[t_{i−1}, t_i]`：双方在 `t_{i−1}` 处都认 `L_{i−1}`；在 `t_i` 处，声明者坚持 `L_i`，挑战者坚持自己公布的叶。

#### 7.3 第 2 层：拍

令 `t_lo = t_{i−1}`，`t_hi = t_i`。双方（声明者先）承诺 `t = t_lo … t_hi` 上的 `tree(checkpointLeaf(t, stateRoot_t, io_t))`（拍叶，与检查点使用同一叶函数）。这棵树的大小是 `t_hi − t_lo + 1`，由博弈根据检查点时刻确定，而不由双方声明。每方在承诺时证明自己的两个端点；`t_lo` 处的拍叶不等于 `L_{i−1}`、或 `t_hi` 处的拍叶不等于自己在 `t_i` 处的叶（声明者为 `L_i`，挑战者为其公布的叶）的一方，输。二分：只要区间 `[lo, hi]` 满足 `hi − lo > 1`，就在一轮中由声明者、然后由挑战者公布自己在严格位于 `lo` 与 `hi` 之间、由博弈而不是由某一方选定的一拍上的叶（RECOMMENDED 取向下取整的中点 `⌊(lo + hi)/2⌋`；参考博弈即如此），形式为原像 `(stateRoot, io)` 加对照自己拍树根的证明，时刻由博弈填入叶中；若两片叶相同，该拍成为 `lo`，否则成为 `hi`。整个过程中，`lo` 处的叶一致，`hi` 处的叶不同。二分止于某拍 `t`：其叶一致，而 `t + 1` 处的叶不同。`t` 处一致的叶给出双方一致的 `stateRoot_t` 与 `io_t`（`stateRoot_0 = EMPTY`）。

#### 7.4 第 3 层：一拍

第 3 层从一轮承诺开始，声明者先走。声明者这一步以 `packedInputs_t` 开头，并附 `inputLeaf(t)` 在大小为 `T` 的树中下标 `t` 处对 `inputRoot` 的 Merkle 证明。若它不公布（因超时）、证明不通过、或 `packedInputs_t` 不是未用位为零的 `⌈nIn/8⌉` 字节，它输。对带 MEM 的文件且双方一致的 `stateRoot_t ≠ EMPTY`，声明者这一步还公布 `stateRoot_t` 的打开 `(latchRoot_t, memRoot_t)`（§4.6），若 `SHA-256(0x0C ‖ latchRoot_t ‖ memRoot_t) ≠ stateRoot_t` 则声明者输。否则 `latchRoot_t = stateRoot_t`，`memRoot_t = ZERO_MEM`（对带 MEM 的文件，这只发生在第 0 拍，此时二者为 `EMPTY` 与 `ZERO_MEM`）。博弈记录这两个根供最终调用使用。公布的输入与 `memRoot_t` 给出第 `t` 拍的公开 `seg_0`（§5.3）。

在自己的承诺这一步中，每方为第 `t` 拍承诺三个元组列表及其长度：段元组（`segLeaf`）、输出元组（`endLeaf` 种类 1）与状态元组（`endLeaf` 种类 2）。在同一步中，它公布每个列表的首尾元组及其对照这些列表的证明，并重新提供两片拍叶的原像（博弈从 §7.3 起持有它们的哈希）：`t` 处一致叶的 `(stateRoot_t, io_t)`，以及它自己在 `t + 1` 处叶的 `(stateRoot_{t+1}, io_{t+1})`。博弈对照叶哈希检查每个原像；不符则该行动被拒绝（§7.1，失败的行动）。前者给出双方一致的 `stateRoot_t` 与 `io_t`，供第 4 条检查与最终调用使用；后者给出该方的 `stateRoot_{t+1}` 与 `io_{t+1}`，供第 4、5 条检查使用。除非下列各条全部成立，该方不经任何核对调用即输：

1. 其 `seg_0` 等于第 `t` 拍的公开 `seg_0`（§5.3）；
2. 其最后一个段元组满足 `byteOffset = fileLen`；
3. 其第一个输出元组与第一个状态元组都是 `(0, EMPTY)`；
4. 其最后一个输出元组满足 `ordinal = nOut`，且其 `t + 1` 处的拍叶满足 `io_{t+1} = SHA-256(0x08 ‖ io_t ‖ root)`，其中 `io_t` 为双方一致的值，`root` 为该元组的根；
5. 其最后一个状态元组的 `ordinal` 等于其最后一个段元组的 `latchOrdinal`，且其 `t + 1` 处拍叶的 `stateRoot_{t+1}`，对不带 MEM 的文件等于该元组的 `root`，对带 MEM 的文件等于 `SHA-256(0x0C ‖ root ‖ M)`，其中 `root` 为该元组的根，`M` 取自其最后一个段元组。

若双方都通过，博弈按段、输出、状态的顺序选出第一个末元组不同的列表。元组按其承诺字段（§4.5）比较：不带 MEM 的文件的段元组为 `(byteOffset, firstSignal, latchOrdinal, P, D)`，带 MEM 的文件为 `(byteOffset, firstSignal, latchOrdinal, P, D, M)`，输出元组与状态元组为 `(ordinal, root)`；等价地，按它们在同一个公共下标处的叶比较（参考博弈用下标 0）。末元组不按各自的叶比较：叶包含下标，而长度不同的两个列表的末元组位于不同下标。未被承诺的字段 MUST NOT 参与比较：不带 MEM 的文件，元组的 `M` 不受任何叶约束，一方可以给自己的末元组任意的 `M`，使段列表显得不同，尽管它的每片叶都一致。这样的列表必然存在，因为末输出元组与末状态元组都相同、末段元组也相同（对带 MEM 的文件，其 `M` 进入第 5 条检查）时，`t + 1` 处的叶就会相同（第 4、5 条检查）。选中输出或状态列表时，双方的末段元组在承诺字段上一致，这个共同的元组就是双方一致的 `seg_m`（不带 MEM 的文件，其 `M` 不被 `verify_beat_end` 使用）。

**在选中列表中二分。** 令 `n_A` 与 `n_B` 为两列表的最后下标，`h = min(n_A, n_B)`。在一轮中，声明者、然后挑战者公布自己在下标 `h` 处的元组，并附对照自己列表的证明。在二分中，同一下标处的两个元组相同，是指它们的叶相同，即承诺字段相同。

- 若 `h` 处元组相同，则两列表长度不同：长度相同时 `h` 是两个列表的最后下标，而它们的末元组在某个承诺字段上不同（见上文的选取），所以博弈 MAY 把长度相同而 `h` 处元组相同的情形视为不可达（参考博弈回滚）。列表较长的一方 MUST 证明 `h → h + 1` 这一步。它再走一步，公布自己的第 `h + 1` 个元组及对照其列表的证明，然后进行最终调用，以双方一致的第 `h` 个元组为 `a`、以自己的第 `h + 1` 个元组为 `b`。由于 `h` 处一致的元组是末元组（偏移为 `fileLen`，或序号为 `total`），这一步总会失败。
- 否则，从 `lo = 0`（公布检查之后下标 0 一致）与 `hi = h` 开始，按 §7.3 进行二分轮（声明者先公布，下标由博弈选定，RECOMMENDED 取 `⌊(lo + hi)/2⌋`），止于下标 `j`：`j − 1` 处元组相同，`j` 处不同。声明者的第 `j` 个元组从双方一致的第 `j − 1` 个元组出发核对，由声明者进行最终调用。

**最终调用。** 到达这一步的争议由恰好一次核对调用决定：段列表调用 `verify_segment(netlistHash2, latchRoot_t, memRoot_t, seg_{j−1}, seg_j, witness, budget)`（两个根为第 3 层开始时记录的值），输出或状态列表调用 `verify_beat_end(netlistHash2, nOut, seg_m, a, b, witness)`，二者都使用该声明的分块来源。被核对元组的所属方进行这次调用并提供见证（§6.3）；核对函数返回 true 则该方赢，否则输。它随见证重新提供一致的元组、自己被核对的元组，以及（对输出或状态列表）`seg_m`；博弈对照自己持有的叶检查它们。不带 MEM 的文件，这些元组的 `M` 不被承诺，证明方传入 `ZERO_MEM`（不带 MEM 的文件没有 RAM 写口，`verify_segment` 只要求两个元组的 `M` 相同）。对输出或状态列表，见证种类（§6.3）MUST 等于选中列表的种类（§6.2）。两种不符都使这次调用被拒绝，不调用核对函数（§7.1，失败的行动）：该方仍可在截止时间之前做出正确的调用。其余每场争议都更早地在某项公布检查处结束，不经核对调用。

**长度。** 到达核对函数的争议至多用 `⌈log2 K⌉ + ⌈log2 h⌉ + 5` 轮，在 `h` 处元组相同时用 `⌈log2 K⌉ + 6` 轮；附录 D 给出计数，并说明博弈必须计入的成本。

### 8. 与网表绑定 NFT 配合使用（信息性）

本节是信息性的。它说明遵循 `TAP-draft-netlist-bound-nft`（本节中称为**主标准**）的代币与合约可以如何把代币绑定到 `tapnet/2` 网表；§1 至 §7 不依赖本节。本节依赖主标准 §2.5（其它网表格式），它属于对主标准的一项尚未合并的修订提案（§8.3，见向后兼容）；本节本身不提出任何要求：主标准 §2.5 要求格式定义的成员由 §2.2 规定，本节只是复述。主标准包含 §2.5 之后，本 TAP 的后续修订可以把本节其余部分改为要求。

#### 8.1 元数据与池

网表为 `tapnet/2` 文件的代币，其 `tap` 对象（主标准 §7.1）的 `netlist` 成员将包含下列成员，其中 `format`、`netlistHash2`、`bytes` 与各指标成员遵循 §2.2：

| 成员 | 类型 | 内容 |
|---|---|---|
| `format` | 字符串 | `"tapnet/2"` |
| `netlistHash2` | 字符串 | §2.1，`0x` 加 64 个小写十六进制数字 |
| `bytes`、`nIn`、`nOut`、`nand`、`latch`、`romBits`、`romRecords` | 数字 | §1.4 的指标 |
| `ramBanks`、`ramBits`、`ramReadPorts`、`ramWritePorts`、`romPorts` | 数字 | 含端口文件的 §1.4 指标（否则可选） |
| `uris` | 数组 | 文件的非空 URI 字符串数组 |

按 §2.2 的要求，它不包含 `sha256`：由 `netlistHash2` 取代；`bytes` 是 §1.4 的文件长度，而不是主标准 §7.3 第 2 条的 `tapnet/1` 公式。只有当 `TAP-draft-netlist-structural-hash` 为该文件定义了结构哈希时，它才包含 `structuralHash`（主标准 §7.1）；该草案定义的结构哈希只涵盖 `tapnet/1` 文件。池条目（主标准 §5.2）将以 `netlistHash2` 作为网表值。核对这类代币的工具（主标准 §7.4）将取得文件，检查它是有效的 `tapnet/2` 文件，由它算出 `netlistHash2` 并与 `tap.netlist.netlistHash2` 及池条目比较，并比较各项指标。主标准 §2.5 要求不支持该格式的工具把该代币的网表显示为不可核验，而不是无效。

#### 8.2 与主标准接口的关系

以下将是主标准加上 §2.5（§8.3）后的推论，不是对主标准的新要求：

- L0（主标准 §8.1）将不变：元数据携带哈希与 URI，文件大小不起作用。
- 在 L1（主标准 §8.2），`netlistHashOf` 对 `tapnet/2` 代币将返回 `netlistHash2`。`tapnet/2` 网表哈希将按与任何网表哈希相同的规则成为**已知**（主标准 §8.2 第 4 条）。集合合约或伴随合约（主标准 §8.2–§8.3）可以按 §3 存储 `tapnet/2` 文件；此时 §3 第 7 条的已知哈希将是其已知网表哈希。
- `storeNetlist` 将永远不接受 `tapnet/2` 文件：主标准 §2.5 与 §8.2 第 4 条把它限定为 SHA-256 为已知网表哈希（且长度至多 49,152 字节）的 `tapnet/1` 文件；铸造即绑定合约（主标准 §8.2）还可以接受任何有效的 `tapnet/1` 文件，而 `tapnet/2` 文件不是；并且 `tapnet/2` 文件的 SHA-256 不是它的网表哈希。因此 `netlistFile(netlistHash2)` 与 `netlistOf` 将不返回 `tapnet/2` 文件；这类文件在链上的存储与读取方式见 §3。
- 由于文件从不通过 `storeNetlist` 存储，Eval 扩展的函数对 `tapnet/2` 代币将会回滚（主标准 §8.4 第 3 条），合约也不会通过 `ITapNetlistEval` 求值 `tapnet/2` 文件。大网表（任何 `tapnet/2` 文件，不论大小）将不实现 Eval 扩展（`evalOf`、`stepOf`、`evalFile`、`stepFile`）。
- 关于集合中 `tapnet/2` 网表的争议博弈（§7），将只为由该集合的清单承诺（主标准 §5）、且其文件已在链下检查过的网表启动，这正是 §7.1 对每个博弈的要求。铸造即绑定集合是否也应有资格，见待定问题 9。

#### 8.3 主标准中的格式钩子（§2.5）

没有 §2.5 时，主标准在若干处假定每个网表值都是有效 `tapnet/1` 文件的 `SHA-256(file)`：§2.2（有效性）、§2.3（哈希规则）、§2.4（指标）、§5.2（池中每个网表哈希必须属于有效的 `tapnet/1` 文件）、§7.1（`format` 为 `"tapnet/1"`，`sha256` 必需）、§7.3 第 2、3 条（`tap.netlist.bytes` 符合 `tapnet/1` 的大小公式，`tap.netlist.sha256` 等于池条目）、§7.4（工具用 `sha256` 核对 SHA-256）以及 §8.2 第 4 条（已知网表哈希与 `storeNetlist`）。结构哈希扩展与跨链扩展也是按 `tapnet/1` 写的。主标准 §2.5 就是本节所依赖的那一个钩子：对按它定义的格式的网表，池条目、`netlistHashOf` 与已知网表哈希存放该格式的网表哈希；上面列出的、涉及 `tapnet/1`、`SHA-256(file)`、`sha256` 或 `bytes` 公式的要求，以该格式的有效性、指标、哈希与成员替换后适用；`storeNetlist`、`netlistFile`、`netlistOf`、Eval 扩展以及为 `tapnet/1` 编写的 TAP，除非另有规定，只适用于 `tapnet/1` 文件。§2.5 属于对主标准的一项尚未合并的修订提案（见向后兼容）；本 TAP 不对主标准作任何改动。

## 设计理由

**一份独立标准，一个新的格式版本。** 凡是合约需要标识、存储或核对电路的地方（推理、游戏、处理器设计、NFT）都需要大网表，所以本 TAP 只依赖 TAP-02，网表绑定 NFT 可以按信息性的 §8 所述使用它。`tapnet/1` 及其网表哈希是每个网表绑定 NFT 的字节级身份，不能改变。新的版本字节避免 `tapnet/1` 的工具把 ROM 误读为未知操作码，使文件自描述，并让哈希规则跟随格式。把 REF 排除在 `tapnet/2` 之外，避免引入意义取决于处理器的电路引用。

**ROM 操作码 `0x10`。** 低位操作码是 TAP-02 最可能新增记录的地方；`0x10` 留出余地且易于识别。与 TAP-02 的协调是待定问题。

**ROM 的上限。** `a ≤ 20` 与 `w ≤ 256` 使单个 ROM 至多 2^20 × 256 位 = 32 MiB，在 64 MiB 文件上限之内；`w ≤ 256` 还使一次读取至多 33 字节、单个 ROM 的门成本至多 276，因此单个 ROM 总能放进默认预算下的一段。填充位为零使文件保持规范：同一内容，同一字节串，同一哈希。

**ROM 的门成本 `w + a`。** 核对者按地址位（读取并组合一个值）和输出位（追加到 `P` 累加器）付出代价，而不是按存储的位；数据本身从存储合约读取，由 `chunks` 分量约束。

**为什么不用文件的 SHA-256。** 在 BNB Smart Chain 上一笔交易有 16,777,216 gas，在内存中对 7.5 MB 求哈希要超出许多倍。分块上的 Merkle 根可以一次核对一块，同一棵树还让核对函数能随机访问所需字节。长度进入 `netlistHash2`，从而固定块数和最后一块的长度。叶带有自身下标和域字节，因此块不能挪位，叶也不会与节点混淆。`netlistHash2` 的原像是以 `0x03` 开头的 41 字节，而每个 `tapnet/1` 文件以 `TAPNET` 开头；因此除非 SHA-256 碰撞，`netlistHash2` 永远不会等于某个有效 `tapnet/1` 文件的网表哈希，同一池字段可以容纳两种值。

**为什么 `C = 16,384`。** 一块能放进一个数据合约，在 24,576 字节代码大小上限之下留有余量，核对者用一次代码复制即可读出。由于见证不携带块，块大小不再影响见证大小；更小的块会增加上传交易数和分块树的深度，并减少每次读取复制的字节数。

**随长度增长的前缀根。** 信号只追加，一段写入的是一个连续区间。基于前缀各字的树让核对者无需知道 `S`，只凭几个哈希组成的附加证明就能接着计算根。状态使用同一规则，意味着状态列表末尾的根无需转换就是下一拍的 `stateRoot`（对带 MEM 的文件，再用一次哈希把它与 `seg_m.M` 组合，§4.6），输出列表末尾的根就是 `outRoot`。空前缀取 `EMPTY` 使每个列表有固定的首元组。

**段身份：五个字段，以及存储器根。** 二分按下标比较元组，所以第 `j` 个元组对双方必须意味着同一个边界；规范分段保证了这一点。需要字节偏移，是因为 ROM 记录变长，仅凭信号编号无法定位记录。LATCH 序号定位每个 LATCH 的状态位。`D` 承诺边界之前各 LATCH 的 `d` 引用；`verify_segment` 每经过一个 LATCH 就扩展它，所以双方一致的 `seg_m.D` 是对整张 `d` 表的已证明承诺，状态核对函数只需几个字就能读出 256 个 LATCH 的 `d_i`，而不必扫描文件。`M` 对存储器起同样的作用：由于 RAM 写口的输入都在它之前算出（§1.2），含该写口的段就能施加这次写入，所以 `M` 只在有写口处改变，双方一致的 `seg_m.M` 就是拍后已证明的存储器根。不带 MEM 的文件，每个元组都携带 `ZERO_MEM`，并保留五字段的叶。

**规范的、贪心的、与取值无关的分段。** 如果由声明者选择边界，双方的列表就无法对齐，声明者还可以选出任何核对者都负担不起的边界。贪心规则是核对者能在本地检查的最简单规则：它重算该段，只需下一条记录即可确认极大性。只由文件计算成本，使边界在每一拍都相同、只需计算一次，并使核对者的工作量与作弊者提供的值无关。ROM 块数项出于同样理由采用静态上界：ROM 读取触及哪一块取决于地址。第一条记录总是纳入，使分段总能前进，即使预算小于一条记录。

**预算的三个维度。** 门数约束核对者的计算量；外部字约束它核对的 Merkle 证明，从而约束见证大小（§6.3）；块数约束它从存储合约读取的块。只用其中任何一个，精心构造的文件都能超出一笔交易：例如每个 NAND 都读取一个不同旧字的文件，或数据跨越许多块的 ROM。扇入分散的电路会得到更短的段，代价是多几轮二分，而不是可靠性。

**常量不算读取。** 信号 0 与 1 的值固定；计入它们会惩罚使用常量的文件，却换不来任何东西。

**`stateRoot` 作为参数，以及不受约束的状态树大小。** 状态根来自双方一致的拍叶而不是某个元组，所以它是核对函数的参数（对带 MEM 的文件，以其打开的两个根作为参数，见下文）。见证提供状态树的大小，且没有任何东西约束它。这是可靠的。从根往下，把核对者的重算与真实树比较：重算出的根等于真实的根；凡是重算出的哈希等于某个真实哈希之处，除非 SHA-256 碰撞，二者的原像相等。节点原像以 `0x01` 开头，叶原像以其叶域（这里是 `0x05`）开头，所以重算出的节点只能等于真实的节点，此时它的两个子节点等于真实节点的两个子节点；重算出的叶只能等于真实的叶。沿每片声明的叶从根往下的路径，每一步都是这样的相等，所以每片声明的叶都等于一片真实的叶，其原像带有声明的下标和真实的字。声明的大小错误时，节点可能在别的层原样上移；这只改变重算出的哈希与哪个真实哈希比较，不改变论证。同一论证适用于本 TAP 的每棵树。

**以 `EMPTY` 作为初始状态。** 博弈算不出 `nLatch` 个零位的根：`nLatch` 不在文件头里，而构造至多 65,536 个零字的树超出一笔交易的承受能力。若由声明者提供初始根，它可以选一个只有自己知道原像的根：挑战者算不出这次运行，只能在每一步都表示不同意，争议止于第 0 拍，而在那里持有原像的声明者会赢。规定 `stateRoot_0 = EMPTY` 使 `L_0` 成为常量，并使第 0 拍无需状态字即可核对。这不开启新的攻击：对 `nLatch > 0` 且 `t ≥ 1`，正确的 `stateRoot_t` 是树根，即 SHA-256 的输出，只有找到 32 个零字节的原像才会等于 `EMPTY`。在某拍之后声明 `stateRoot_{t+1} = EMPTY` 的一方，必须以 `(nLatch, EMPTY)` 结束其状态列表，而 `verify_beat_end` 在这一步算出的是真实的根，所以该方会输（有测试向量覆盖）。`EMPTY` 只由参数 `stateRoot` 选择，而它来自双方都同意的叶；诚实方只会在 `t = 0` 或 `nLatch = 0` 时同意 `EMPTY`，这两种情况下每个 LATCH 确实读出 0。第一个新状态照常计算，所以第 0 拍之后的状态根是真实的根。对带 MEM 的文件，每一拍 `t ≥ 1` 的状态根都是 `0x0C` 哈希，永远不是 `EMPTY`；第 0 拍博弈传入 `(EMPTY, ZERO_MEM)`，而全零存储器的根 `ZERO_MEM` 使每次 RAM 读取都返回 0，这正是第 0 拍的存储器。

**公布的检查点列表。** 若声明只承诺 `checkpointRoot`，声明者可以只伪造一个中间检查点而让最后一个保持正确；只看得到根的挑战者只能猜伪造的叶在哪里，公布的列表消除了这种猜测。列表公开后，挑战者自己指定第一个不同的下标，所以第 1 层无需二分、也无需声明者行动，而且每片叶都可被挑战，不只是最后一片。博弈从列表推出 `checkpointRoot`，使挑战者根据列表所作的选择总与核对时所对照的根一致。

**承诺的输入。** 对约 10^8 拍，输入无法在链上存储或哈希，而只有声明者知道的输入会使其声明无法被挑战。`inputRoot` 用一个值承诺全部输入；数据可获取性使每个挑战者都能计算这次运行；博弈只需要一拍的输入，在核对 `seg_0` 之前对照 `inputRoot` 公布。`T` 较小时，博弈可以从链上公布的输入算出 `inputRoot`，从而排除任何不一致（§7.1 情形 a）。否则就得有人给出 `inputRoot`，由博弈指名是谁（情形 b）。提供者不能是声明者：承诺输入 `Y` 而公布 `X` 的声明者，可以在 `Y` 上正确计算自己的运行，于是每个在 `X` 上计算的挑战者都会在第 3 层输掉，唯一安全的做法是不挑战，声明就在无人知道的输入上成立。第三方提供者仍可能不一致，但它不会因此得到一个声明，挑战者也能在挑战之前发现这种不一致。证明公布的输入与 `inputRoot` 不符，需要一场针对输入数据本身的争议，有其自己的承诺与可获取性规则。本版本不包含它，拍层面的争议只针对在承诺的输入上的运行，这样的证明列为待定问题。

**从存储合约读块。** 把一步所需的块放进见证时，对一个 ROM 读取跨块边界的有效文件，诚实见证可达 136 KB，超过 geth 系内存池 128 KiB 的交易大小上限，诚实的证明者可能因提交不了见证而输。核对函数现在读取存储合约已持有的块，这些块已由 `putChunk` 与 `netlistHash2` 绑定；见证只保留树证明，大小受预算约束（§6.3）。`chunks` 预算仍约束读取次数。

**拍末每步 256 个值，且对齐。** 一步至多读取它所构建列表的一个字，附加证明不需要部分字，两个列表分别有 `⌈nOut/256⌉ + 1` 与 `⌈nLatch/256⌉ + 1` 个元组。输出与新状态对照双方一致的 `seg_m` 的 `P(S)` 核对，这就是先检查段列表的原因。

**稀疏检查点与按需细化。** 声明只公布每第 `K` 拍的叶。逐拍的叶只为被争议的区间计算，元组列表只为被争议的那一拍计算。这把细粒度承诺的链下成本从每次声明转移到罕见的被争议的那一次。总是包括 `T`，使最终状态与输出成为每个声明的一部分。

**输出链。** 输出根上的哈希链以每个检查点一个 32 字节值承诺一次运行的全部输出，拍叶可以用一次哈希对照前一拍核对。输入另由 `inputRoot` 承诺，因为博弈只需要被争议那一拍的输入。

**立即输与被拒绝的行动。** 打不开自己承诺或没通过检查的一方，反正也会因超时而输；立即判定省去另一方的等待，并使失败行动的结果在每个博弈中都相同。博弈已持有其哈希的值则不同：可接受的值只有一个，正确计算的一方总能提供，因此不符就像格式错误的交易一样被拒绝。拒绝还避免一个笔误或编码错误让诚实方输掉争议，而没有正确值的一方从中得不到任何好处，并在截止时间输掉。一场争议任何时刻只有一方该行动，所以超时规则不需要处理双方都错过截止时间的情形。

**多个挑战者，以及与争议分离的声明成立。** 一场争议只决定其双方中谁对某个区间是对的。如果第一个挑战就关闭窗口，声明者可以用第二个地址发起挑战，立即输掉（下标越界）或拖延到超时，错误的声明就会在任何诚实方能行动之前被判"声明者胜"；本草案早先版本的参考博弈就可以这样被击败。把声明状态与其争议分开就消除了这种攻击：赢得一场争议只击败那个挑战者，窗口对所有人保持开放，一个挑战者获胜即否决声明，声明成立要等窗口届满且所有未决争议结束。按争议逐场结算保证金，使每个挑战者的风险只限于它自己的争议；马甲要其主人付出一份保证金，得不到任何好处，至多把声明成立推迟一场争议的长度。

**`T` 与 `K` 的上界，以及按时间计的截止时间。** 第 2 层承诺这一步的工作量随 `K` 增长，挑战者的工作量随 `T` 增长，而二者都由声明者选择。没有上界时，声明者可以选 `T = 2^50`、`K = 2^40`（1,025 片检查点叶，在叶数上限之内）：没有挑战者能在截止时间之前构造 2^40 片叶的拍树，声明就无法被争辩。由博弈规定的上界，加上随 `K` 增长的承诺截止时间，使诚实方的每一步都可行。截止时间按时间而不是区块计，是因为一步背后的链下工作（重算拍、构造树与至多 96 KiB 的见证）无论出块间隔如何都耗时相同，而各链的出块间隔相差一个数量级。

**二分之前的公布检查。** 每个列表的首尾元组由公开数据或该方自己的拍叶确定。先检查它们，使下标 0 一致，把列表与被争议的叶绑在一起，并让公布不一致列表的一方立即输掉，无需核对调用。

**列表长度不等。** 作弊者可以承诺比规范列表更长或更短的列表。若两列表在较短长度之内一致，较短列表的末元组就是末元组，较长列表必须证明越过文件末尾或越过最后一个值的一步，没有见证能做到。诚实方永远不必这样做：作弊者的末元组必须带有最终偏移或最终序号，而诚实方更早的元组都没有。出于同样的原因，博弈按承诺字段比较末元组，而不按各自的叶比较：列表长度不同时，两个末元组的叶总是不同，因为叶带有下标；若按叶比较，即使双方对 `seg_m` 一致、只在输出或状态上有分歧，也会选中段列表。而且只比较承诺字段：不受任何叶约束的字段（例如不带 MEM 的文件的 `M`）可以随意设定，会使段列表被选中，尽管它的叶全部一致，此时 `h` 处的元组相同而长度也相同，博弈找不到必须证明一步的一方。

**见证种类由博弈绑定。** 输出列表与状态列表的首元组都是 `(0, EMPTY)`，元组形状相同，而 `verify_beat_end` 从见证中读取种类。若博弈不把种类绑定到选中的列表，一方就可以用另一个列表的计算来证明这个列表的一步，例如把前 256 个状态值当作第一个输出元组。

**为什么要 RAM 端口。** 用 LATCH 搭的 RAM 每一位要一个 LATCH，外加写译码器与读多路选择器，而每个被争议的拍都要在状态列表中把它全部重新证明一遍。卡带演示（见参考实现）显示了其代价：它 256 字节基于 LATCH 的 RAM 占文件 104,076 字节中的 98,380 字节（2,048 个 LATCH 与 12,884 个 NAND），每拍 3 段、9 个状态列表步，而它的程序（一个 256 项的 ROM）只占 540 字节。用 RAM 端口，同一台机器是 5,781 字节、一个分块、每拍一段、一个状态列表步，且每一拍的引脚与存储器都完全相同。把可写状态放在寄存器里的设计，如 INT-C16 的预算（94,800 个 LATCH 位），毫无损失：不含 RAM 端口的文件在每个字节、哈希、元组与见证上都不变。

**读口与写口分为两条记录。** 若一条记录同时含读口与写口，它的写入只能在拍末施加，核对函数就需要像 `D` 那样的第二张表来找到文件中的每次写入，元组要加字段，还要对一张写入列表二分。把二者分开，读取保持组合性（它读本拍开始时的存储器，正如 LATCH 读状态），含写口的段自己就能施加写入，运行中的根由 `M` 携带；`verify_beat_end` 不变。每个存储体的多个读口与写口自然得到支持，一拍内的读-改-写就是按顺序排列的读口、逻辑与写口。

**只允许向后引用、写入对下一拍生效、最后一次写入生效。** 写口的 `we`、地址与数据必须在它之前算出，使它所在的段拥有所需的值；这与 `d` 可以前向引用的 LATCH 不对称，是拍末核对函数保持不变的代价，设计者把写口放在给它供数的逻辑之后。写入对下一拍生效，使读取永不取决于它与写口在文件中的相对位置，从而读取保持组合性、分段与取值无关；一拍中两个写口写同一项时由文件顺序决定，也就是各段施加它们的顺序。

**固定深度的存储树。** 所有存储体共用一棵高 24 的树，使每项都有固定位置（`bank · 2^16 + word`），因此叶不需要下标，空存储器的根对每个文件都是同一常量，博弈不必知道文件的存储体就能导出 `seg_0`。步长向上取 2 的幂，使每项都落在一个字内，一次访问就是一个字加 24 个兄弟节点。按文件定深度可以为小存储器省下哈希，却会使 `ZERO_MEM` 依赖于文件。§6.3 的零兄弟掩码在存储器稀疏时让见证保持很小；它省的是 calldata，不是哈希。

**为什么不把存储器追加到状态向量。** 把每个存储体的 `2^a · w` 位追加到 LATCH 位之后，会使每个被争议的拍都要在状态列表中按每步 256 位重证整个存储器（64 KiB 时每拍 2,048 步），双方每拍都要对整个存储器算根，宽度不是 2 的幂的项会跨字，而且受状态树 2^16 个字的限制。单独的树只在端口读写处被触及。

**两个根作为核对函数的参数。** 博弈在第 3 层本来就要检查声明者对 `stateRoot_t` 的打开，因为 `memRoot_t` 进入公开的 `seg_0`；因此它把 `latchRoot_t` 与 `memRoot_t` 传给 `verify_segment`，而不是 `stateRoot_t`。这样核对函数永远不必根据见证判断一个状态根是 LATCH 根还是组合根（这种推断会允许什么，见安全考虑），见证不携带打开，`verify_segment` 对每个文件的行为都相同。对不带 MEM 的文件传入 `ZERO_MEM`，其元组、叶与见证都不变。

**标志字节。** 博弈需要知道文件是否带 MEM，才能构造 `seg_0`、把 `M` 哈希进段叶并应用第 5 条检查；每个声明读一次第 0 块的第 7 字节，比其他任何办法都便宜，不需要改动 `ITapLargeNetlistStore`，有效性第 11 条又使该标志与记录一致。

**ROM 读口。** 一个 ROM 每拍只按一个地址读出，因此在同一拍里既取指令又查表的设计，过去需要第二条 ROM 记录，带一份自己的数据副本。ROM 读口按第二个地址读取一条更早 ROM 的数据，不需要新的承诺：核对函数像对 ROM 本身一样通过分块来源读取这些字节。它不能替代 RAM，因为数据不能改变。

**一次存储访问按四个字计。** 一次存储访问在见证中占一个字、4 字节掩码与至多 24 个兄弟节点（至多 804 字节，约为一个 `P` 字连同其最坏情形下多重证明份额的 2.75 倍），读取约 25 次哈希，写入约 50 次。把它计为四个外部字，使默认预算下一段的最坏情形保持在见证上界与实测最贵调用的 gas 之内，而不需要第四个预算分量，`segmentBudget()` 的形式保持不变：Solidity 参考核对实现实测一个读口约 25,400 gas、一个写口约 37,000 gas（`a = 8`、`w = 8`），含 RAM 端口最贵的一段为 590 万，而最贵的调用为 930 万（附录 B）。

**全零的初始存储器。** 与状态一样，第 0 拍的存储器全为零，所以其根是常量，第 0 拍不需要只有声明者知道的原像就能核对（见上文的初始状态）。因此程序会改动的表要在运行时写入；永不改变的表应放进 ROM，ROM 读口可以每拍按多个地址读取它（见待定问题）。

**为什么不在链上求值，也不用有效性证明。** `TAP-draft-netlist-bound-nft` 的 Eval 扩展在一次调用中运行至多 49,152 字节的网表；每拍 110 万个门、每生成一个 token 约 9,800 万拍，任何调用都做不到。原则上可以为这样的运行生成简洁证明，但这种规模下的证明成本，以及 TapeOut 各链上缺少标准证明系统，使之只能作为以后的选项；争议博弈只需要 SHA-256 和一个能重新求值一段（默认预算下至多 6,000 门当量）的合约。

**为什么争议的经济层不在范围内。** 保证金数额、超时的长短与资格取决于应用（游戏奖金、集合担保、悬赏）和链；数据、核对函数以及争议如何决定声明的规则则不取决于这些。只固定后者，可以让多个博弈共享证明者和核对函数，并使每个符合本标准的博弈无论经济层如何都能抵御单槽攻击。

## 向后兼容

本 TAP 定义新的格式与新的接口，不改变任何现有内容：TAP-02 电路、`tapnet/1` 文件及其网表哈希保持原意。只实现 `tapnet/1` 的工具会看到一个它不认识的格式版本字节 `0x02`，并像对待任何未知版本一样，不把该文件当作 `tapnet/1` 文件接受；显示网表绑定 NFT 的工具则把这种代币的网表显示为不可核验，而不是无效（见下文）。

网表绑定 NFT（`TAP-draft-netlist-bound-nft`）通过该草案的 §2.5"其它网表格式"使用 `tapnet/2` 网表；该节允许 Standards TAP 定义另一种带有自己网表哈希的网表格式，具体用法见 §8。该 §2.5 属于对 `TAP-draft-netlist-bound-nft` 的一项修订提案，尚未合并；在合并之前，绑定到 `tapnet/2` 网表的代币不符合该草案 §5.2 与 §7.1 的字面要求。无论如何，§1 至 §7 都不受影响。本文以草案名称 `TAP-draft-netlist-bound-nft` 指代它；编辑为它分配编号后，对它的引用随之更新。

`tapnet/2` 满足该草案 §2.5 对格式提出的每一项条件：它以第 0–5 字节 `TAPNET` 加第 6 字节 `0x02`（§1.1）以及 `tap.netlist.format` 字符串 `"tapnet/2"`（§2.2）标识；它定义了自己的有效性（§1.2）、指标（§1.4）和网表哈希规则 `netlistHash2`（§2.1）；`tap.netlist` 中替代 `sha256` 与 `bytes` 的成员是 `netlistHash2` 和 §1.4 的 `bytes`（文件长度），后者取代 `tapnet/1` 的公式 `16 + 7·nand + 4·latch`（§2.2）。若无 SHA-256 碰撞，任何 `netlistHash2` 都不等于某个有效 `tapnet/1` 文件 `f` 的 `SHA-256(f)`：其原像是以 `0x03` 开头的 41 字节，而每个 `tapnet/1` 文件都以 `0x54` 开头（见设计理由）。

按该 §2.5，不支持 `tapnet/2` 的工具把 `tapnet/2` 代币的网表显示为不可核验，而不是无效；该草案的参考检查器对本 TAP 向量中的 `files.message` 示例正是如此处理。该草案的 `storeNetlist`、`netlistFile`、`netlistOf` 与 Eval 扩展只适用于 `tapnet/1` 文件（其 §2.5 与 §8.2 第 4 条），而 `tapnet/2` 文件的 SHA-256 从来不是它的网表哈希，因此实现这些函数的合约不受影响（§8.2）。

## 测试用例

`assets/tap-draft-large-netlists/vectors.json` 由 `make_vectors.py` 从参考实现生成，可逐字节复现（`python3 make_vectors.py --check`），SHA-256 为 `b6ba365e912f149e1ab8e292588fac7f934cdb1fa1ba6d8d1b8ac3dabfd9f2d6`。位串在位置 `i` 列出第 `i` 位；段元组对不带 MEM 的文件写作 `[byteOffset, firstSignal, latchOrdinal, P, D]`，对带 MEM 的文件写作 `[byteOffset, firstSignal, latchOrdinal, P, D, M]`，拍末元组写作 `[ordinal, root]`，存储器内容按存储体以项的列表给出。每个见证都以 §6.3 的编码完整给出十六进制内容（`witnessHex`），并附其 SHA-256 与大小；用例的分块来源是持有其 `example` 文件的存储合约（`oracle: "file"`）、空的存储合约（`"none"`）或只持有第 0 块的存储合约（`"chunk0"`）。各节为：

- `meta`：分块大小、§4.1 的域字节、默认预算 `(6000, 256, 16)`、用于强制产生许多段的微型预算 `(16, 4, 2)`、`EMPTY`、MEM 标志、存储树高度、一次存储访问的权重、`ZERO_MEM`（`0x073f71c2a5b9f99ff7828db2196e3c833ca70e5225e20912d7dcf150c165dcff`）以及零子树 `Z_0 … Z_24`；
- `files`：七个有效 `tapnet/2` 文件，含十六进制内容、SHA-256、指标、分块叶、分块根、`netlistHash2`、多块文件的每个分块证明，以及小文件规范展开（§1.3）的 SHA-256、大小与门数：

| 文件 | 字节 / 块 | nIn / nOut | nand / latch | romRecords / romBits | netlistHash2 |
|---|---|---|---|---|---|
| `sbox`（AES S 盒，`a = 8`，`w = 8`） | 300 / 1 | 8 / 8 | 0 / 0 | 1 / 2,048 | `0xccf1241a62105e591275e5ac83c68e9593f59bc84fa7083b88d56cf9d81a570b` |
| `mul4`（4 × 4 位乘积表） | 300 / 1 | 8 / 8 | 0 / 0 | 1 / 2,048 | `0xeebe110ecb2416ab0b400aa434cc84df1796e530cf86ea2b58b085ade242eac2` |
| `message`（6 位计数器寻址一段 ROM 文本） | 336 / 1 | 1 / 8 | 30 / 6 | 1 / 512 | `0xf5cd90005f48bc1eac7173c4f05c1af9710a1b6bd689d9377709c522627095b0` |
| `large`（合成样例，三个 ROM） | 89,509 / 6 | 16 / 16 | 8,016 / 64 | 3 / 264,256 | `0x6abfe829d81de05e2997125e2aa31e24a8b4f3da306e2fee6621ade5e1a0758f` |
| `ram16`（16 × 4 位存储器，存储体 3：两个 RAM 读口与一个读-改-写口；MEM） | 742 / 1 | 14 / 12 | 92 / 4 | 0 / 0 | `0xe3c579fd2206105d1408423b5038f11b0aa9c7f9ec8f5e56af6c26dcbf9728d2` |
| `rammix`（存储体 0（`a = 6`、`w = 12`）与 7（`a = 2`、`w = 1`），三个写口，其中两个写存储体 0，一个以存储器内容寻址的 ROM 读口；MEM） | 1,212 / 1 | 12 / 29 | 123 / 4 | 1 / 768 | `0x8eb8b26e72a134507aa9d1ff8dd7888213fac36333653a829ccdb044ea4625cf` |
| `romports`（一个 ROM，经它自己和两个 ROM 读口读取） | 68 / 1 | 6 / 15 | 0 / 0 | 1 / 40 | `0xc634f9a0606384ef6681c1b12deae78c055898dc5200d44575cc77ff2fed20fa` |

- `invalid`：48 个文件，每个因 §1.2 的一条规则被拒绝，并给出预期原因（文件头字段错误、未知标志位、有 MEM 标志而无 RAM 端口以及有 RAM 端口而无 MEM 标志、无记录、REF、未知操作码 `0x03` 与 `0x14`、各类记录被截断、前向引用（包括 RAM 写口的 `we`、地址与数据）、LATCH 引用超出最后一个信号、信号不足以构成输出、ROM 与端口的 `a` 与 `w` 越界、大于 2^24 位的存储体、同一存储体维度不同的 RAM 端口、`romOffset` 不指向维度相同的更早 ROM 的 ROM 读口、ROM 声明的数据超出文件末尾、填充位非零）；`invalidNotInHex` 描述两个大到无法收录的文件（超过 64 MiB；超过 2^24 个信号）；
- `step`：七个文件的单拍结果，包括 `message` 打印出 `Hello from tapnet/2 ROM!` 的 40 拍运行，以及 `ram16` 与 `rammix` 的 24 拍运行，含每拍前后的存储器与拍后的 LATCH 根、存储器根与状态根；
- `segmentations`：每个文件某一拍在两种预算下的规范边界、元组、元组列表根与逐段成本，以及输出与状态元组列表；对 `ram16` 与 `rammix` 还给出该拍状态根的打开 `(latchRoot, memRoot)`，以及拍末的 `seg_m`、状态列表的根和下一拍的状态根 `SHA-256(0x0C ‖ root ‖ seg_m.M)`；
- `verifySegment`：177 个用例（63 个预期为 true），每个给出 `a`、`b`、核对函数参数 `latchRoot` 与 `memRoot`、该拍的 `stateRoot`、预算、分块来源与见证，正例还给出读取的块数。正例包括从没有任何结构字节需要的块中读取 ROM 数据，以及 `stateRoot = EMPTY` 的第 0 拍。反例包括：声明的 `P`、`D`、`firstSignal` 或 `latchOrdinal` 错误；段少一条记录或多一条记录；`P` 字、`P` 前沿与状态字被篡改；`stateRoot` 错误与状态树大小错误；`stateRoot = EMPTY` 而见证中有状态字，以及写出零状态的根而不带状态字；文件未存储或只存储了一部分；在不同预算下核对。`ram16`、`rammix` 与 `romports` 上的 98 个用例（36 个预期为 true）包括含 RAM 读取的段、含多次写入（其中两次写同一存储体）的段、`memRoot = ZERO_MEM` 且有写入的第 0 拍，以及 ROM 读口；其反例包括读证明被篡改、缺失或多余，兄弟节点被篡改，写证明被篡改或被丢弃，声明的 `M` 错误以及忽略一次写入的 `M`，`memRoot` 参数错误，对非零存储器传入 `ZERO_MEM`，把组合状态根当作 `latchRoot` 传入（带或不带 `ZERO_MEM`，即安全考虑所述的情形），`memRoot = ZERO_MEM` 时仍给出读证明，以及非规范的存储编码（写出零兄弟节点、掩码高于第 23 位、多余尾部字节）；
- `verifyBeatEnd`：60 个用例（10 个预期为 true，其中 4 个在 `ram16` 与 `rammix` 上，`seg_m` 有六个字段），每个给出 `nOut`；反例包括声明的根或序号错误、`P_m` 或 `D_m` 错误、`P` 字或 `D` 字被篡改、`nOut` 参数错误，以及不在文件末尾结束的 `seg_m`；
- `checkpoints`：`message` 的运行，`T = 40`，`K = 8`：时刻 `0, 8, …, 40`，每片叶（第一片是常量 `0x2770ce5f9266ec7331a4479dfdfe1f22f261a49d52d5f17c2f7b64865d4b6d0f`）、`checkpointRoot` `0x3e5803cf23eeea6a2a6980cba5775eadc63f029177e3126ee62c834446dd3700`，以及打包的输入、输入叶与 `inputRoot` `0x3eef60dacb1c17e3bc5919d3863def3018183fa30d255e79c156800501eb8fd0`；
- `store`：`beginLarge` 在长度边界 16、17、67,108,864 与 67,108,865 处的结果，以及未知哈希、长度与哈希不符的情形；`large` 的一次上传：`beginLarge` 之前的 `putChunk`、按 5、2、0、0、4、1、3 顺序上传的块、每次调用后的 `largeInfo`、事件，以及被篡改的块；
- `disputes`：在 `message`、`large`、`ram16` 与 `rammix` 上模拟的 81 场争议：诚实对诚实；每类作弊者分别作为声明者和挑战者（非规范分段，偏短与偏长；新状态的某一位错误；ROM 输出错误；伪造检查点；值正确但谎报输出、并在文件末尾之后多加一个段元组；值正确但谎报输出、并在末段元组中伪造不被任何叶承诺的 `M`；第 0 拍的谎报，以 `stateRoot = EMPTY` 裁决；某拍之后声明 `stateRoot = EMPTY`）；扣留或谎报被争议拍输入的说谎声明者；在 `t = 8, 16, 24, 32` 各伪造一个中间检查点而最后一片叶正确；以及指定了叶相同之下标的挑战者；在 `ram16`（微型预算，每拍多段）与 `rammix`（默认预算）上，错误的 RAM 读取、写口之后伪造的存储器、错误的写数据、新状态的某一位错误以及第 0 拍的错误写入，各自分别作为声明者和挑战者，错误与扣留的输入，以及不打开双方一致的状态根的声明者。诚实方赢得每一场诚实方与不诚实方之间的争议；到达核对函数的争议在 6 到 11 轮（按附录 D 计数）后以恰好一次调用结束，其余的在某项公布检查处结束，不经调用；诚实对诚实不产生争议；指定了叶相同之下标的挑战者输；
- `measurements`：§6.3 的见证上界（段见证 84,208 字节，含不含 RAM 端口都一样），以及 `large` 与一个 21,000 NAND 文件在默认预算下的逐段成本、读取块数与见证大小。

接口（选择器用 `cast sig` 计算，事件主题用 `cast keccak` 计算；ERC-165 标识符是各函数选择器的异或）：

| 项 | 选择器或主题 |
|---|---|
| `ITapLargeNetlistStore`（ERC-165） | `0x8230af7f` |
| `beginLarge(bytes32,uint64,bytes32)` | `0xb0fc46a0` |
| `putChunk(bytes32,uint32,bytes,bytes32[])` | `0x806ca833` |
| `largeStored(bytes32)` | `0xe1a06945` |
| `largeChunk(bytes32,uint32)` | `0x75077164` |
| `largeInfo(bytes32)` | `0x260759cd` |
| `segmentBudget()`（核对或博弈合约，§5.1） | `0x09683cea` |
| `UnknownNetlist(bytes32)` | `0x1dd49e73` |
| `BadLength(uint64)` | `0xf55c3a0f` |
| `HashMismatch(bytes32)` | `0x7d7e4bfb` |
| `NotBegun(bytes32)` | `0xc7be356f` |
| `BadChunk(uint32)` | `0xa46ef7e6` |
| `ChunkNotStored(uint32)` | `0x23f6aba8` |
| `LargeBegun(bytes32,uint64,bytes32)` | `0x9f1baa4bbf9155c50182876bbfc80a6ae68dc45e89869b743774f6a9148dcd0f` |
| `ChunkStored(bytes32,uint32)` | `0x0b105209a631d6f65bf6ca2cfee3327230af70d7ad1215e49d434cf751b7c7d8` |
| `LargeStored(bytes32)` | `0xbea4f9cc5ffc14f00d94052699882369f432531457ac3211008d81a630c3c573` |

## 参考实现

Python 参考实现位于 `assets/tap-draft-large-netlists/`（MIT），是无依赖的 Python 3.9（`python3 make_vectors.py --check && python3 test_tapnet2.py`）：

- `tapnet2.py`：`tapnet/2` 的编码、有效性与指标，包括 RAM 端口、ROM 读口与标志字节；带存储器的单拍求值（`evaluate_full`）；ROM 规范展开；存储树与带 MEM 文件的状态根；§0 的树、证明与多重证明；分块与 `netlistHash2`；`ITapLargeNetlistStore` 的模型，它同时是分块来源；前缀根与附加证明；输入叶；预算与规范分段；见证构造、`verify_segment`、拍末核对函数、检查点、输出链与元组叶；§6.3 的见证编码与见证大小上界。核对函数只使用其参数与分块来源，模拟链上核对者。`step` 与 `evaluate` 既接受解码后的 `Netlist`，也接受文件字节；要跑很多拍的调用方应只解码一次并传入 `Netlist`（卡带演示实测这样每拍约 3 ms，传字节则约 31 ms）；
- `bisect_sim.py`：模拟双方之间 §7 的争议，裁判只持有链上数据（存储合约、声明与公布的叶），从不查看任何一方的私有值，包括测试用例一节列出的各类作弊者；它按附录 D 的方式计数轮数。其声明层（`ClaimGame`）在多场争议之上应用 §7.1 的声明成立规则，其裁判可选地执行 `T` 与 `K` 的上界以及两种输入情形；
- `examples.py`、`make_vectors.py`、`test_tapnet2.py`、`test_ram.py`：样例文件、向量生成器与 68 个测试（`test_tapnet2.py` 50 个，`test_ram.py` 18 个），包括用独立的 TAP-02 求值器 `assets/tap-02/reference.py` 检查 `tapnet/2` 文件与其展开结果的等价性、只凭十六进制内容核对每个向量，以及 §7.1 的声明规则（马甲、多个挑战者、上界、输入情形）；`test_ram.py` 把用 LATCH 搭的存储器与用 RAM 端口搭的同一存储器逐拍比对，把 `rammix` 与其行为的直接模型比对，并检查写入顺序、ROM 读口、存储树、§6.1 中关于端口的每条核对规则（含变异）、§6.3 的存储编码，以及双方角色下有 RAM 作弊者的争议。

Solidity 参考实现、卡带演示与实测数据位于参考仓库 `tap-large-netlists`，其中也有本 TAP 及其资产的副本，提交 `4fa0c0baf2c1d226b38814aed0d0f58c244cad24`：https://github.com/BruceLanLan/tap-large-netlists/tree/4fa0c0baf2c1d226b38814aed0d0f58c244cad24。它没有部署，也没有经过审计。其内容：

- `contracts/src/ITapLargeNetlistStore.sol` 与 `contracts/src/TapLargeNetlistStore.sol`：§3 的接口，以及每块一个 SSTORE2 数据合约的存储合约；哪些哈希是已知的交给派生合约决定（`knownNetlist`），`TapLargeNetlistStoreOwned` 为测试保存一个由所有者登记的集合；
- `contracts/src/TapLargeVerifier.sol`：`verifySegment`（§6.1，含 RAM 端口、ROM 读口、`latchRoot` 与 `memRoot` 两个参数以及存储证明）、两种类型的 `verifyBeatEnd`（§6.2）与 `segmentBudget()`（§5.1），都是 view 函数，只通过存储合约的 `largeInfo` 与 `largeChunk` 读取文件，见证采用 §6.3 的编码；`contracts/src/TapLargeHash.sol`：§2.1 与 §4 的哈希；
- `contracts/src/game/TapLargeDisputeGame.sol`：一个信息性的争议博弈，把 §7 接到 §6 的核对函数上（单一保证金额、无手续费；不是生产代码）。争议以 `(声明, 挑战者)` 为键；窗口内任意多个挑战者都可以加入，其他争议未决时也可以；声明状态按 §7.1 的要求与其争议分离（挑战者获胜即否决声明，`accept` 只在窗口届满且无未决争议时成功，因否决而留下的未决争议通过 `refund` 退还保证金），保证金按 §7.1 的建议逐场结算。博弈规定 `maxK` 与 `maxT`，使用按时间戳计的截止时间（`moveTime`，commitBeats 为 `moveTime + perBeatTime · K`），并由构造参数决定输入情形：情形 (a) 在 `claim` 中从 calldata 中的输入算出 `inputRoot`，情形 (b) 只接受由固定的、永远不能提出声明的提供者公布过的三元组 `(netlistHash2, T, inputRoot)`。它随每个声明记录 `largeInfo`、MEM 标志与 `segmentBudget()`，从公布的列表算出 `checkpointRoot`（至多 2,048 片叶），并完全按 §7.2 至 §7.4 执行，每个中点都由博弈选定；对带 MEM 的文件，它在第 3 层检查声明者对 `stateRoot_t` 的开示，把 `M` 哈希进段叶，并把 `latchRoot_t` 与 `memRoot_t` 传给最终调用；它按承诺字段比较末元组（段元组按其在下标 0 处的叶）。它对 calldata 列表的树计算（`checkpointRoot`、情形 (a) 的 `inputRoot`、`seg_0` 的 `P`）放在外部库 `contracts/src/game/TapLargeGameLib.sol` 中，使博弈合约低于 24,576 字节的代码大小上限。当场输掉的挑战也发出 `Challenged`，随后是带原因的 `Resolved`；最终调用在此之前发出带列表种类与下标的 `VerifierCalled`；
- `contracts/test/`：重放每个 `verifySegment`、`verifyBeatEnd`、`store` 与 `checkpoints` 向量，结果与 Python 参考实现一致（包括每个正例步读取的块数）；由 Python 参考实现生成的 3,500 个随机用例的差分语料（其中一半用例的文件含 RAM 端口或 ROM 读口）（`contracts/test/data/gen_diff.py`）、模糊测试、单元测试，以及附录 B 的 gas 测量（`contracts/test/data/gen_gas.py` 用 Python 参考实现构造合成文件，并用其核对函数检查每个用例）；
- `contracts/test/game/E2E.t.sol` 与 `contracts/test/data/game_gen.py`：`message` 样例（情形 a）、`large` 样例（情形 b）、含 RAM 端口的文件 `ram16`（情形 a）与 `rammix`（情形 b），下文演示中的卡带（情形 b）及其 LATCH-RAM 版本（情形 b，不带 MEM）上的 118 个端到端场景。生成器让 `bisect_sim.py` 中的各方对着博弈的 Python 镜像对局，记录每一笔交易；测试从该方自己的地址在链上重放每一笔，并断言每场争议的赢方与原因、声明的最终状态、每一笔保证金转移、每次公布时博弈所问的下标，以及核对调用次数（争议到达最终调用时一次，否则零次）。场景覆盖测试用例一节中每类作弊者（包括 RAM 作弊者）的两种角色、错误与扣留的输入、错误的检查点下标、五种行动上的超时、各层的打不开承诺与没通过检查、无法开示约定 `stateRoot_t` 的声明者、伪造末段元组中未被承诺的 `M` 的一方（在 `message`、`large` 与 LATCH-RAM 卡带上，两种角色）、卡带的分数争议，以及六个多挑战者场景：作弊声明者的马甲立即输掉或拖延，随后诚实挑战者获胜；两个诚实挑战者并行；诚实声明者并行对抗两个作弊挑战者；
- `contracts/test/game/MultiChallenger.t.sol`：第二次审阅对单槽攻击的两个复现（现在作为攻击均失败），以及声明成立规则的各项防护；`contracts/test/game/GameLimits.t.sol`：上界、两种输入情形、按时间计的截止时间、输入由输入提供者公布的 2,048 片叶的声明，以及把 gas 测量中最贵的最终调用经由博弈发送；
- `measurements/attacks/`：对照现行规则重跑本稿第一次审阅中攻击的脚本（`attack1.py` 见证大小，`attack2.py` 状态树大小，`attack3.py` 隐藏的中间检查点），以及测量接近最坏情况见证的脚本（`worst_witness.py`）；`measurements/l1fee.md`：附录 B 的 L1 数据费快照。

**成本。** 附录 B 给出存储合约、核对函数与参考博弈的实测 gas，以及 OP Stack 链上 L1 数据费的一份快照。简言之：存储一个 16 KiB 的块每笔交易约 370 万 gas；实测最贵的核对调用约需 930 万执行 gas，作为博弈的最终一步约 1,060 万；118 个场景中的每一步都低于 16,777,216 gas；实测含 RAM 端口最贵的一段约需 590 万执行 gas。本 TAP 不对上传或争议的货币成本作任何断言。

**卡带演示（信息性）。** 参考仓库的 `demo/cartridge/` 把 TAPBOY-8（一台 8 位单周期 CPU）搭成一个 `tapnet/2` 文件：5,781 字节（1 块），720 个 NAND、25 个 LATCH、一个装着程序的 ROM（256 条 16 位指令），以及一个存储体中的 256 字节 RAM，带一个 RAM 读口和一个 RAM 写口；它每拍执行一条指令。默认预算下其规范分段只有 1 段（门成本 802），每拍一个状态列表步，诚实段见证 48 至 224 字节。为作比较而保留的、RAM 基于 LATCH 的同一 CPU 占 104,076 字节（7 块，13,604 个 NAND、2,073 个 LATCH），每拍 3 段、9 个状态列表步；二者每一拍的引脚与存储器都完全相同。一项测试把电路与一个独立的指令级模拟器逐拍比对，包括全部 RAM。在它上面运行的一个小游戏里，玩家声明 651 拍之后的分数，`K = 32`；程序停机后分数保持在输出引脚上，并从最后一片检查点叶开示（附录 C）。通过伪造的 RAM 写入、错误的 RAM 读取或错误的 ALU 门虚报分数的声明，在 8 或 9 轮中以恰好一次 `verify_segment` 调用输掉；作弊挑战者对抗诚实声明时同样如此（RAM 基于 LATCH 时为 10 至 12 轮）。在参考博弈中进行时，每场这样的争议各方合计 230 万至 240 万 gas，其最终调用至多约 76 万（附录 B）。

## 部署

本 TAP 不依赖任何已部署的合约，它自己的合约（存储合约、核对函数合约与参考争议游戏）目前也均未部署。进入 Review 之前，本节将加入存储合约与核对函数合约的一次测试网部署，并附上只读核验其地址与代码的步骤。

## 安全考虑

- **假设。** 核对函数与争议的可靠性依赖于 SHA-256 的抗碰撞性、§4.1 的域字节以及每片叶中的下标。在这些假设下，对数据可获取的有效文件，并且公布的输入与 `inputRoot` 相符时，正确计算每个值并遵循 §7 的一方，会赢得它参与的每一场争议；按 §7.1 的声明成立规则，只要有一个这样的一方在窗口内挑战并把争议走完，错误的声明就会被否决。
- **有效性无法在链上强制。** 没有合约检查 7.5 MB 文件的有效性，核对函数只检查它们触及的记录；`largeStored` 只说明各块与 `netlistHash2` 相符，不说明文件有效。对无效文件，挑战者可以把任何争议引到含无效记录的一段，在那里每个后继都会失败；由于被核对的总是声明者的元组，任何挑战者都能击败任何声明者，无论声明者计算是否正确。因此博弈只能为应用事先承诺过、且其文件已在链下检查过的网表启动（§7.1）；承诺无效文件的应用违背了它自己的承诺（对网表绑定 NFT 集合而言，即违反经其 §2.5 适用于其他格式的 `TAP-draft-netlist-bound-nft` §5.2，§8.3），工具在核对该文件时拒绝它（§8.1）。无效性证明是待定问题。
- **数据可获取性。** 没有文件、输入或声明者检查点叶的一方无法计算值或见证，它算不出的声明实际上无法被挑战。因此 §7.1 要求在接受声明之前 `largeStored(netlistHash2)` 为 true，声明附带完整的检查点列表，并在挑战窗口开始之前公开输入。在第 2 层与第 3 层，声明者的拍叶与元组连同证明在链上公布。
- **来自输入提供者的输入。** 当 `inputRoot` 是给出的而不是由博弈计算的（§7.1 情形 b），提供者可能公布输入 `X`，而 `inputRoot` 承诺另一组输入 `Y`。在 `X` 上计算运行的挑战者，会与在 `Y` 上正确计算的声明者产生分歧；在第 3 层，声明者对照 `inputRoot` 公布 `Y_t`，公开的 `seg_0` 由 `Y_t` 得出，而挑战者由 `X` 算出的元组通不过公布检查或最终调用（参考实现复现了这一点）。自己充当提供者的声明者可以故意这样做，使其声明无法被挑战，所以 §7.1 禁止这样做。面对第三方提供者，挑战者必须在挑战之前对照 `inputRoot` 检查公布的输入，二者不一致时不得在拍层面挑战。本版本没有这种不一致的证明（见待定问题）；由此引出什么后果由应用决定，例如只指名它信任的输入提供者，或在 `T` 允许时在链上计算 `inputRoot`（情形 a）。
- **活性与计算量。** 争议只保护参与者，无人核查的声明就会成立。诚实的挑战者必须重算这次运行才能找到错误的叶：对 INT-C16 这样的设计，每拍约 110 万个门、每生成一个 token 约 9,800 万拍，即每生成一个 token 约 1.1 × 10^14 次门求值，按每秒 10^9 次门求值约需 30 小时。诚实的声明者被挑战时，必须重算被争议区间至多 `K` 拍与一拍的元组列表，并及时应答每一步。上界 `maxT` 与 `maxK`、挑战窗口、每步截止时间与保证金必须覆盖这些链下计算、各步的链上成本，以及至多 96 KiB 的见证（§6.3）；按区块数计的截止时间会随出块间隔缩短，这就是 §7.1 建议按时间计的原因。
- **马甲挑战。** 声明者可以从其他地址挑战自己的声明。在一个挑战就能关闭窗口的博弈中，立即输掉或拖延的马甲会在任何诚实挑战者能行动之前了结一个错误的声明；§7.1 排除了这样的博弈。按 §7.1，马甲只会击败它自己：窗口保持开放，诚实挑战者仍能挑战，声明只在窗口届满、且没有未决或挑战者胜的争议时才成立。每个马甲都要其主人付出一份保证金（若按 §7.1 建议结算保证金，则付给它自己）与其各步的 gas，并至多把声明成立推迟一场在窗口届满前开始的争议的长度。
- **延迟与并行挑战。** 在窗口届满前加入的每个挑战者都能把声明成立推迟一整场争议，而争议并行进行，所以延迟以窗口加上最长的一场争议为界，而不随挑战者人数增长。每个挑战者都可以把自己的争议引到不同的检查点区间，因此声明者可能要并行地为每个区间各重算至多 `K` 拍；`K` 由声明者选择，但受博弈的上界约束（`maxK`，§7.1），保证金则让失败的挑战付出代价。诚实声明者必须及时应答每一场争议；博弈应按它允许并行的争议数来确定截止时间。
- **谁为上传付费。** 不在范围内（§3）。任何人都可以上传；抢跑 `putChunk` 无害，因为抢跑者自己付费，原调用变成空操作；`beginLarge` 无法以其他值抢跑，因为这些值必须与 `netlistHash2` 相符。
- **规范分段只在某个值错误时才起作用。** 段元组只在检查点叶出现分歧之后才公布，也就是在某个值已经出错之后。此时，值正确但分段不规范的一方可能会输，因为被核对的元组必须是规范的后继。因此证明者必须在声明的预算下使用 §5.3 的规范分段。
- **通过列表捣乱。** 作弊者可以承诺任意长度或含任意未打开子树的列表。二分的代价只随较短列表长度的对数增长，较长列表必须证明超出较短列表的任何一步（§7.4），而无法打开自己承诺过的下标的一方会输。拍叶树的大小由博弈确定（§7.3）；博弈也可以限制元组列表的长度（下标为 32 位）。
- **`netlistHash2` 与 `SHA-256(file)`。** 核对函数与存储接口只检查 `netlistHash2`。公开的 `tapnet/2` 文件 `SHA-256(file)` 不被任何东西检查，也不是其身份；工具从文件计算 `netlistHash2`。`netlistHash2` 约束长度和每一块，因此除非 SHA-256 碰撞，两个不同文件的 `netlistHash2` 不同。
- **见证大小与解析。** 核对函数以预算约束自己的工作量，读取前用文件长度核对记录长度，任何数据缺失都失败；精心构造的文件可以让段变短，但不能让核对函数超出预算，唯一例外是每段的第一条记录，其大小受格式约束（ROM 记录头至多 64 个结构字节，门成本至多 276，外部字至多 20 个；RAM 写口至多 836 个结构字节，门成本至多 277，外部字至多 277 个，外加一次存储访问；§6.3 的见证上界已计入二者）。见证不携带文件字节，其大小受预算约束（§6.3），因此精心构造的文件无法把诚实见证推到博弈必须接受的上限之上。
- **没有排他性。** 已存储的 `tapnet/2` 文件是公开的：任何人都能读取分块、求值该设计，并在别处引用同一文件。`netlistHash2` 以及指名它的声明或代币，证明的是所指的是哪个文件，而不是对设计或其权重的独占使用。
- **预算变更。** 若预算在声明期间改变，规范分段就会在双方脚下改变；§5.1 规定它按声明固定。
- **存储器，以及状态根的形式。** 对带 MEM 的文件，状态根组合了 LATCH 根与存储器根（§4.6）。若核对函数只接收 `stateRoot`、并根据见证中的打开推断其形式，就会面临这样的攻击：一方把组合根当作普通 LATCH 根交给一个有 RAM 读取而没有 LATCH 的段，核对函数看不到打开，就把存储器读成全零；伪造的读取值进入 `P`，该方便能为错误的后继自证。这种设计必须在段含 RAM 读取时强制要求打开（或 `EMPTY`）。本 TAP 从构造上消除了这种情形：博弈在第 3 层检查打开，并把 `latchRoot_t` 与 `memRoot_t` 作为两个独立参数传入（§6.1、§7.4），RAM 读取只对照 `memRoot` 检查，见证的任何内容都不能改变读取所对照的根。域分隔使两种形式互不混淆：LATCH 根是 `0x01` 节点或 `0x05` 叶，组合根是 `0x0C` 哈希，不发生碰撞就不能把其一打开成另一。其余的可靠性论证与状态相同：双方一致的 `stateRoot_t` 的打开是唯一的；读取对照双方一致的 `memRoot_t` 证明并进入 `P`；双方一致的元组 `seg_{j−1}` 的 `M` 与 `P` 中的值决定了 `seg_j` 唯一正确的 `M`；段列表与诚实方相同而 `stateRoot_{t+1}` 不同的作弊者通不过第 5 条检查；对有效文件，不同项的位置 `bank · 2^16 + word` 互不相同。
- **含 RAM 端口的文件与有效性。** 同一存储体的 RAM 端口 `a` 与 `w` 一致、写口只向后引用、`romOffset` 指向一条维度相同的更早 ROM，这些都是有效性规则（§1.2），与其他规则一样在链下检查；对无效文件，任何挑战者都能赢，如上所述。博弈所依赖的规则 11（MEM 标志置位当且仅当文件含 RAM 端口）也是如此：博弈从已存储的第 0 块读取该标志（§7.1），对不带该标志的文件既不承诺也不比较 `M`（§4.5、§7.4），这之所以可靠，是因为这样的文件没有 RAM 写口，`M` 在一拍之内不会改变。含 RAM 端口却不带该标志的文件，其存储器不受任何叶约束；与每个无效文件一样，必须在为它启动博弈之前在链下拒绝。

### 待定问题

1. ROM 操作码是保持 `0x10`，还是与 TAP-02 协调，使未来的 TAP-02 版本可以使用同一记录？
2. 结构哈希应如何处理 ROM：作为一个以内容哈希为标签的节点，还是按其规范展开？
3. `TAP-draft-netlist-bound-nft` §2.5（其它网表格式）的采纳：它属于对该草案的一项尚未合并的修订提案（§8.3）。§2.2 已经以规范方式定义了该 §2.5 对格式的要求：`format` 字符串以及替代 `sha256` 与 `bytes` 的成员。§2.5 被采纳后，§8 的其余部分（池条目、`netlistHashOf`、不经 `storeNetlist` 与 Eval 扩展、争议资格）是否应改为规范性内容？
4. 预算数值与见证上限：`(6000, 256, 16)` 与 96 KiB 依据 Solidity 参考核对实现的 gas 选定（附录 B）。有了其他核对实现之后，是否应重新审视？对 OP Stack 链上 L1 数据费的第一次快照（附录 B）发现，它相对 L2 执行很小：在 Base 上低于 1%，在 X Layer 上为零，因为其费用系数为 0。该快照是在以太坊 blob fee 很低时取得的，确定这些数值之前应再测一次。
5. 争议博弈（声明、保证金、超时、资格）是否应成为建立在 §7 之上的单独 Application TAP？
6. 以后的版本是否应允许存储器有初始内容，例如由文件中的数据预置一个存储体？存储器与状态一样从全零开始（设计理由），因此程序会改动的表要在运行时写入；永不改变的表应放进 ROM，ROM 读口（`0x13`）可以每拍按多个地址读取它。预置的存储体会使 `memRoot_0` 依赖于文件，第 0 拍就需要它作为公开值。
7. 无效性证明：除 §1.2 第 5、7 条外，所有有效性规则都只涉及单条记录，而 `S` 与 `nLatch` 可由双方一致的 `seg_m` 得出，所以分段框架可以证明文件无效，并让声明作废，而不是判挑战者赢。以后的版本是否应定义这样的证明？
8. 输入不一致：当 `inputRoot` 由输入提供者给出时（§7.1 情形 b），以后的版本是否应定义一种证明，证明公布的输入与 `inputRoot` 不符（例如对照 blob 中的数据在输入叶上二分），使输入不一致的声明可以在链上作废？
9. 铸造即绑定集合的争议资格：§8.2 只为由集合清单（主标准 §5）承诺的网表启动关于该集合 `tapnet/2` 网表的博弈，这是 §7.1 所要求的事先承诺的一种形式。铸造即绑定集合（主标准 §8.2，与主标准 §2.5 属于同一项修订提案，见 §8.3）没有清单，而是在每个代币铸造时记录其网表哈希。这种集合的网表是否应有资格，以铸造时记录的网表哈希作为承诺？若是，既然以后的铸造可能记录新的哈希，博弈之前应如何在链下检查这些文件？

## 版权

版权及相关权利依 [CC0](../LICENSE) 放弃。

## 附录 A：INT-C16 的规模估算（信息性）

这些数字是作者为 INT-C16 给出的源级预算（见动机），只用于估算问题规模。

- **文件大小。** 仅按 NAND 与 LATCH 记录写出，`16 + 7 · 1,012,501 + 4 · 94,800 = 7,466,723` 字节，尚未计入任何 ROM 数据：约为 `TAP-draft-netlist-bound-nft` 的 49,152 字节上限的 152 倍，即 456 个 16,384 字节的分块。权重的 ROM 数据另计。
- **ROM 与 NAND 对比。** 测试向量中 256 项的 AES S 盒作为一条 ROM 记录（`a = 8`、`w = 8`）占 300 字节，其规范展开（§1.3）占 6,288 字节，即 896 条 NAND 记录。
- **每生成一个 token 的工作量。** 每个芯片时钟一拍，每生成一个 token 约 9,820 万拍，每拍求值约 `1,012,501 + 94,800 ≈ 110` 万个门：每生成一个 token 约 `1.1 × 10^6 · 9.8 × 10^7 ≈ 1.1 × 10^14` 次门求值，按每秒 10^9 次门求值约需 30 小时。
- **检查点。** 检查点叶至多 2,048 片时（§7.1），9.8 × 10^7 拍的运行需要 `K ≥ 9.8 × 10^7 / 2,047 ≈ 48,000`。重算这样的一个区间约需 `48,000 · 1.1 × 10^6 ≈ 5.3 × 10^10` 次门求值，按每秒 10^9 次不到一分钟，这就是 `maxK` 与第 2 层承诺截止时间必须容许的量级（§7.1）。

## 附录 B：实测数据（信息性）

**测量设置。** 全部 gas 数字均用 Solidity 参考实现（见参考实现）与 Foundry 1.8.3、solc 0.8.28（优化器 10,000 runs、传统代码生成即不用 via-IR、`evm_version` 为 Shanghai）实测；这些操作的 gas 在 Osaka 规则下相同。执行 gas 是从另一个合约调用存储合约或核对合约所花的 gas，此时存储合约与块数据合约是冷的、预编译合约是热的。calldata gas 按每个非零字节 16、每个零字节 4 计算，交易估计再加 21,000；对这些调用，EIP-7623 的 calldata 下限从不是较大的一方。博弈的数字按交易计，含 21,000 固有 gas 与 calldata（在 Foundry 中，测试发出的每个顶层调用都作为一笔独立交易执行）。

| 操作 | 执行 gas | calldata | 交易估计 |
|---|---|---|---|
| `beginLarge` | 52,902 | | |
| `putChunk`，一个 16,384 字节的块 | 3,368,007 至 3,370,176（约每字节 206，主要是代码存储费） | 16,772 字节，约 267,000 gas | 约 366 万 |
| 上传一个 7,466,723 字节的文件（456 块） | 1.54 × 10^9 | | 合计约 1.67 × 10^9 |
| `largeChunk`，一个 16,384 字节的块 | 15,512 | | |
| `verifySegment`，6,000 个输入在段内的 NAND | 4,257,622（约每个 NAND 700） | 708 字节 | 4,283,986 |
| `verifySegment`，6,000 个 LATCH（24 个状态字） | 5,076,447（约每个 LATCH 840） | 1,668 字节 | 5,118,111 |
| `verifySegment`，6,864 门当量的 ROM（`a = 8`、`w = 256`，不限块数） | 2,585,800 | 708 字节 | 2,612,176 |
| `verifySegment`，256 个 NAND，各读最深信号树中一个不同的分散字 | 1,757,646 | 67,812 字节 | 2,850,042 |
| `verifySegment`，6,000 个 NAND 读取最深信号树中 256 个分散字 | 9,306,494（约每个 NAND 1,290） | 75,940 字节 | 10,528,410 |
| 同上但 7,000 个 NAND（原默认值 `(7000, 256, 8)`） | 10,590,214 | 75,940 字节 | 11,812,130 |
| `verifySegment`，64 个 RAM 读口（`a = 8`、`w = 8`） | 1,671,387（约每个端口 25,400） | 9,156 字节 | 1,832,007 |
| `verifySegment`，64 个 RAM 写口（`a = 8`、`w = 8`） | 2,415,970（约每个端口 37,000） | 9,156 字节 | 2,576,554 |
| `verifySegment`，4,078 个 NAND 与 64 个 RAM 写口，稠密存储器（64 个 804 字节的证明） | 5,907,435 | 52,164 字节 | 6,751,959 |
| `verifySegment`，4,142 个 NAND 与 64 个 RAM 读口，稠密存储器（64 个 804 字节的证明） | 5,180,926 | 52,164 字节 | 6,025,690 |
| `verifySegment`，7 个 ROM 读口（`a = 10`、`w = 128`），其数据位于该段不解析的块中 | 452,483 | 932 字节 | 482,791 |
| `verifySegment`，卡带在一个有一次 RAM 读与一次 RAM 写的拍中的那一段 | 648,550 | 868 字节 | 677,402 |
| `verifyBeatEnd`，256 个输出 | 322,735 | 1,060 字节 | 357,095 |
| `verifyBeatEnd`，256 个 LATCH，其 `d` 读取 256 个分散字 | 2,311,605 | 73,188 字节 | 3,486,165 |

最深信号树是测量文件 `wideA`（参考仓库的 `contracts/test/data/gen_gas.py`）的信号树：它有 16,777,024 个信号，信号树有 65,536 个字、16 层，是格式所允许的最深；分散读取遍布整棵树。一步每多读一块约增加 16,000 gas。

**默认预算。** 在 `(6000, 256, 16)` 下，实测最贵的调用是上表中 6,000 个 NAND 的一段，执行 930 万 gas。`chunks` 预算取 16 对它至多增加约 30 万，见证取 §6.3 的上界（84,208 字节）时 calldata 约 135 万，因此只做这一次调用的交易仍低于约 1,100 万 gas，约为 16,777,216 的 65%、30,000,000 的 37%。§5.1 的余量指执行 gas：执行 gas 为参考实现 1.5 倍的核对实现（此例约 1,400 万），加上同样的 calldata、块读取与博弈自身的工作（参考博弈中约 62,000 gas），约为 1,570 万，低于 16,777,216；它并不意味着每笔交易都可以贵 1.5 倍。原默认值 `(7000, 256, 8)` 在同一用例约需 1,180 万（见证取上界时约 1,200 万），按上述估算会超过 16,777,216。`extWords = 256` 使见证上界低于常见客户端 128 KiB 的交易大小限制；读一块约需 16,000 gas，因此 `chunks = 16` 代价很小，而对含许多小 ROM 的文件，段数比取 8 时减半。

**见证。** 默认预算下，测试向量各文件的段见证为 48 至 1,068 字节，每步至多读 5 块。实测最大的诚实见证为 74,896 字节（参考仓库的 `measurements/attacks/worst_witness.py`），来自一个构造的 2^24 个信号的文件，其最后几段证明之前 65,535 个字中的 256 个分散字；gas 测量使用了同类的 75,280 字节见证（按每个非零字节 16 gas 计，calldata 约 120 万 gas；98,304 字节至多约 160 万）。

**存储访问。** 上表中含 RAM 端口的各行使用参考仓库 `contracts/test/data/gen_gas.py` 的用例：同一存储体的端口，其各项均非零，地址与数据取自段内算出的信号，因此端口不读外部字，存储器根也不是 `ZERO_MEM`。gas 随端口数线性增长（实测 1、8、32 与 64 个端口）。`a = 8`、`w = 8` 的一个 RAM 读口约需 25,400 执行 gas，一个 RAM 写口约 37,000，包括对其引用与输出的求值以及其证明的 25 或 50 次 SHA-256 哈希；每多一个地址位约增加 700 gas（`a = 16`：每个端口约 30,900 与 42,500），每个数据位读口约增加 290、写口约 670（`w = 64`：约 41,800 与 74,300；`w = 256`：约 96,800 与 202,000）。一次存储访问至多增加 804 字节见证（24 个非零兄弟节点），约 13,000 gas 的 calldata；兄弟节点全为零时只有 36 字节。默认预算下一段至多有 64 个 RAM 端口（每个按四个外部字计，§5.2），它们用掉全部 256 个外部字，因此该段其他记录不读外部字。实测最贵的这类段，即写入 64 个存储体的 64 个写口（`a = 20`、`w = 9`：每个 30 门当量；存储器在每个被写项周围都是稠密的，因此每个证明都是 804 字节）加 4,078 个 NAND，需 590 万执行 gas、52,164 字节 calldata（作为参考博弈的最终一步为 680 万）；换成 64 个读口时为 520 万。二者都远低于最贵调用的 930 万，因此实测结果不要求改变默认预算，也不要求改变每次存储访问按四个字计的权重。若 ROM 读口的数据位于该段结构字节所不覆盖的块中，则每次读取都要从存储合约取一次该块，每个端口约 58,000 gas（`a = 10`、`w = 128`）；数据位于正在解析的块中时约 12,000（`a = 8`、`w = 8`）；`chunks` 预算计入每个这样的数据区域（§5.2）。

**卡带。** 带 RAM 端口的 TAPBOY-8 卡带（见参考实现）每拍一段：在一个有一次 RAM 读与一次 RAM 写的拍中需 648,550 执行 gas（只有读时为 619,161），而其 LATCH-RAM 版本的三段每段需 340 万至 550 万。它的上传分两笔交易需 1,384,398 gas（1 块），而 LATCH-RAM 版本分八笔需 22,780,576（7 块），两数都包括 calldata 与每笔交易的 21,000。它的五场分数争议在参考博弈中各方合计 228 万至 244 万 gas（包括声明与输入的公布）；每场最贵的一步是最终的 `proveSegment`，至多 764,023 gas。

**参考博弈。** 118 个端到端场景中的每一步都低于 16,777,216 gas。最终调用以外的行动至多约 409,000 gas：最大的是 `message` 样例 `K = 1` 的情形 (a) 声明（41 片叶，并从 40 个输入向量算出 `inputRoot`）；样例的声明为 220,836 至 408,992，元组承诺至多 376,215，`publishInputs` 至多 57,960，`accept` 至多 54,722，`refund` 为 59,071。场景中最大的单步是 `large` 样例的最终 `proveSegment`，4,455,909 gas。一场到达核对函数的争议，各方合计 140 万至 590 万 gas（1,417,590 至 5,856,556，包括声明、情形 (b) 中输入的公布，以及声明者获胜时的 `accept`）；因没通过检查、打不开承诺或超时而结束的争议为 46 万至 200 万。经由博弈发送时，gas 测量中最贵的最终调用（默认预算下 6,000 个 NAND 读取最深信号树中 256 个分散字的一段，见证 75,280 字节）为 10,590,813 gas，最贵的状态步为 3,533,837，含 RAM 端口最贵的一段为 6,797,695。2,048 片叶的声明为 3,356,411 gas，此前输入提供者以 `publishInputs` 公布其 65,504 个输入字节（710,216）。逐场景数字写入参考仓库的 `contracts/test/data/game_gas.csv`。

**L1 数据费（快照）。** 2026-10-18（UTC），对携带真实 calldata 的未签名 type-2 交易调用了 `GasPriceOracle` 预部署合约的 `getL1Fee`（只用 `eth_call`，未发送任何交易）。所用的链与区块是：Base 区块 52,780,049，经 `mainnet.base.org` 查询，并用 `base-rpc.publicnode.com` 交叉核对，凡两者都有应答之处结果完全相同（其余用例在稍后的区块上一致）；X Layer 区块 73,580,619，经 `rpc.xlayer.tech` 与 `xlayerrpc.okx.com` 查询。

- **Base。** 一笔 `putChunk`（一块 16,384 字节、证明 9 个哈希、calldata 16,868 字节）的 L1 数据费为 6.9 至 9.5 × 10⁻⁸ ETH：低端是 NAND 记录，可以压缩一些；高端是不可压缩的数据。见证 75,280 字节的 `verifySegment` 为 3.9 × 10⁻⁷ ETH，见证取 §6.3 上界（84,208 字节）时为 4.4 × 10⁻⁷ ETH。按当时 0.006 gwei 的 L2 gas 价格，这约为同一交易 L2 执行费用的 0.3% 至 0.7%。
- **在 Base 上传。** 上文 456 块的上传，L1 数据费约 3 至 4 × 10⁻⁵ ETH，L2 执行约 0.01 ETH。
- **X Layer。** X Layer 运行同一个预言机合约，但两个 L1 费用系数都设为 0，因此不单独收取 L1 数据费；交易只付 L2 gas 费，当时 gas 价格处于其下限 0.02 gwei（OKB）。

这些数值随以太坊的 base fee 与 blob fee 变化，测量时两者都很低（约 0.07 与 0.004 gwei）。例如 blob fee 上涨 10 倍时，Base 的 L1 数据费约增至 6.7 倍。这些数值也随链自身的费用参数变化。方法、全部用例与合计见参考仓库的 `measurements/l1fee.md`。

## 附录 C：开示输出（信息性）

声明不列出它的输出；输出由检查点叶中的输出链承诺。要从最后一片公布的叶 `L_{n−1}` 开示最后一拍（第 `T − 1` 拍）的输出，开示者给出 `stateRoot_T`、`io_{T−1}` 与输出位 `out_{T−1}`；任何人都可以重算 `outRoot_{T−1} = prefixRoot(0x04, out_{T−1}, nOut)`、`io_T = SHA-256(0x08 ‖ io_{T−1} ‖ outRoot_{T−1})` 与 `checkpointLeaf(T, stateRoot_T, io_T)`，并与 `L_{n−1}` 比较。`nOut ≤ 256` 时输出根是单个字叶，开示只需 3 次 SHA-256；`nOut` 更大时，开示者可以给出 `outRoot_{T−1}` 而不是全部输出位，再用对它的 Merkle 证明开示单个输出字。在每个检查点时刻 `t_k` 都可以同样开示，用 `io_{t_k − 1}` 与第 `t_k − 1` 拍的输出。两个检查点之间某拍的输出，还需要其后直到下一个检查点每一拍的输出根，每拍一次哈希。

开示只说明声明说了什么，不说明它是对的；应用应只采信已成立的声明（§7.1）中开示的值：其窗口已届满，没有关于它的未决争议，也没有挑战者胜的争议。声明者仅仅赢了一场争议的声明尚未成立。因此应用应把电路设计成让它关心的结果（分数、生成的 token）保持在最后一拍的输出上（由 `T` 处的叶承诺），或保持在某个检查点之前那一拍的输出上，并规定如何从输出位读出结果。卡带演示（见参考实现）在停机后把分数保持在输出引脚上，并按这种方式开示。

## 附录 D：轮数与经济层（信息性）

**轮数。** 把一轮计为一场争议的每方各走一步，或只有一方行动时的一步。第 1 层用一轮（挑战）。第 2 层用一轮承诺，加至多 `⌈log2(t_i − t_{i−1})⌉ ≤ ⌈log2 K⌉` 轮二分。第 3 层用一轮承诺（含输入）、一轮公布第 `h` 个元组，然后至多 `⌈log2 h⌉` 轮二分，或在 `h` 处元组相同时用一步公布第 `h + 1` 个元组，最后是调用。因此到达核对函数的争议至多用 `⌈log2 K⌉ + ⌈log2 h⌉ + 5` 轮，在 `h` 处元组相同时用 `⌈log2 K⌉ + 6` 轮。这里对段列表 `h` 至多为段数 `m`，对输出或状态列表至多为 `⌈nOut/256⌉` 或 `⌈nLatch/256⌉`。测试向量中到达核对函数的争议用了 6 到 11 轮；在参考博弈中，一轮就是该轮中行动的每一方各发一笔交易。同一声明的各场争议彼此独立进行，所以多个挑战者不会加长其中任何一场。

**一种经济安排。** 声明者附保证金提出声明；在挑战窗口内任何人可以附同等保证金挑战，每个挑战者各有一场争议；每一步有截止时间，错过的一方输；保证金按 §7.1 的建议逐场结算（声明者得到它击败的每个挑战者的保证金，第一个获胜的挑战者得到声明者的保证金并否决声明，其他未决争议退还保证金，成立的声明退还其保证金）。这类规则属于应用 TAP 或博弈自己的文档（见待定问题）。最终调用通常是最贵的一步，由被核对元组的所属方付费。二分之后，这总是声明者，即使作弊的是挑战者；只有在 `h` 处相同的情形下才是列表较长的一方。因此诚实声明者在对抗作弊挑战者时，可能要付出整场争议中最多的 gas，每个作弊挑战者一次。应用应确定保证金（或报销赢方的行动费用），使输方的保证金至少覆盖赢方的链上成本，包括一次达到预算上限的最终调用：在参考博弈中，`large` 样例的最终调用至多约 450 万 gas，一整场到达核对函数的争议各方合计 140 万至 590 万 gas，而实测最贵的最终调用约 1,060 万（附录 B）。按需细化使诚实声明的链下成本保持很低：声明每 `K` 拍只公布一片叶，拍叶只为被争议的检查点区间计算和公布，元组列表与输入只为被争议的那一拍计算和公布。博弈必须计入的风险列在安全考虑一节。
