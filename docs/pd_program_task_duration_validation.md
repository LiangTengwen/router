# PD Program 调度：四项任务时间目标验证（2026-10-12）

**新目标已经在原章 6 的两种长上下文负载上达到：mean、p50、p95、p99 任务耗时，三轮配对均严格优于 main。** 当前采用 cohort 配置，每个 D 最多放行 4 个活跃 Program，两个 D 合计最多 8 个；客户端仍为 16 个 Program 同时提交。

本次是调度配置优化，没有改 Rust 调度源码。被测源码仍为 e11695a，基线为 main bc16b19。旧 serial 配置的长输出 p95/p99 更差，不满足新目标。

## 新目标与验收

四项分别是 mean_task_duration_seconds、p50_task_duration_seconds、p95_task_duration_seconds、p99_task_duration_seconds。每项都必须严格小于对应轮次的 main，相等不算改善。不再要求原先 mean 至少降低 10%，TPOT、TTFT、吞吐等也不作为门槛。

公平比较仍要求成功完成相同 Program、请求和输入/输出 token 工作量。分位数从每个 Program 原始耗时样本按线性插值计算；每个场景要求 3 组完整配对，每轮四项都更好，不能只把三轮混起来看平均。

[新验收脚本](../scripts/pd_program_validation/analyze_task_duration.py)将结果保存为 task-duration-analysis.json，不覆盖旧目标记录。漏跑、重复轮次、缺少个体样本、失败/不同工作量或任意一项退化都不能通过。

## 配置优化

| 项目 | 旧 serial | 当前 cohort |
|---|---:|---:|
| 客户端 Program 并发 | 16 | 16 |
| 每个 D 活跃 Program 上限 | 1 | 4 |
| 两个 D 合计上限 | 2 | 8 |
| P Prefill 并发上限 | 4 | 8 |
| metrics 间隔 | 0.1 s | 0.02 s |
| Router 排队超时 | 300 s | 180 s |
| 强制恢复等待 | 300 s | 60 s |

两种候选都使用 Program round robin、显式 Acting TTL（前 3 轮 1 秒，最后为 0）、实测 capacity，关闭未经校准的 batch gain admission。参数联合变化，没有隔离每个参数的独立贡献。

cohort 之前因旧 TPOT 门槛未完全通过，不是没有任务时间收益。这次重新完整采集个体 JCT，不用旧缺失分位数的数据凑结论。[配置片段](../examples/configs/pd_program_task_duration.json)与实际启动参数一致，没有修改生产默认值。

## 实验条件与成功工作量

SLURM 分配 4949，同节点 3 张 H20，一 P 两 D；vLLM 0.28.0、NIXL 1.3.2、27B 混合 Attention/Mamba 模型、BF16 language-only、每引擎 8 GiB KV pool，capacity 101338 tokens。最大序列数 16；Mamba 启动参数 all、运行时报告 align，与此前两个对照保持一致。

两个场景各 16 个 Program，每个顺序做 4 轮，共 64 请求。四轮输入为 8444/8572/8700/8828 tokens；pressure 每轮输出 128、prefill-heavy 每轮输出 32。main/cohort payload 相同，每个 arm 前等待空闲并 reset prefix cache；第二轮反转运行顺序。

每次 16/16 任务、64/64 请求成功。12 个 run 共 192 个完整 Program、768/768 成功请求；每个场景每个 arm 合计 48 个任务、192 请求。完整 SSE、usage 和逐轮相同工作量校验通过。

启动前完成工作区健康检查。head 上确认的 45 个 Codex 探测/失败代理做一次有界 TERM 清理，全部退出；后续新探测记录身份，不重复清理。内存、调度器和存储正常，自有文件读写/fsync约 10 ms。GPU、端口、IPC 和节点检查均在分配内执行，未清理其他作业。

四份不同二进制哈希与真实 P→D probe 通过。probe 先访问 D0，未完全对称预热所有批次形状；三轮和反转顺序减少顺序影响，不代表消除所有热身影响或统计显著性。

## 长输入、长输出

