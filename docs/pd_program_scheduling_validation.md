# PD Program 调度收益验证（2026-10-11–12）

最终分配 4927 的长上下文、四轮合成负载达到平均 Program JCT 目标：长输出逐轮降低 **31.3%–32.4%**，短输出降低 **70.7%–71.0%**，两种 TPOT p95 算法均通过门槛。长输出批次总耗时和 JCT 尾延迟变差，两个场景首响应排队都变长；这是限定场景下的平均完成时间收益。

验收以 Program 完成时间为主：在完成相同请求、输入和输出 token 工作量的条件下，三轮配对实验的平均 JCT 均至少降低 10%，且每轮 TPOT p95 不超过 main 的 1.05 倍。成功请求必须收到完整 SSE `[DONE]` 和正确 usage；失败或工作量不同的实验不能用于收益结论。

原 harness 使用 lower empirical p95。原始数据补充核查发现，临界配置在该算法下通过，在标准线性插值 p95 下仍略超门槛。因此最终验收同时要求这两种 TPOT p95 算法均通过，不把临界结果当作稳健达标。

## 版本与修复

分支 `feat/pd-program-scheduling` 基于 main `bc16b190f8875a275287dd70ce5b4c9e54373dd6`，不包含设计文档提交 `57e86174c71a9766667c22800fcfa8b00ec8c0fb`。本次验证的是 M1 单向 P→D 路径。

| 对照名 | 源码版本 | 变更/配置 |
|---|---|---|
| main | `bc16b19` | 原生 PD，逐请求 round robin |
| original | `ba6a9ff` | 初始 M1，P 并发 1，原有 Program placement 配置 |
| optimized | `3d10990` | P permit 在完整 P body 结束后释放；P 并发 8，Program round robin |
| token-aware | `e11695a` | 平面 token ID 数组按长度计费，避免把 JSON 元数据当输入 token |
| cohort | `e11695a` | 在 token-aware 上，每个 D 最多 4 个活跃 Program；显式 Acting TTL 开启，强制恢复等待 60 秒 |
| paced | `e11695a` | 与 cohort 相同，仅将 P 并发从 8 调到 4 |
| bounded | `e11695a` | 与 paced 相同，仅将每个 D 的活跃 Program 上限从 4 调到 2 |
| serial | `e11695a` | 每个 D 最多 1 个活跃 Program，P 并发 4，指标间隔 0.1 s，排队/强制恢复等待 300 s |

optimized/token-aware/cohort/paced/bounded 使用等待请求阈值 4、指标采集间隔 0.02 秒；original 和 serial 的间隔为 0.1 秒。original 的强制恢复等待为 5 秒。所有 Program 对照禁用未经校准的 batch gain admission，使用实测 D rank token capacity；除 serial 的排队/强制恢复等待为 300 秒，其余排队超时为 180 秒。显式 TTL 请求为 1 秒，最后一轮为 0；main 和其他对照也收到完全相同的请求元数据。

cohort 联合调整了活跃 Program 上限、显式短 TTL 和强制恢复等待时间。本次消融比较验证的是这组配置的整体效果，没有分别隔离这三个参数的独立贡献。

P permit 的修复来自 HTTP mock 中发现的 P/D 意外串行化。修复覆盖 sequential 和 concurrent NIXL push；完整 D body 仍决定 Program 结算。精确 token 计数仅用于非空平面 u32 数组 `/v1/completions`，其他输入保留估计器；没有把逻辑 admission 当成物理 KV 分配保证。

回归验证：739 项通过（707 library、4 launcher、18 DP、8 PD Program、2 PD）；`cargo fmt --all -- --check` 和 `git diff --check` 通过。Mock 的高并发调参结果仍约比 main 慢 5%，不能作为 GPU 收益证明。

## 实验条件

- 使用系统 SSH 和 SLURM；每次启动前记录调度器、进程、内存、存储、端口、GPU 和 IPC 状态。只管理本实验创建的进程组，不清理其他作业。
- 同节点 3 张 H20：1 个 P、2 个 D，各占一张 GPU；申请 36 CPU、320 GiB 内存。单组配对共享同一分配和持续运行的三个引擎。
- 模型配置 `Qwen3_5ForConditionalGeneration`，27B 混合 Attention/Mamba，BF16、language only；vLLM 0.28.0、Torch 2.13.0+cu130、NIXL 1.3.2、UCX。
- 引擎一致使用 eager、prefix caching、Mamba cache `all`、最大长度 16384、最大序列数 16、batched tokens 4096、每个引擎 8 GiB KV pool。实测两端 D capacity 均为 101338 tokens。
- 完整验收使用三轮重复，第二轮反转所选对照的运行顺序；每次对照开始前等待引擎空闲并 reset prefix cache。失败配置的先导轮保留为诊断，不冒充完整三轮。

