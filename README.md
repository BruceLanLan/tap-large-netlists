# tap-large-netlists · Large Netlists and Verifiable Execution

[中文](#中文) · [English](#english)

> **Status / 状态:** Draft TAP, not yet submitted · not deployed · reviewed internally twice, not externally audited
> 草案 TAP，尚未提交 · 未部署 · 已做两轮内部审查，未经外部审计

---

## English

### What this is

A draft TapeOut standard (TAP) and its reference implementation for netlists far larger than one transaction:

- **`tapnet/2`**, a netlist format that adds a mask-ROM record, a ROM read port and a writable memory (RAM read and write ports, committed by a fixed-depth memory tree) to the NAND and LATCH records of TAP-02;
- **`netlistHash2`**, a hash over 16,384-byte chunks, and **`ITapLargeNetlistStore`**, an optional interface that stores a file of up to 64 MiB on chain chunk by chunk;
- **commitments, a canonical segmentation and one-step verifiers** that check one segment of one beat (at most about 6,000 gate-equivalents under the default budget) against the stored file;
- **dispute rules** (claim data, data availability, several challengers, acceptance, bisection, timeouts) so that a wrong claim about a run is rejected if one party that computed correctly challenges it in time.

Use cases: verifiable AI inference (sized on the INT-C16 language-model chip, about 1.1 million gates per beat), provable game results (the TAPBOY-8 cartridge demo), large designs for TapeOut processors, and NFTs (use with the netlist-bound NFT draft, described in the informative §8 of the TAP).

The specification is [`TAPs/TAP-draft-large-netlists.md`](TAPs/TAP-draft-large-netlists.md) (English, normative); [`TAPs/TAP-draft-large-netlists.zh.md`](TAPs/TAP-draft-large-netlists.zh.md) is an informative Chinese translation. The TAP number is assigned by the TAPs editors; until then the draft is called `TAP-draft-large-netlists`.

### Layout

| Path | Content | Licence |
|---|---|---|
| `TAPs/` | The TAP draft and its translation | CC0-1.0 |
| `assets/tap-draft-large-netlists/` | Python reference implementation, vector generator, `vectors.json`, tests (the same files as in the TAPs pull request) | MIT (code), CC0 (vectors) |
| `assets/tap-02/reference.py` | Copy of the TAP-02 reference evaluator of the TAPs repository, used as an independent evaluator in the tests | MIT |
| `contracts/` | Foundry project: store, verifiers, hash library, an informative dispute game, tests and test data | CC0 (SPDX headers) |
| `demo/cartridge/` | TAPBOY-8, an 8-bit CPU with a game in ROM, as one `tapnet/2` file, with a score dispute | MIT |
| `measurements/` | L1 data fee snapshot (Base, X Layer) and the scripts that re-run the attacks found in internal review | MIT / CC0 |
| `docs/TESTING.md` | Exact commands and current results | MIT |

### Run the tests

Python 3.9+ (standard library only) and Foundry 1.8.3. `contracts/lib/forge-std` is not committed; see [`contracts/lib/README.md`](contracts/lib/README.md).

```
( cd assets/tap-draft-large-netlists && python3 make_vectors.py --check && python3 test_tapnet2.py && python3 test_ram.py )
( cd contracts && forge test )
( cd demo/cartridge && python3 test_cartridge.py )
```

Current results: 68 Python tests (50 + 18 for RAM ports), 18 cartridge tests and 89 Foundry tests (including 118 end-to-end dispute scenarios, every vector replayed and a 3,500-case differential corpus), all passing ([`docs/TESTING.md`](docs/TESTING.md)). `vectors.json` SHA-256: `b6ba365e912f149e1ab8e292588fac7f934cdb1fa1ba6d8d1b8ac3dabfd9f2d6`.

### Key figures (measured, see Appendix B of the TAP)

- Default budget `(gates, extWords, chunks) = (6000, 256, 16)`; the costliest verifier call measured takes about 9.3 million gas of execution, about 10.6 million as the final move of the reference game; witnesses are at most 84,208 bytes (segment, with or without RAM ports) and 92,810 bytes (beat end).
- RAM ports: about 25,400 gas per read port and 37,000 per write port (`a = 8`, `w = 8`); the costliest segment with RAM ports under the default budget (64 write ports, 804-byte proofs) takes about 5.9 million gas of execution.
- The TAPBOY-8 cartridge with RAM ports: 5,781 bytes, one chunk, one segment per beat of about 0.65 million gas, upload about 1.4 million gas (with LATCH-based RAM: 104,076 bytes, 7 chunks, 3 segments of up to 5.5 million gas, upload about 22.8 million); a score dispute through the reference game costs about 2.3 to 2.4 million gas for all parties together.
- Storing a 16 KiB chunk: about 3.7 million gas per transaction; a 7.5 MB file (456 chunks): about 1.67 × 10^9 gas.
- A dispute that reaches the verifier: 1.4 to 5.9 million gas for all parties together in the reference scenarios.

The TAP makes no claim about the monetary cost of uploads or disputes.

### What is not done

- Nothing is deployed, and nothing has been externally audited. The dispute game is informative and not production code.
- Open questions are listed in the TAP: ROM opcode coordination with TAP-02, a structural hash for `tapnet/2`, initial memory contents, invalidity proofs, proofs of input inconsistency.

### Contact

X [@BruceBlue](https://x.com/BruceBlue) · GitHub [@BruceLanLan](https://github.com/BruceLanLan)

---

## 中文

### 这是什么

一份 TapeOut 标准草案（TAP）及其参考实现，面向远超一笔交易容量的网表：

- **`tapnet/2`**：在 TAP-02 的 NAND 与 LATCH 记录之外增加掩模 ROM 记录、ROM 读口和可写存储器（RAM 读口与写口，由固定深度的存储树承诺）的网表格式；
- **`netlistHash2`**：对 16,384 字节分块求出的哈希；**`ITapLargeNetlistStore`**：可选接口，把至多 64 MiB 的文件逐块存上链；
- **承诺、规范分段与单步核对函数**：对照已存储的文件核对某一拍的某一段（默认预算下至多约 6,000 门当量）；
- **争议规则**（声明数据、数据可获取性、多个挑战者、声明成立、二分、超时）：只要有一个正确计算的一方及时挑战，关于一次运行的错误声明就会被否决。

用途：可验证的 AI 推理（以 INT-C16 语言模型芯片估算规模，每拍约 110 万门）、可证明的游戏结果（TAPBOY-8 卡带演示）、面向 TapeOut 处理器的大型设计，以及 NFT（与网表绑定 NFT 草案的可选配合，见 TAP 第 8 节）。

规范文本是 [`TAPs/TAP-draft-large-netlists.md`](TAPs/TAP-draft-large-netlists.md)（英文，规范性）；[`TAPs/TAP-draft-large-netlists.zh.md`](TAPs/TAP-draft-large-netlists.zh.md) 是仅供参考的中文译本。TAP 编号由 TAPs 编辑分配，在此之前草案名为 `TAP-draft-large-netlists`。

### 运行测试

需要 Python 3.9+（只用标准库）与 Foundry 1.8.3。`contracts/lib/forge-std` 不随仓库提交，见 [`contracts/lib/README.md`](contracts/lib/README.md)。命令与当前结果见 [`docs/TESTING.md`](docs/TESTING.md)：Python 67 项（其中 RAM 端口 18 项）、卡带 17 项、Foundry 84 项（包括 111 个端到端争议场景、全部向量的重放与 3,500 个用例的差分语料），全部通过。

### 尚未完成

- 没有任何部署，也没有外部审计；争议博弈是信息性的，不是生产代码。
- 待定问题见 TAP：ROM 操作码与 TAP-02 的协调、`tapnet/2` 的结构哈希、存储器初始内容、无效性证明、输入不一致证明。

### 联系

X [@BruceBlue](https://x.com/BruceBlue) · GitHub [@BruceLanLan](https://github.com/BruceLanLan)
