# Inference demo: a quantized digit classifier as one `tapnet/2` file, with disputes (informative)

[中文](#中文) · [English](#english)

> This is a demonstration on the reference implementation of the draft TAP "Large Netlists and Verifiable
> Execution". It is not a deployed service, and it makes no claim about any production model. The numbers below
> are measurements of this small circuit with the reference contracts in Foundry.

---

## English

### What it shows

A small neural network, compiled to one `tapnet/2` netlist, run for one input through the whole dispute flow of §7
of the TAP, locally:

1. **the model**: a 64-16-10 multilayer perceptron for the 8 × 8 digits of scikit-learn (`train.py`, numpy),
   quantized after training to int8 weights, 4-bit inputs (`min(pixel, 15)`), 4-bit hidden activations and 20-bit
   accumulators; the integer arithmetic is fixed in `model/weights.json` and mirrored by `mlp_model.py` (standard
   library only);
2. **the circuit**: `rtl/mlp_digits.v`, generated from the weights by `mlp_verilog.py`, converted with
   [`tools/verilog2tapnet`](../../tools/verilog2tapnet/README.md) (Yosys) into `build/mlp_digits.tapnet2`: the two
   weight tables become ROM records, the rest is NAND and LATCH; one inference is **T = 92 beats** (16 multiply-
   accumulates per beat, so a beat has about 12,900 gate-equivalents and **three canonical segments** under the
   default budget `(6000, 256, 16)`);
3. **the claim**: an honest prover runs the 92 beats on one test digit and publishes
   `(netlistHash2, T = 92, K = 8, inputRoot, checkpointRoot)` with the 13 checkpoint leaves; the predicted class is
   on the output pins of the last beat, so it is opened from the last checkpoint leaf (Appendix C of the TAP,
   `mlp_dispute.open_class`);
4. **the lies**: a cheating prover flips a gate that feeds the class register in the last ARGMAX beat (the claim
   then says class 0 instead of 1), or an adder bit of one accumulator lane in a layer-1 beat (class 2), or a bit
   of the new accumulator state; each cheater recomputes everything downstream of its lie, so the checkpoints
   differ and the dispute has to reach the verifier;
5. **the dispute**: the honest challenger names the first differing checkpoint, bisects the 8 beats, both commit the
   tuple lists of the one beat, the segment list is bisected to one segment (or the state list to one step), and
   **one** call of `verify_segment` (or `verify_beat_end`) decides, in the Python referee of `bisect_sim.py` and
   on chain in the reference game (`contracts/test/game/E2E.t.sol`, scenarios `inference/*`). The three gate lies
   land on each of the three segments of a beat.

### Numbers

From `python3 mlp_report.py` (writes `build/numbers.json`, figure-ready) and `forge test` (`contracts/`), local
run with Yosys 0.69, Foundry 1.8.3:

| Item | Value |
|---|---|
| Model | 64-16-10 MLP, ReLU; int8 weights, 4-bit inputs and hidden activations, 20-bit accumulators, hidden shift 9 |
| Data | scikit-learn digits, 1,797 images; stratified split 1,347 train / 450 test (seed 7) |
| Accuracy (float, test) | 98.89 % (train 100 %) |
| Accuracy (integer model, test) | **97.78 %** (train 100 %) |
| Netlist vs integer model | **60 of 60** test digits identical on every beat's outputs and in the class (`mlp_report.py`); 50 of 50 in `test_inference.py`; the netlist's accuracy on those 60 is 96.67 % (two digits the integer model also gets wrong) |
| File | **88,854 bytes**, **6 chunks** of 16,384; `netlistHash2` `0x561f036b1c051727b724839a77498a5592b0f324436fd971153644c426df300e` |
| Records | 12,276 NAND, 421 LATCH (417 register bits + 4 output copies), 2 ROM records (64 × 128 and 16 × 80 bits, 9,472 bits), 12,911 signals; `nIn = 4`, `nOut = 5`; no RAM ports |
| Gate cost per beat | **12,915** gate-equivalents |
| Canonical segments per beat | **3**: gates 6,000 / 6,000 / 915, extWords 3 / 18 / 7, chunks 5 / 4 / 1 |
| Beats per inference | **T = 92** (L1 beats 0–63, ACT 64, L2 65–80, ARGMAX 81–90, DONE 91); `K = 8`, 13 checkpoint leaves |
| Honest segment witnesses (every 10th beat) | segment 0: 48–120 bytes; segment 1: 1,140 bytes; segment 2: 708–776 bytes; state-list steps 2,542 and 1,422 bytes; output step 142 bytes |
| Converter | Yosys `abc -g NAND` and the `dch` script (`--best` keeps the smaller: 12,276 against 12,838 NAND); 9,060 NAND + 3,884 NOT from Yosys before the tool's own optimization |

Disputes on test digit 0 (label 1, honest class 1; Python referee and the reference game; gas per transaction
including the 21,000 intrinsic and calldata gas):

| Scenario | Result | Rounds | Decided by | Witness | Gas, all parties | Costliest move |
|---|---|---|---|---|---|---|
| A honest claim vs honest checker | no dispute, accepted | 0 | – | – | 362,959 | 252,353 (`claim`) |
| B class lie (gate, beat 90) as claimant: class 0 | cheater loses | 9 | `verify_segment(seg_2 → seg_3)` | 776 B | 2,645,757 | 992,593 (`proveSegment`) |
| C accumulator lie (gate, beat 63, lane 1) as claimant: class 2 | cheater loses | 9 | `verify_segment(seg_0 → seg_1)` | 120 B | 6,016,015 | 4,359,683 (`proveSegment`) |
| D accumulator lie (state bit, beat 63) as claimant: class 2 | cheater loses | 9 | `verify_beat_end[state](0 → 1)` | 2,542 B | 2,627,766 | 976,534 (`proveBeatEnd`) |
| E honest claim vs the cheater of B as challenger | honest party wins | 9 | `verify_segment(seg_2 → seg_3)` | 776 B | 2,644,086 | 953,288 (`proveSegment`) |
| F honest claim vs the cheater of C as challenger | honest party wins | 9 | `verify_segment(seg_0 → seg_1)` | 120 B | 6,031,456 | 4,337,478 (`proveSegment`) |
| G accumulator lie (gate, beat 63, lane 13: middle segment) as claimant: class 2 | cheater loses | 10 | `verify_segment(seg_1 → seg_2)` | 1,140 B | 6,731,163 | **4,961,813** (`proveSegment`) |

Gas per move type over the seven scenarios (`contracts/test/data/inference_gas.csv`): `publishInputs`
35,984–55,884; `claim` 235,241–252,353; `challenge` 143,552–145,664; `commitBeats` 72,014–91,544; `revealBeat`
51,810–70,252; `commitTuples` 124,991–371,476; `revealSeg` 54,344–65,979; `revealEnd` 51,770–62,155;
`proveSegment` 953,288–4,961,813; `proveBeatEnd` 976,534; `accept` 37,634–54,722. The final call on a 6,000-gate
segment costs 4.3 to 5.0 million gas, the one on the 915-gate segment about 1 million; the worst move of any
dispute is 4,961,813 gas, under the 16,777,216-gas transaction cap the game tests assume.

### How to reproduce

Everything but the first two steps needs only Python 3.9+ and the standard library (plus Foundry for the gas
figures).

```
cd demo/inference
python3 train.py                 # numpy + scikit-learn: model/weights.json, model/test_digits.json (about 5 s)
python3 mlp_verilog.py           # rtl/mlp_digits.v from the weights
python3 mlp_build.py             # yosys: build/mlp_digits.tapnet2 and build/convert.json (about 10 s)
python3 mlp_model.py             # the integer model reproduces train.py's predictions on the 450 test digits
python3 test_inference.py        # 12 tests, about 30 s (ConverterTests skipped without yosys)
python3 mlp_dispute.py [digit]   # the seven scenarios on one test digit (default 0), about 3 s
( cd ../../contracts && forge test --match-contract E2ETest )   # inference/* on chain; writes test/data/inference_gas.csv
python3 mlp_report.py [--digits N]   # numbers table and build/numbers.json (N digits compared, default 60; about 20 s)
```

`contracts/test/data/game_gen.py` includes the inference game (file, inputs and cheaters from `mlp_dispute.py`) in
`game_moves.bin`, which `E2E.t.sol` replays; `test_inference_gasPerMove` writes one row per transaction to
`contracts/test/data/inference_gas.csv`, which `mlp_report.py` reads. The committed `.tapnet2` is the file all
numbers refer to; a fresh conversion with another Yosys version may differ in bytes and gate count, and
`ConverterTests` checks that it still behaves like the integer model.

### Files

| File | Content |
|---|---|
| `train.py` | training (numpy, full-batch Adam) and post-training quantization; writes `model/` (needs numpy and scikit-learn) |
| `model/weights.json`, `model/test_digits.json` | the integer model and the test split (pixels as hex nibbles, labels, the integer model's prediction per digit) |
| `mlp_model.py` | the integer arithmetic and the beat-by-beat mirror of the circuit (registers at the start of every beat, outputs of every beat) |
| `mlp_verilog.py`, `rtl/mlp_digits.v` | the Verilog generator and the generated circuit (weights as `initial`-block memories, biases as constants) |
| `mlp_build.py`, `build/mlp_digits.tapnet2`, `build/convert.json` | the conversion with `verilog2tapnet` and its report |
| `mlp_dispute.py` | the seven dispute scenarios through the referee of `bisect_sim.py`; finds the LATCH ordinals of the registers by their value sequences and picks lies that change the class |
| `mlp_report.py`, `build/numbers.json` | measurements, witness sizes, the ≥ 50-digit comparison, the Foundry gas rows; figure-ready JSON |
| `test_inference.py` | the tests (model, netlist shape and segmentation, 50 digits beat by beat, register mapping, disputes, witnesses; a converter rebuild when yosys is present) |

### Limits

- A toy model on a small, clean dataset; post-training quantization with 4-bit activations. The accuracies above
  are for this split only. Nothing here says anything about the cost or feasibility of any other model.
- The inputs of a beat are an application choice of this demo (pixel `t` on beat `t`, zeros after); the input
  provider of the reference game publishes them (§7.1, case (b)).
- One beat does 16 multiply-accumulates in parallel; a serial design would have about twelve times fewer gates per
  beat and twelve times more beats. The segmentation, witnesses and gas depend on that choice and on the Yosys
  version.
- The lies are chosen by the demo so that the class changes; the dispute does not depend on that, only on the
  checkpoint leaves differing.
- The Python referee is informal; the on-chain game is informative and not production code; nothing is deployed
  or audited.

Licence: MIT (code); the digits are the UCI "Optical Recognition of Handwritten Digits" test images as distributed
with scikit-learn.

---

## 中文

> 这是对草案 TAP《大型网表与可验证执行》参考实现的一个演示：不是已部署的服务，也不是对任何生产模型的断言。下面的数字
> 是这个小电路在 Foundry 中用参考合约测得的。

### 演示了什么

一个小神经网络编译成一个 `tapnet/2` 网表文件，对一个输入在本地走完 TAP 第 7 节的整个争议流程：

1. **模型**：scikit-learn 8×8 手写数字的 64-16-10 多层感知机（`train.py`，numpy 训练），训练后量化为 int8 权重、4 位输入
   （`min(pixel, 15)`）、4 位隐藏层激活、20 位累加器；整数算术固定在 `model/weights.json` 中，`mlp_model.py`（只用标准库）
   逐位复现；
2. **电路**：`rtl/mlp_digits.v` 由 `mlp_verilog.py` 从权重生成，用 [`tools/verilog2tapnet`](../../tools/verilog2tapnet/README.md)
   （Yosys）转成 `build/mlp_digits.tapnet2`：两张权重表成为 ROM 记录，其余是 NAND 与 LATCH；一次推理 **T = 92 拍**
   （每拍 16 个乘累加，每拍约 12,900 门当量，默认预算 `(6000, 256, 16)` 下 **每拍 3 个规范分段**）；
3. **声明**：诚实的证明方对一个测试数字跑完 92 拍，发布 `(netlistHash2, T = 92, K = 8, inputRoot, checkpointRoot)`
   和 13 个检查点叶子；预测类别停在最后一拍的输出引脚上，从最后一个检查点叶子打开（TAP 附录 C，
   `mlp_dispute.open_class`）；
4. **谎言**：作弊的证明方在最后一个 ARGMAX 拍翻转一个送入类别寄存器的门（声明变成类别 0 而不是 1），或在第一层的某一拍
   翻转一个累加器通道的加法器输出位（类别 2），或伪造新累加器状态的一位；每个作弊者都把谎言下游的一切重新算一遍，所以
   检查点不同，争议必须走到核对函数；
5. **争议**：诚实的挑战者指出第一个不同的检查点，在 8 拍里二分，双方承诺这一拍的元组列表，分段列表二分到一段（或状态
   列表到一步），由**一次** `verify_segment`（或 `verify_beat_end`）调用裁决——在 `bisect_sim.py` 的 Python 裁判里，
   也在参考博弈合约里（`contracts/test/game/E2E.t.sol`，场景 `inference/*`）。三个门级谎言分别落在一拍的三个分段上。

### 数字

来自 `python3 mlp_report.py`（写出可直接作图的 `build/numbers.json`）与 `forge test`（`contracts/`），本地 Yosys 0.69、
Foundry 1.8.3：

| 项目 | 数值 |
|---|---|
| 模型 | 64-16-10 MLP，ReLU；int8 权重，4 位输入与隐藏激活，20 位累加器，隐藏层右移 9 |
| 数据 | scikit-learn digits，1,797 张；分层划分 1,347 训练 / 450 测试（种子 7） |
| 浮点准确率（测试） | 98.89 %（训练 100 %） |
| 整数模型准确率（测试） | **97.78 %**（训练 100 %） |
| 网表 vs 整数模型 | **60 / 60** 个测试数字每拍输出与类别完全一致（`mlp_report.py`）；`test_inference.py` 中 50 / 50；这 60 个上网表准确率 96.67 %（错的两个整数模型也错） |
| 文件 | **88,854 字节**，**6 块**（每块 16,384）；`netlistHash2` `0x561f036b1c051727b724839a77498a5592b0f324436fd971153644c426df300e` |
| 记录 | 12,276 NAND，421 LATCH（417 个寄存器位 + 4 个输出副本），2 条 ROM 记录（64×128 与 16×80 位，共 9,472 位），12,911 个信号；`nIn = 4`，`nOut = 5`；无 RAM 端口 |
| 每拍门成本 | **12,915** 门当量 |
| 每拍规范分段 | **3** 段：门 6,000 / 6,000 / 915，extWords 3 / 18 / 7，块 5 / 4 / 1 |
| 每次推理拍数 | **T = 92**（L1 第 0–63 拍，ACT 64，L2 65–80，ARGMAX 81–90，DONE 91）；`K = 8`，13 个检查点叶子 |
| 诚实分段见证（每 10 拍取样） | 第 0 段 48–120 字节；第 1 段 1,140 字节；第 2 段 708–776 字节；状态列表两步 2,542 与 1,422 字节；输出一步 142 字节 |
| 转换器 | Yosys `abc -g NAND` 与 `dch` 脚本（`--best` 取较小者：12,276 对 12,838 NAND）；Yosys 给出 9,060 NAND + 3,884 NOT，再经工具自身优化 |

对测试数字 0（标签 1，诚实类别 1）的争议（Python 裁判与参考博弈；gas 按每笔交易计，含 21,000 固有与 calldata）：

| 场景 | 结果 | 轮数 | 裁决 | 见证 | 全体 gas | 最贵一步 |
|---|---|---|---|---|---|---|
| A 诚实声明 vs 诚实核对者 | 无争议，成立 | 0 | – | – | 362,959 | 252,353（`claim`） |
| B 类别谎言（门，第 90 拍）作为声明方：类别 0 | 作弊者输 | 9 | `verify_segment(seg_2 → seg_3)` | 776 B | 2,645,757 | 992,593（`proveSegment`） |
| C 累加器谎言（门，第 63 拍，通道 1）作为声明方：类别 2 | 作弊者输 | 9 | `verify_segment(seg_0 → seg_1)` | 120 B | 6,016,015 | 4,359,683（`proveSegment`） |
| D 累加器谎言（状态位，第 63 拍）作为声明方：类别 2 | 作弊者输 | 9 | `verify_beat_end[state](0 → 1)` | 2,542 B | 2,627,766 | 976,534（`proveBeatEnd`） |
| E 诚实声明 vs B 的作弊者作为挑战者 | 诚实方赢 | 9 | `verify_segment(seg_2 → seg_3)` | 776 B | 2,644,086 | 953,288（`proveSegment`） |
| F 诚实声明 vs C 的作弊者作为挑战者 | 诚实方赢 | 9 | `verify_segment(seg_0 → seg_1)` | 120 B | 6,031,456 | 4,337,478（`proveSegment`） |
| G 累加器谎言（门，第 63 拍，通道 13：中间分段）作为声明方：类别 2 | 作弊者输 | 10 | `verify_segment(seg_1 → seg_2)` | 1,140 B | 6,731,163 | **4,961,813**（`proveSegment`） |

七个场景按步骤类型的 gas（`contracts/test/data/inference_gas.csv`）：`publishInputs` 35,984–55,884；`claim`
235,241–252,353；`challenge` 143,552–145,664；`commitBeats` 72,014–91,544；`revealBeat` 51,810–70,252；`commitTuples`
124,991–371,476；`revealSeg` 54,344–65,979；`revealEnd` 51,770–62,155；`proveSegment` 953,288–4,961,813；`proveBeatEnd`
976,534；`accept` 37,634–54,722。对 6,000 门分段的最终调用 430 万至 500 万 gas，对 915 门分段约 100 万；任何争议中最贵
的一步是 4,961,813 gas，低于博弈测试假定的 16,777,216 gas 交易上限。

### 复现

除前两步外只需 Python 3.9+ 与标准库（gas 数字需要 Foundry）。

```
cd demo/inference
python3 train.py                 # numpy + scikit-learn：model/weights.json、model/test_digits.json（约 5 秒）
python3 mlp_verilog.py           # 由权重生成 rtl/mlp_digits.v
python3 mlp_build.py             # yosys：build/mlp_digits.tapnet2 与 build/convert.json（约 10 秒）
python3 mlp_model.py             # 整数模型复现 train.py 对 450 个测试数字的预测
python3 test_inference.py        # 12 项测试，约 30 秒（无 yosys 时跳过 ConverterTests）
python3 mlp_dispute.py [digit]   # 对一个测试数字（默认 0）的七个场景，约 3 秒
( cd ../../contracts && forge test --match-contract E2ETest )   # 链上重放 inference/*；写出 test/data/inference_gas.csv
python3 mlp_report.py [--digits N]   # 数字表与 build/numbers.json（比较 N 个数字，默认 60；约 20 秒）
```

`contracts/test/data/game_gen.py` 把推理博弈（文件、输入与 `mlp_dispute.py` 的作弊者）写进 `game_moves.bin`，由
`E2E.t.sol` 重放；`test_inference_gasPerMove` 把每笔交易写成一行到 `contracts/test/data/inference_gas.csv`，
`mlp_report.py` 读取它。全部数字都以提交的 `.tapnet2` 为准；用其他 Yosys 版本重新转换，字节与门数可能不同，
`ConverterTests` 检查它仍与整数模型行为一致。

### 文件

| 文件 | 内容 |
|---|---|
| `train.py` | 训练（numpy，全批 Adam）与训练后量化；写出 `model/`（需要 numpy 与 scikit-learn） |
| `model/weights.json`、`model/test_digits.json` | 整数模型与测试集（像素为十六进制半字节、标签、整数模型对每个数字的预测） |
| `mlp_model.py` | 整数算术与电路的逐拍镜像（每拍开始时的寄存器、每拍的输出） |
| `mlp_verilog.py`、`rtl/mlp_digits.v` | Verilog 生成器与生成的电路（权重为 `initial` 块存储器，偏置为常量） |
| `mlp_build.py`、`build/mlp_digits.tapnet2`、`build/convert.json` | 用 `verilog2tapnet` 转换及其报告 |
| `mlp_dispute.py` | 经 `bisect_sim.py` 裁判的七个争议场景；按取值序列找到寄存器对应的 LATCH 序号，并选出会改变类别的谎言 |
| `mlp_report.py`、`build/numbers.json` | 测量、见证大小、≥ 50 个数字的比对、Foundry gas 行；可直接作图的 JSON |
| `test_inference.py` | 测试（模型、网表形状与分段、50 个数字逐拍、寄存器映射、争议、见证；有 yosys 时重新转换） |

### 局限

- 小模型、小而干净的数据集；训练后量化、4 位激活。上面的准确率只对这一划分成立。这里的任何数字都不说明其他模型的成本或
  可行性。
- 每拍的输入是本演示的应用约定（第 t 拍给像素 t，之后为零）；参考博弈由输入提供方发布（§7.1 情形 (b)）。
- 每拍并行做 16 个乘累加；串行设计每拍门数约少十二倍、拍数多十二倍。分段、见证与 gas 都取决于这一选择与 Yosys 版本。
- 谎言由演示选定以使类别改变；争议本身不依赖这一点，只依赖检查点叶子不同。
- Python 裁判是非正式的；链上博弈是信息性的，不是生产代码；没有任何部署或审计。

许可：MIT（代码）；数字图像来自 scikit-learn 随附的 UCI "Optical Recognition of Handwritten Digits" 测试集。