| 预先选定的场景 | Programs / 客户端并发 | 每轮输入长度 | 每轮输出 | 每次运行成功工作量 |
|---|---:|---|---:|---|
| light | 8 / 4 | 2048、2176、2304、2432 | 128 | 32 请求；71680 输入、4096 输出 tokens |
| pressure | 16 / 16 | 8444、8572、8700、8828 | 128 | 64 请求；552704 输入、8192 输出 tokens |
| prefill-heavy | 16 / 16 | 同 pressure | 32 | 64 请求；552704 输入、2048 输出 tokens |

每个 Program 四轮；提示由固定 token ID 语料和各 Program 的不同前缀组成。轮间工具间隔为固定规则的 10/40 ms。JCT 包括 Router 排队、四轮请求和工具间隔；light 的执行器排队发生在 JCT 起点之前。温度 0、seed 42、ignore EOS 确保指定输出工作量。

这是一组具有历史前缀复用和 P 缓存压力的合成负载，未使用真实 AgentInfer 任务轨迹。后续轮次追加的是固定语料，而非实际生成内容。结论不能直接推广到业务质量、所有模型、长时间工具等待或所有 P/D 比例。

## 数据与验收

首次完整实验（分配 4921）说明代码修复本身未带来目标收益：

| 场景/配置 | 三轮 run-mean JCT 中位数 | 逐轮 JCT 收益 | 验收 |
|---|---:|---|---|
| light / main | 28.446 s | 基线 | — |
| light / original | 27.186 s | 3.9%–4.9% | 未达 10% |
| light / optimized | 28.690 s | -2.0%–-0.6% | 未达 10% |
| light / token-aware | 28.507 s | -0.6%–0.3% | 未达 10% |
| light / cohort | 28.450 s | -0.6%–0.6% | 未达 10% |
| pressure / main | 200.078 s | 基线 | — |
| pressure / original | 200.229 s | 约 -0.1% | 未达 10% |
| pressure / optimized | 200.309 s | 约 -0.1%–-0.2% | 未达 10% |
| pressure / token-aware | 200.347 s | 约 -0.1%–-0.2% | 未达 10% |
| pressure / cohort | 78.617 s | 60.6%–61.6% | 线性 TPOT p95 第三轮 +5.2%，未完全通过 |

cohort 的 pressure 三轮 P prefix hit 分别为 68.4%、67.4%、68.8%，main 均为 0%。D 的 NIXL 传输全部完成，失败、通知失败和 KV 过期计数增量均为 0。cohort 的传输量较小但累计 transfer time 更大：缓存复用改善 JCT 不保证解码尾延迟同时改善。首轮 TTFT p95 从 50.18 s 增至 64.85 s，而 JCT p95 从 223.15 s 降至 108.05 s；这里有首轮排队与完成时间的取舍。

短输出先导也暴露尾延迟问题。4921 的完整首轮 main JCT 为 195.767 s，cohort 为 55.197 s（降低 71.8%），但 TPOT p95 增加 16.5%（线性算法 18.1%）。在新的分配 4922 中，main JCT 为 195.625 s，paced 为 53.366 s（降低 72.7%），TPOT p95 仍增加 6.9%。这些配置未达标，后续相同配置的重复停止；未完成的配对保留为诊断。

分配 4923 中，D 上限 2 的首轮 main JCT 为 199.421 s，bounded 为 88.701 s（降低 55.5%）；TPOT p95 增加 7.7%（线性算法 8.2%），仍未达标。随后测试 D 上限 1 与 10 Hz 指标抓取的联合配置，并延长排队/强制恢复等待以容纳完整 Program 的驻留；这组变化没有分别隔离独立贡献，需同时报告首响应排队和批次总耗时。

### 最终完整配对：分配 4927

`main` 与 `serial` 在同一分配、同一持续运行的三个引擎上完成 12 次运行。每次 64/64 请求成功；每个场景每个对照共 192 请求，输入 1658112 tokens，长输出 24576、短输出 6144 tokens。总计 768/768 成功。每轮完全相同的请求元数据和 token 工作量，并核对原始 usage、Program/turn 唯一性和 16 个 Program JCT 样本。

以下时间为三轮 run-level 指标的中位数，逐轮收益与尾延迟比值按对应轮次计算。JCT p95 / TTFT p95 主表使用原 harness 的 lower quantile，另列线性结果；平均 JCT 无分位数差异。

| 场景 | main 平均 JCT | serial 平均 JCT | 三轮逐轮 JCT 降幅 | lower TPOT p95 相对变化 | 线性 TPOT p95 相对变化 | 验收 |
|---|---:|---:|---|---|---|---|
| pressure：每轮输出 128 | 199.750 s | 136.335 s | 32.4%、31.7%、31.3% | -2.31%、+0.66%、-2.59% | -2.45%、+0.77%、-2.84% | 通过 |
| prefill-heavy：每轮输出 32 | 195.494 s | 56.963 s | 70.7%、70.9%、71.0% | +1.25%、+2.91%、+1.45% | +1.14%、+2.30%、+0.75% | 通过 |