| 指标 | main（三轮中位数） | cohort（三轮中位数） | 每轮配对降幅范围 |
|---|---:|---:|---:|
| mean_task_duration_seconds | 200.190002 s | 78.571640 s | 60.56%–62.50% |
| p50_task_duration_seconds | 200.029118 s | 91.215757 s | 54.35%–61.57% |
| p95_task_duration_seconds | 223.286335 s | 108.491810 s | 50.98%–54.21% |
| p99_task_duration_seconds | 223.745190 s | 111.808498 s | 49.80%–53.47% |

## 长输入、短输出

| 指标 | main（三轮中位数） | cohort（三轮中位数） | 每轮配对降幅范围 |
|---|---:|---:|---:|
| mean_task_duration_seconds | 195.666392 s | 58.669136 s | 69.24%–70.07% |
| p50_task_duration_seconds | 195.545787 s | 70.633691 s | 62.99%–64.08% |
| p95_task_duration_seconds | 218.791427 s | 78.894581 s | 62.55%–64.05% |
| p99_task_duration_seconds | 219.094535 s | 80.688926 s | 62.15%–63.65% |

## 每轮四项验收

| 场景 | repeat | mean 降幅 | p50 降幅 | p95 降幅 | p99 降幅 | 四项均更好 |
|---|---:|---:|---:|---:|---:|---|
| pressure | 0 | 60.56% | 54.35% | 50.98% | 49.80% | 是 |
| pressure | 1 | 62.50% | 61.57% | 54.21% | 53.47% | 是 |
| pressure | 2 | 60.74% | 54.38% | 51.41% | 50.02% | 是 |
| prefill-heavy | 0 | 70.07% | 63.88% | 63.94% | 63.17% | 是 |
| prefill-heavy | 1 | 70.02% | 64.08% | 64.05% | 63.65% | 是 |
| prefill-heavy | 2 | 69.24% | 62.99% | 62.55% | 62.15% | 是 |

代表值是单次指标的三轮中位数，验收使用逐轮数据。两种场景三轮均通过。

## 结论与边界

当前源码配合 cohort 配置，满足原章 6 两种合成负载的四项任务时间目标。每个 D 上限从 1 提到 4，避免了旧 serial 中后排任务的 p95/p99 退化。

客户端并发 16 不等于每个引擎并发 16；这里最多 8 个活跃 Program，其余仍可能在 Router 等待。当前配置 light、更多节点、持续到达率和最大稳定吞吐没有补测，不能由一个并发点推出完整可扩展性结论。

完整 30 项指标继续保留在[完整指标附录](pd_program_scheduling_validation_metrics.md)和[逐轮 CSV](pd_program_scheduling_validation_metrics.csv)。四项任务时间之外的性能指标不是当前门槛。任务尾延迟更好，不自动证明请求 TTFT、TPOT 或业务质量更好。

其他性能指标仅观察：线性 TPOT p95 长输出逐轮增加约 4.2%–5.7%，短输出增加约 8.8%–11.7%；按旧 TPOT 门槛仍不能通过，但不影响用户明确的新目标。P/D preemption、NIXL transfer failure、notification failure、KV expired 增量均为 0。输出文本与配对 main 逐字相同的请求数为长输出 20/64、15/64、22/64，短输出 60/64、62/64、63/64，业务质量等价仍未验证。

这仍是固定语料四轮负载，工具间隔为 10/40 ms，没有实际执行工具或改代码。生成文本可能不同，业务质量等价未验证。KV event 订阅关闭，范围仍为 M1 单向 P→D，不代表完成 M2–M4。

[新目标结果 JSON](pd_program_task_duration_validation_results.json)包含每轮实测值、降幅、验收条件、源码/二进制/trace/harness 来源和配置。原始证据在工作区 gpu-4949；[详细历史报告](pd_program_scheduling_validation_detailed.md)保留旧目标及失败诊断，不因目标变化而改写当时结论。

只重算新目标、不启动引擎：

    python3 scripts/pd_program_validation/analyze_task_duration.py /path/to/pd-program-validation/20261011/gpu-4949