| 指标（三轮中位数） | pressure main → serial | prefill-heavy main → serial |
|---|---|---|
| 批次总耗时 / wall | 223.207 → 241.886 s | 218.966 → 101.269 s |
| Program JCT p95（lower） | 222.769 → 240.329 s | 218.462 → 100.016 s |
| 请求 TTFT p95（lower） | 49.938 → 157.441 s | 54.428 → 69.514 s |
| 请求 TTFT p95（线性） | 50.028 → 181.976 s | 54.460 → 79.097 s |
| 请求 TPOT p95（lower） | 47.831 → 46.639 ms/token | 45.311 → 45.970 ms/token |
| P native prefix hit | 0% → 70.36% | 0% → 70.36% |

长输出的 wall 逐轮增加 **7.6%–9.0%**，JCT p95 增加 **7.0%–9.1%**，lower TTFT p95 约为基线的 **3.12–3.19 倍**。短输出的 wall 降低 **53.6%–53.9%**，JCT p95 降低 **53.9%–54.4%**，但 lower TTFT p95 仍增加 **27.0%–28.2%**。平均 JCT 变好不代表批次吞吐、最慢 Program 或首响应都变好；有严格首响应期限/批次吞吐目标时，不能直接采用这个配置。

两个场景 serial 的 P cache 查询均为 552640 tokens，命中 388864 tokens；main 命中 0。P/D preemption 增量均为 0，每次运行每个 D 完成 32 次 NIXL transfer，transfer failure、notification failure、KV expired 增量均为 0。这支持该负载下 Prefill 复用的证据；活跃数、TTL、等待时间与指标间隔一起改变，不能将收益独立归因于其中一个参数。

使用测过的 [配置片段](../examples/configs/pd_program_long_context.json) 时，需配合请求中的 1 秒显式 Acting TTL（最后轮为 0）、实测 capacity 和能容纳长排队的客户端 deadline。本次所有对照的 HTTP timeout 同为 240 秒，Router serial 排队/强制恢复等待为 300 秒。没有修改生产默认值。**最终 serial 未在 light 负载中验证**，之前 light 的无收益结果属于其他配置。

原始文本与对应 main 完全相同的请求数：pressure 三轮分别 19/64、20/64、20/64；prefill-heavy 分别 59/64、58/64、60/64。协议/工作量验收不等同于业务质量等价。

可机读的 [结果与来源清单](pd_program_scheduling_validation_results.json) 包含逐轮指标、两种 TPOT 验收、缓存/传输差分、个体 JCT 样本、源码版本、二进制哈希、trace/harness 哈希及诊断实验。实际运行的 harness 与仓库 `run.py` 字节一致。原始 SSE、engine logs/metrics、健康快照和完整命令保留在工作区实验目录；未在公共仓库发布共享机器完整进程信息。分配 4926 因 Router 启动失败只形成一组完整配对及一组孤立候选，不参与最终验收。

健康检查中的短暂系统监控 D 状态经过独立 SLURM 分配诊断并记录：节点内存与存储正常，特定 monitor 的连续采样回到 S。复现脚本仅对记录中身份完全匹配的进程进行有界状态复查，持续或未知异常仍中止。后续 Router 启动出现 `AddrInUse`，监听端口 38600/38601 位于系统临时端口范围 32768–60999；完整重跑使用范围外的 18480/18481，并在分配内验证。原始端口占用的 socket 所有者未确认，失败启动和未配对结果保留为诊断，不计入最终三轮。

## 复现与局限

复现工具见 [scripts/pd_program_validation](../scripts/pd_program_validation/README.md)。使用 `analyze.py --require-linear` 检查两种 TPOT p95；脚本拒绝缺失/不完整原始请求、重复轮次、未完成或 token 工作量不同的对照。不跨 SLURM 分配拼接配对，不宣称三次重复具有统计显著性。

准备阶段发现跨 worktree 复用 Cargo target 导致错误候选二进制；这些失败尝试全部排除。正式实验使用独立 target 重新编译、四份不同 SHA-256，并在分配内重新核对哈希后启动。错误二进制、失败日志和不配对的早期 main 运行保留为诊断记录。

P 的 native prefix-cache counter 差分用于检查实际 Prefill 复用；D 的 hit/cached token 包含 connector 输入，不能当作 P 计算节省。cohort 仅限制逻辑活跃数、短暂保护轮间恢复，不提供物理 KV lease。KV event 管理模块在本次性能实验中关闭，因此不能把收益归因于 PR #326 的索引。

部分相同请求的生成文本存在差异。没有启用 batch invariance，固定 seed 并不证明不同 online batching 下逐字一致；参见 [vLLM 0.28.0 reproducibility](https://docs.vllm.ai/en/v0.28.0/usage/reproducibility/)。本次检查协议完成、usage 和完整输出工作量，同时记录文本差异；未完成业务质量等价评估，也未证明这些差异的具体原因。

M2–M4 的 D→P lease、自动 TTL、LocalAppend/TPOT 控制、Store、warm migration 和 exact KV feedback 仍未实现。本次结果只能支持测过的 M1 路径和配置。
