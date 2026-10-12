# PD Program 调度收益验证：详细报告

**当前目标已更新：mean、p50、p95、p99 任务耗时均严格优于基线，其他性能指标不作为门槛。** 分配 4949 的 cohort（每个 D 最多 4 个活跃 Program，客户端并发 16）已在两种长上下文场景各三轮通过，见[新目标报告](pd_program_task_duration_validation.md)及本报告第 14 节。第 1–13 节保留旧目标的 159-run 历史记录，第 6 节 serial 是旧目标配置；当前指标导出新增 4949，共 171 个 run、21 组对比。

记录范围：2026-10-10 至 2026-10-12。报告基于已经完成并保存的实验，不新增 GPU 实验。被测功能源码为 `e11695a`，早期版本与失败尝试也保留在报告中。

**先说结论：当前方案在“有限缓存、长上下文、四轮任务”的合成负载上，找到了平均 Program 完成时间的收益；还没有证明它适合高并发、持续流量的生产环境。** 最终通过配置每个 D 只允许 1 个活跃 Program。长输出的平均任务完成时间降低约 32%，但批次吞吐下降、后排任务和单次请求尾延迟变差；短输出平均任务完成时间降低约 71%，批次吞吐也提高，但单次请求尾延迟仍变差。

这份报告把“请求是否成功”“任务平均多久做完”“整批吞吐”“慢请求等多久”分开看。它们回答不同问题，不能只选一个好看的数字代表整个方案。

## 1. 实验想解决什么问题

原始任务是验证 [PD 分离场景下的 Program 调度](https://github.com/LiangTengwen/router/issues/1) 在 `router` 分支上的实际收益。分支 `feat/pd-program-scheduling` 基于 main `bc16b190f8875a275287dd70ce5b4c9e54373dd6`，不包含用户要求移除的设计文档提交 `57e86174c71a9766667c22800fcfa8b00ec8c0fb`。

P（Prefill）先读输入并建立生成需要的状态；D（Decode）接着生成回答。KV cache 是模型读过内容后留下的中间结果，同样的历史前缀再次出现时，缓存还在就有机会少做重复 Prefill。

Program 是一整段多轮任务，不是一次 HTTP 请求。本次真实 GPU 负载每个 Program 有四轮：请求 → 短暂工具间隔 → 请求 → 间隔 → 请求 → 间隔 → 最后一轮请求。任务 JCT 从客户端开始第一轮算起，到第四轮完整结束，包含 Router 排队和轮间间隔。

main 主要逐请求选择 D；M1 新方案增加 Program 归属、逻辑 admission 和短暂的轮间恢复保护。同一个 Program 保持 D 归属，P 仍是每轮的资源，每轮仍经过 P→D。没有用到 D→P 回传或 LocalAppend。

可以把缓存想成一张有限大的工作台。很多带着长历史的任务一起挤进来，之前留下的计算结果可能留不住。控制同时活跃的任务数，有机会让一部分任务连续完成多轮；代价是其他任务先在外面排队。实验就是要检查这个取舍有没有实际价值。

用户选择的主要目标是平均 Program 完成时间。验收要求每个场景的三轮配对都满足：完成相同的成功任务/请求和输入输出工作量；平均 JCT 至少下降 10%；请求级 TPOT p95 最多增加 5%。后来发现 p95 算法会影响临界判定，最终要求 lower 和线性插值两种算法均通过。**这个门槛没有约束 TTFT、请求 latency p95/p99 或批次吞吐，因此“通过”不等于这些指标也改善。**

## 2. 实验怎么组织，哪些条件保持一致

真实 GPU 实验使用系统 SSH 和 SLURM。同节点 3 张 H20，各一张做 P、D0、D1；申请 36 CPU 和 320 GiB 内存。每次启动/重启前保存调度器、内存、存储、进程、端口、GPU 和共享内存状态。计算节点检查在有效分配内完成，只管理实验自己创建的进程组。

模型目录中的配置架构是 `Qwen3_5ForConditionalGeneration`，27B 混合 Attention/Mamba 模型，BF16、language-only。软件是 vLLM 0.28.0、Torch 2.13.0+cu130、NIXL 1.3.2 和 UCX。不是用模型目录名字推测架构。

| 项目 | 保持一致的设置 |
|---|---|
| P/D 数量 | 1 P + 2 D；同一个配对实验不换节点或 GPU |
| KV 缓存预算 | 每个引擎 8 GiB |
| D capacity | 两个 D 实测均为 101338 tokens，提供给 Router admission |
| 最大输入长度 | 16384 |
| 引擎最大序列数 | 16；与 Router 的活跃 Program 上限是两个概念 |
| 每步 batched tokens | 4096 |
| 执行模式 | eager、prefix caching、language-only |
| Mamba cache | 启动参数为 `all`；保存的三个引擎运行时 `cache_config_info` 均报告 `align`，两个对照一致。复现时应同时核对启动参数与有效配置 |
| 请求 | token ID 数组，温度 0、seed 42、ignore EOS、流式返回 usage |
| 冷起点 | 每次 arm 开始前等引擎空闲、reset prefix cache；收益来自本次运行内的复用 |
| 配对方式 | 同一分配、同一持续运行的三个引擎，三轮重复；第二轮反转运行顺序 |
| 构建方式 | Rust/Cargo 1.98.1，dev/debug=0，相同 main Cargo.lock，独立 Cargo targets；两个对照均采用 build-only rlib 副本 |

这是 dev 构建下的部署对比，不是 release 的极限性能测量。配对配置一致让相对比较有意义，绝对数值仍受部署和构建方式影响。第一次直接 probe 先访问 D0，没有把所有连接完全对称预热；重复和交换顺序减少单次顺序影响，但不等同于严格消除所有热身影响。

三个 GPU 场景在调参前已定义：

| 场景 | Program 数 / 客户端并发 | 四轮输入 tokens | 每轮输出 tokens | 每次运行工作量 |
|---|---:|---|---:|---|
| light：轻负载 | 8 / 4 | 2048、2176、2304、2432 | 128 | 32 请求；71680 输入、4096 输出 |
| pressure：长输入、长输出 | 16 / 16 | 8444、8572、8700、8828 | 128 | 64 请求；552704 输入、8192 输出 |
| prefill-heavy：长输入、短输出 | 16 / 16 | 同 pressure | 32 | 64 请求；552704 输入、2048 输出 |

输入由固定语料 token 和 Program 的不同前缀组成，后续轮次追加固定内容，不把实际模型输出接进下一轮。轮间“工具等待”只是按固定规则 sleep 10/40 ms，没有实际搜索、改代码或执行工具。这是合成多轮负载，不是真实 AgentInfer/SWE-bench 任务。

客户端成功条件是 HTTP 200、完整 SSE `[DONE]`、无错误、usage 输入长度准确、输出达到指定数量。main 和候选收到相同元数据，前 3 轮 Acting TTL 为 1 秒、末轮为 0。候选开启显式 TTL 后才使用这种保护规则。所有 GPU 对照 HTTP timeout 同为 240 秒。

light 的客户端执行器最多同时运行 4 个 Program，任务 JCT 从执行器真正开始任务时计时，不包括执行器里的等待。pressure 和 prefill-heavy 的 16 个客户端都并发开始。这也是为什么不能把 light JCT 和长上下文 JCT 直接横向比较。

## 3. 版本和配置是怎样一步步变化的

| 对照名 | 源码 | P Prefill 并发上限 | 每个 D 活跃 Program 上限 | 显式 TTL | metrics 间隔 | Router 排队 / 强制恢复等待 |
|---|---|---:|---:|---|---:|---|
| main | bc16b19 | 原生路由 | 无此 Program admission | 不使用新保护规则 | 原生 | 原生 |
| original | ba6a9ff | 1 | 默认 64 | 关闭 | 0.1 s | 180 / 5 s |
| optimized | 3d10990 | 8 | 默认 64 | 关闭 | 0.02 s | 180 / 5 s |
| token-aware | e11695a | 8 | 默认 64 | 关闭 | 0.02 s | 180 / 5 s |
| cohort | e11695a | 8 | 4 | 开启 | 0.02 s | 180 / 60 s |
| paced | e11695a | 4 | 4 | 开启 | 0.02 s | 180 / 60 s |
| bounded | e11695a | 4 | 2 | 开启 | 0.02 s | 180 / 60 s |
| serial | e11695a | 4 | 1 | 开启 | 0.1 s | 300 / 300 s |

optimized 及后续配置使用 Program round robin 和 waiting threshold 4；各候选禁用未经校准的 batch gain admission，使用实测 capacity。serial 延长等待，是为了让后排任务能等到位置，不因过早强制恢复破坏原有 cohort；这也意味着应用 deadline 必须允许更长等待。

源码修复有两项。`3d10990` 修复 P permit 的生命周期：P 已经完成就释放 P 名额，不能继续在等 D 时占着 P 名额。它覆盖普通顺序路径和并发 NIXL push。`e11695a` 对非空平面 u32 token 数组按真实数组长度计数，避免按 JSON 大小估算导致 admission 预留过多。其他输入类型仍使用原估计路径。

配置并非一次只改一个参数。例如 cohort 同时改活跃上限、TTL 和强制恢复等待；serial 又同时改活跃上限、metrics 间隔和等待。后面的结果能证明这些联合配置的表现，不能证明某一个参数独立贡献了多少收益。

## 4. 所有已保存实验的清单

一次 run 指一个配置处理一整批 Program，不是一次请求。下面清单覆盖当前工作区已保存的 summary/raw benchmark 运行；依赖探测、直接传输 probe、构建失败和启动失败另记为能力/诊断记录，不伪装成完成的 benchmark。

| 阶段 / 分配 | 已保存 run 数 | 做了什么 | 数据使用方式 |
|---|---:|---|---|
| mock-initial，10 日 | 12 | 最初 main/candidate，客户端并发 4、16 | 有 85 次失败请求；仅诊断 |
| mock-dynamic-port，10 日 | 18 | 动态端口尝试，main/candidate/P4 | 有 146 次失败请求；失败 arm 不作收益对比 |
| mock-20261010 | 24 | main/candidate/P4/P32 | 仍有 18 次失败请求；完整成功运行可用于 Router 问题观察 |
| mock-20261011 | 30 | main/original/P permit 修复/均衡归属 P4/P8 | 全部请求成功；验证意外串行化与 Router 开销 |
| mock-tuned | 12 | P8 + round robin + admission/metrics 调整 | 全部成功；候选高并发仍略慢 |
| GPU 4916、4917 能力 probe | 不计入标准 run | 不同 KV 预算下验证真实 P→D、128 输出 | 证明单向路径能跑通，不证明调度收益 |
| GPU 4917 main 单独基线 | 1 | light，32/32 请求成功 | 没有可信候选配对；不能计算收益 |
| GPU 4921 | 37 | light 15、pressure 15、短输出 7 | light/pressure 有完整三轮；短输出部分不完整 |
| GPU 4922 | 5 | 短输出 main/cohort/paced | 只有 repeat 0 能与 main 配对，后续候选孤立 |
| GPU 4923 | 5 | pressure main/bounded | bounded 有两组配对，main 另有 repeat 2 |
| GPU 4924 | 0 | serial 重启尝试，健康检查拦截 | 模型初始化后、正式 benchmark 前中止 |
| SLURM 4925 | 0 | 独立分配诊断系统 monitor | 不是性能实验 |
| GPU 4926 | 3 | pressure main/serial | 一组完整配对；后续 Router 启动失败 |
| GPU 4927 | 12 | main/serial，两种长上下文场景各三轮 | 最终完整验收 |

完整指标导出覆盖 **159 个已保存 run**：96 个 HTTP mock、62 个正式 GPU 保存运行、1 个早期未配对 GPU 基线。96 个 mock 一共记录 12288 请求，其中 249 次失败，对应 45 个至少一轮失败的任务。62 个正式 GPU 保存运行共记录 3488/3488 成功请求、872 个完整成功 Program；最终 4927 占其中 768 请求、192 个 Program。这里的 Program 数按重复运行计数，不是 192 个不同业务任务。

这些成功计数不代表整个实验历史没有失败。没有开始 benchmark 的启动失败没有产生任务结果，计数是 N/A；已经运行但没保存完整结果的尝试也不能补成“0 失败”。逐个已保存 run 的任务/请求失败数都在 [CSV](pd_program_scheduling_validation_metrics.csv) 和 [JSON](pd_program_scheduling_validation_metrics.json) 中。

### 4.1 HTTP mock：先找代码问题

mock 使用 HTTP 服务模拟 P 和 D，通过固定 sleep 和流式响应验证 Router 行为，没有真实模型计算，也没有真实 KV 缓存。mock usage 是服务写死的计数，不能当成 GPU token 吞吐。

前面三组 mock 出现 HTTP 503、连接关闭/重置、Prefill JSON 读取失败等。部分旧记录没有保存更具体错误文本。它们揭示了当时实现/测试环境需要修正，但不是一个健康系统下的收益证明；本报告保留失败任务和请求数，也不从混有失败任务的旧平均 JCT 推算成功任务平均值。

后续 11 日的成功 mock 中，客户端并发 16 时，main 平均 Program JCT 中位数约 0.553 秒，original 约 4.675 秒，P permit 修复后约 1.299 秒，P8 + 均衡归属约 0.783 秒。再次调 waiting threshold 和 metrics 后，新方案约 0.588 秒，对应 main 约 0.559 秒，仍慢约 5%。

结论是代码修复解决了明显的调度阻塞，但 mock 没有显示普遍的 Router 速度优势。真实缓存复用收益必须到 GPU 上测，不能拿 mock 外推。

### 4.2 部署 probe 与错误构建：先确认测到的是对的东西

真实 NIXL probe 完成了 1024 输入、128 输出的 P→D 路径；保存的传输观察约 244.8 MB、10.4 ms。探测使用过 24 GiB 和 8 GiB KV 预算，正式比较统一 8 GiB。它证明当前测试版本的单向路径能跑通，不代表所有 connector 能力都验证了。

第一次候选 GPU 启动失败后，发现多个 worktree 共用了 Cargo target，候选、permit-fix、token-aware 文件实际与 main 具有相同二进制哈希。即使文件名字不同，执行的也不是预期代码。相关候选尝试全部排除，不能用来比较方案。

后来在独立 target 中重建，核对源码和四份不同哈希，每次 GPU 实验再核对一次。构建期间还处理了导出源码的 AppleDouble WIT sidecar，以及隔离环境的 OpenSSL/NIXL 加载依赖。没有修改共享 Python 环境来强行消除问题。

早期 main 单独 light 运行平均 JCT 29.511 秒、wall 60.108 秒，32 请求全部成功；由于缺少可信候选配对，它只作为部署记录，不能与其他分配的候选拼接计算收益。

### 4.3 GPU 4921：初始代码修复没有直接产生目标收益

light 中 main 约 28.446 秒，original 约 27.186 秒，改善不到 5%；optimized、token-aware、cohort 约 28.45–28.69 秒，也未达到 10%。**最终 serial 没有在 light 上测过，不能把这些结果当成它的轻负载结果。**

pressure 中 main 约 200.078 秒，original/optimized/token-aware 仍约 200 秒。仅修复代码和提高 P 并发，没有解决这组多轮长历史的瓶颈。

cohort 在 pressure 中平均 JCT 降到约 78.617 秒，三轮改善 60.6%–61.6%，P prefix hit 约 67%–69%，说明联合配置确实改变了缓存复用和完成时间。但线性 TPOT p95 第三轮变差 5.22%，未完全通过。原 lower 算法曾让这个临界配置看起来通过，最终补核线性算法后收紧结论。

短输出首轮 cohort 的 JCT 改善约 71.8%，但线性 TPOT p95 变差约 18.1%。实验在分配时限附近停止，后续数据不完整；不能把孤立候选轮拿来凑三轮。

### 4.4 GPU 4922、4923：减少并发，仍存在 TPOT 问题

4922 新分配里，短输出 main 首轮 JCT 195.625 秒，cohort 56.528 秒，paced 53.366 秒。平均任务确实提前完成，但线性 TPOT 分别变差 5.25% 和 6.88%。后续候选记录没有对应 main，保留为诊断。

4923 把每个 D 活跃 Program 上限降到 2，bounded 两组配对平均 JCT 改善约 55.5% 和 55.9%，线性 TPOT 变差约 8.20% 和 8.45%。因此它并非“没有收益”，而是没有同时满足两个目标；也没有完成三组候选配对。

这些阶段改变的参数并不完全一样，不能只根据结果对 Program 并发画出严格的因果曲线。

### 4.5 GPU 4924–4926：处理环境问题，不把不完整数据算作成功

4924 的模型初始化已推进，但 Router benchmark 尚未开始，节点健康检查发现系统 monitor 的 D 状态。D 表示进程正在不可中断等待，不是直接证明 Router 或模型崩溃。4925 在有效分配内诊断到内存、存储正常，身份明确的 monitor 连续采样回到 S/R。

复现脚本只对预先记录且身份完全匹配的 monitor 做有界复查；未知或持续异常仍中止，不终止系统服务。单独确认的 Codex 探测残留若没有造成实际资源问题，按工作区规则不反复清理或取消实验。

4926 的 serial 首轮平均 JCT 改善约 32%，线性 TPOT 增加 4.21%，单轮满足目标。但是后续 main Router 启动遇到 `AddrInUse`：端口不能绑定。38600/38601 位于系统临时端口范围，确切 socket 所有者未确认。改用范围外的 18480/18481 后，重新完整配对。4926 一组完整配对和一组孤立候选，不属于最终三轮验收。

### 4.6 GPU 4927：最终完整对比

4927 main 与 serial 在同一分配完成两个场景的三轮配对，12 次运行全部成功。每次 16 个任务、64 请求，输入 552704 tokens；长输出 8192、短输出 2048 tokens。核对了 raw 请求计数、usage、Program/turn 唯一性以及 16 个任务耗时样本。

长输出平均 JCT 三轮分别改善 32.36%、31.74%、31.34%；短输出分别改善 70.75%、70.87%、70.98%。两种 TPOT p95 算法均通过门槛，且所有请求完整结束。下面先把指标口径说清楚，再列出完整表格。

## 5. 30 项指标怎么理解，怎样计算

“task” 在本报告里指一个完整 Program，“request” 指其中一轮 HTTP 请求。成功任务要求它的所有预定轮次都成功；有一轮失败就记为失败任务。这是协议完成，不等于业务任务被正确解决。

`tasks_with_patch` 暂按“产生代码补丁的任务数”理解。当前合成负载没有改代码，也未采集 patch，所以所有表格为 **N/A**；不能把使用修改版 Router 的 Program 数冒充“产出了补丁”的任务数。CSV/JSON 同样用空值/null，而不是 0。若采用“运行在修改版 Router 上的任务数”的口径，导出脚本支持显式切换，必须在报告中同步声明。

| 字段 | 通俗解释 |
|---|---|
| `completed_tasks` | 四轮/八轮全部成功的 Program 数 |
| `failed_tasks` | 至少一轮失败的 Program 数 |
| `tasks_with_patch` | 生成代码补丁的任务数；本实验不适用 |
| `mean_task_duration_seconds` | 成功 Program 的平均完成时间 |
| `p50_task_duration_seconds` | 成功 Program 完成时间 p50 |
| `p95_task_duration_seconds` | 成功 Program 完成时间 p95 |
| `p99_task_duration_seconds` | 成功 Program 完成时间 p99 |
| `run_wall_time_seconds` | 整批从提交到全部结束的用时 |
| `request_throughput_per_second` | 成功请求数 / 整批用时 |
| `input_token_throughput_per_second` | 成功请求输入 tokens / 整批用时 |
| `output_token_throughput_per_second` | 成功请求输出 tokens / 整批用时 |
| `requests` | 已记录的客户端请求数 |
| `successful_requests` | 成功客户端请求数 |
| `failed_requests` | 失败客户端请求数 |
| `input_tokens` | 成功请求 usage 输入 tokens 总数 |
| `output_tokens` | 成功请求 usage 输出 tokens 总数 |
| `prefix_cache_hit_rate` | P native prefix 命中 tokens / 查询 tokens |
| `prompt_token_hit_rate` | P cached prompt tokens / 客户端 usage 输入 tokens |
| `latency_mean_seconds` | 成功单次请求端到端耗时均值 |
| `latency_p50_seconds` | 成功单次请求端到端耗时 p50 |
| `latency_p95_seconds` | 成功单次请求端到端耗时 p95 |
| `latency_p99_seconds` | 成功单次请求端到端耗时 p99 |
| `ttft_mean_seconds` | 成功单次请求首个内容输出等待均值 |
| `ttft_p50_seconds` | 成功单次请求首个内容输出等待 p50 |
| `ttft_p95_seconds` | 成功单次请求首个内容输出等待 p95 |
| `ttft_p99_seconds` | 成功单次请求首个内容输出等待 p99 |
| `vllm_queue_time_mean_seconds` | P 与所选 D 的引擎 WAITING 统计均值之和 |
| `vllm_prefill_time_mean_seconds` | P 与所选 D 的引擎 PREFILL 统计均值之和 |
| `vllm_decode_time_mean_seconds` | P 与所选 D 的引擎 DECODE 统计均值之和 |
| `vllm_inference_time_mean_seconds` | P 与所选 D 的引擎 RUNNING 统计均值之和 |

### 5.1 时间、分位数和重复统计

请求 latency 是客户端从发出本轮请求到完整结束的时间，含 Router 等待、引擎等待、P/D 服务、传输及 HTTP 开销。TTFT 是到第一个有内容的 SSE 输出的时间，不只是收到 HTTP headers。

平均任务时间只统计完整成功任务。旧 mock 若混有失败任务且没保存个体 JCT，旧 summary 的平均值包含失败任务，无法准确补算成功任务均值，表中标 N/A。它的原始全任务均值另保存在 JSON，不能拿来当成功任务收益。

本次新增表格的 p50/p95/p99 统一使用线性插值：排序后位置为 `(n-1)×q`，必要时在两个样本之间插值。早期 4921 和全部 mock 没保存逐个 Program JCT，所以任务 p50/p95/p99 标 N/A；原始 lower p95 保留为附加字段。最终 4927 保存了个体样本，因此这些指标可以准确按统一算法重算。

**新表里的 p95 与简版旧表可能略有差异，原因是算法明确统一了，原始数据没有变化。** 请求延迟和 TTFT 的个体数据早期也保存了，可以统一补算。每次只有 16 个任务，p99 更接近最慢样本，不能当成长时间服务的稳定尾延迟或统计显著性证据。

下面对比表每列为各自已保存 run 的指标中位数，n 表示 run 数。`completed_tasks=16`、`requests=64` 仍是单次运行的代表计数，不是把三次合起来；最终每个场景每个 arm 的实际总数为 48 个任务、192 请求。逐轮结果和真实总计数见 CSV/JSON。

### 5.2 吞吐的分母必须用整批耗时

请求吞吐 = 成功客户端请求数 / wall；输入输出吞吐分别为成功 usage token 总数 / wall。不能用平均任务 JCT 做分母，也不能把 P 和 D 的 prompt 计数都加起来当业务输入。三个吞吐都包含任务排队和工具间隔，不包含模型初始化、Router 启动和 cache reset；它们是这批有限任务的吞吐，不是持续流量下的最大稳定吞吐。

### 5.3 两种缓存命中率为什么很接近，却不完全相同

`prefix_cache_hit_rate` 使用 **P 端**本次运行的 native prefix hit counter 差分 / query counter 差分；这是 token 加权命中率，不是“多少请求命中了缓存”。`prompt_token_hit_rate` 使用 P `prompt_tokens_cached_total` 差分 / 成功客户端 usage 输入 tokens。

最终 serial 的分子均为 388864；prefix 查询分母为 552640，客户端 usage 分母为 552704，所以分别约 70.3648% 和 70.3566%。本部署两个计数边界相差每请求 1 token；报告保留两个分母，不强行把它们写成同一个值。

**D 端的 cached prompt 包含从 P 传来的 KV。** 即使 main 的 P 没有复用历史，D 接到已算好的 KV 也会显示高 cached token 比例。这不能证明 Prefill 省了多少，所以主表缓存率统一用 P；P/D 分角色原始差分另保存在 JSON。

### 5.4 vLLM 四项平均时间是引擎内统计，不含 Router 队列

每个引擎保存了 before-run 和 after-run Prometheus metrics。每项平均时间由 histogram 的 `_sum`、`_count` 差分计算，先核对 P 的 count 为客户端请求数 N、两个 D 的 count 总和也为 N。原始名称和含义可核对 [vLLM 0.28.0 Production Metrics](https://docs.vllm.ai/en/v0.28.0/usage/metrics/)：queue 对应 WAITING，prefill/decode 对应各阶段，inference 对应 RUNNING。

主表采用每条客户端请求的两阶段统计和：

```text
vllm_phase_mean = (P 阶段时间总和 + D0 总和 + D1 总和) / N
```

不能直接把三台引擎的平均数相加，也不能把分母写成 2N，否则会把每条 PD 请求的两阶段时间错误减半。D0/D1 必须按实际请求数加权；original 的分布并不总是各占一半。

这些数字是阶段的累计观察时长，不是 GPU 纯计算时间，也不是精确串行的端到端耗时拆解。不同 PD 路径可能有传输等待或重叠，inference 不应被当成额外阶段再加到 prefill/decode 上。JSON 保存了 P、D0、D1 的 count、sum 和 mean，可重新按其他口径分析。

**Router 外面的排队不在 vLLM queue 指标里。** 因此 vLLM queue 大幅下降，不能解释成用户排队消失了：有些等待只是提前到 Router 层。我们没有保存逐请求 Router admission 的独立等待时间，不能用几个不同统计值相减，假装已经精确测出 Router queue。

## 6. 最终完整指标对比

### 6.1 长输入、长输出：pressure（旧目标 serial）

| 对比项 | main（n=3） | serial（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 199.750293 | 136.335160 |
| p50_task_duration_seconds | 199.707410 | 136.640220 |
| p95_task_duration_seconds | 222.876124 | 240.715949 |
| p99_task_duration_seconds | 223.133064 | 241.645061 |
| run_wall_time_seconds | 223.207386 | 241.885719 |
| request_throughput_per_second | 0.286729 | 0.264588 |
| input_token_throughput_per_second | 2476.190460 | 2284.979879 |
| output_token_throughput_per_second | 36.701294 | 33.867233 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 49.922509 | 34.068726 |
| latency_p50_seconds | 53.840119 | 6.282883 |
| latency_p95_seconds | 55.773593 | 187.647500 |
| latency_p99_seconds | 59.429849 | 222.274500 |
| ttft_mean_seconds | 44.127104 | 28.250519 |
| ttft_p50_seconds | 48.050852 | 0.481936 |
| ttft_p95_seconds | 50.027693 | 181.975730 |
| ttft_p99_seconds | 53.623111 | 216.460540 |
| vllm_queue_time_mean_seconds | 35.909495 | 0.046203 |
| vllm_prefill_time_mean_seconds | 7.645255 | 1.512988 |
| vllm_decode_time_mean_seconds | 5.828562 | 5.801336 |
| vllm_inference_time_mean_seconds | 13.487374 | 7.314324 |

各列包含的 repeat：main=0,1,2；serial=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| serial | 48 | 0 | 192 | 0 |

### 6.2 长输入、短输出：prefill-heavy（旧目标 serial）

| 对比项 | main（n=3） | serial（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 195.494442 | 56.963172 |
| p50_task_duration_seconds | 195.387020 | 57.033409 |
| p95_task_duration_seconds | 218.585473 | 100.326657 |
| p99_task_duration_seconds | 218.881052 | 101.072612 |
| run_wall_time_seconds | 218.965573 | 101.268772 |
| request_throughput_per_second | 0.292283 | 0.631982 |
| input_token_throughput_per_second | 2524.159354 | 5457.793027 |
| output_token_throughput_per_second | 9.353068 | 20.223411 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 48.858546 | 14.225732 |
| latency_p50_seconds | 53.786539 | 1.874582 |
| latency_p95_seconds | 55.825590 | 80.453306 |
| latency_p99_seconds | 56.354646 | 95.073921 |
| ttft_mean_seconds | 47.479001 | 12.831201 |
| ttft_p50_seconds | 52.395870 | 0.477791 |
| ttft_p95_seconds | 54.459691 | 79.096620 |
| ttft_p99_seconds | 54.988261 | 93.703320 |
| vllm_queue_time_mean_seconds | 39.058911 | 0.029705 |
| vllm_prefill_time_mean_seconds | 7.652828 | 1.511376 |
| vllm_decode_time_mean_seconds | 1.382180 | 1.390881 |
| vllm_inference_time_mean_seconds | 9.036292 | 2.902257 |

各列包含的 repeat：main=0,1,2；serial=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| serial | 48 | 0 | 192 | 0 |

每次都是 16 个完整成功任务、0 个失败任务、64 个成功请求。`tasks_with_patch=N/A` 的原因见指标说明。全部历史配置也有同样 30 项字段的对比表，见[完整指标附录](pd_program_scheduling_validation_metrics.md)；逐轮 CSV 不做中位数合并。

## 7. 如何解读结果：平均变快，尾部为什么变慢

### 7.1 长输出：平均 JCT 改善，吞吐和慢请求有代价

三轮代表平均任务时间 199.750 → 136.335 秒，下降约 32%。但 wall 223.207 → 241.886 秒，每秒成功请求数 0.286729 → 0.264588。逐轮 wall 增加 7.6%–9.0%，对应吞吐下降约 7%–8%，并不是吞吐改善。

统一线性算法的任务 p95 约 222.876 → 240.716 秒。请求 latency p95 约 55.774 → 187.648 秒、p99 约 59.430 → 222.275 秒；TTFT p95 约 50.028 → 181.976 秒。这个配置对首响应期限严格的流式交互有明显代价，不能只看平均 JCT 就直接采用。

### 7.2 短输出：批次结束更早，但单次慢请求仍变慢

平均任务时间 195.494 → 56.963 秒，下降约 71%；wall 218.966 → 101.269 秒，下降约 54%；请求吞吐 0.292283 → 0.631982，请求/输入/输出批次吞吐约为原来的 2.16 倍。

任务 p95 约 218.585 → 100.327 秒，也明显改善。不过请求 latency p95 约 55.826 → 80.453 秒、p99 约 56.355 → 95.074 秒；TTFT p95 54.460 → 79.097 秒。**整个四轮任务尾部变好，不代表某一轮请求的尾部等待也变好。**

### 7.3 第一轮真实任务分布解释了这个现象

长输出 main 的 16 个任务都比较晚完成，JCT 约 174–224 秒。serial 让少数任务先连续做完，再放入下一批：

| 按 JCT 从短到长排序的任务组 | serial 第一轮真实任务 JCT |
|---|---:|
| 最先完成两个 | 30.10、31.63 秒 |
| 接下来两个 | 59.96、61.78 秒 |
| 第三组 | 89.82、91.57 秒 |
| 第四组 | 119.49、121.45 秒 |
| 第五组 | 149.29、151.24 秒 |
| 第六组 | 179.09、180.89 秒 |
| 第七组 | 208.88、210.63 秒 |
| 最后两个 | 238.61、240.50 秒 |

早完成的任务把平均值拉下来，最后面的任务则需要等位置。这里的 JCT 已经包括 Router 等待，未把排队偷偷扣掉。长输出最后两个比 main 更晚；短输出每组更短，首批约 13 秒、最后一批约 101 秒，整批也能更早结束。

serial 单次请求 p50 latency 很低，长输出约 6.28 秒，TTFT p50 约 0.48 秒；但 p95 很高。这意味着大部分后续轮次很快，少数第一次请求在队列里等了很久。只看中位数会漏掉这些等待，只看平均数又会漏掉尾部，因此报告把这些值全部列出。

### 7.4 引擎指标说明了缓存收益，也说明了等待位置变化

最终 serial 的 P prefix hit 约 70.36%，main 为 0%；两种场景 P cached prompt 差分均为 388864 tokens。PD Prefill 阶段平均观察时长长输出约 7.645 → 1.513 秒，短输出约 7.653 → 1.511 秒。这支持“该负载下重复 Prefill 减少”的解释，但不是对 GPU FLOPs 节省比例的测量。

与此同时，引擎 queue mean 长输出约 35.909 → 0.046 秒，短输出约 39.059 → 0.030 秒。请求 TTFT p95 却增长。结合 Router 的并发限制和任务完成分布，这与等待转移到 Router 放行之前的行为一致，而这段时间尚未进入 vLLM 的 WAITING 统计。由于没有独立 Router queue 样本，具体占比未量化；不能把所有收益或所有尾延迟变化都归因于缓存。

最终 12 个运行的 P/D preemption 增量均为 0；每次每个 D 完成 32 次 NIXL transfer，P/D transfer failure、notification failure、KV expired 增量均为 0。cohort 较早实验中传输量下降但累计 transfer time 曾变大，说明不能把“传输更少”直接等同于“传输总耗时更短”。逐角色传输字节和累计时间可在结果 JSON 中核对。

## 8. 所有 GPU 配对结论汇总

以下仅比较同一分配、同一 profile、同一 repeat 的 main，不跨分配拼接。正 JCT 降幅表示变快；正 TPOT 变化表示变慢。诊断阶段不满足三轮数量，即使单轮满足门槛也不算完整验收。

| 分配 / 场景 | 配置 | 可配对 repeat | 平均 JCT 逐轮降幅 | 线性 TPOT p95 逐轮变化 | 判定 |
|---|---|---|---|---|---|
| 4921 / light | original | 0, 1, 2 | 4.40%, 4.93%, 3.94% | +1.22%, +0.29%, +0.98% | 未达目标 |
| 4921 / light | optimized | 0, 1, 2 | -0.62%, -0.86%, -2.02% | +0.85%, +1.42%, -0.04% | 未达目标 |
| 4921 / light | token-aware | 0, 1, 2 | -0.17%, -0.56%, 0.26% | +0.23%, +1.02%, +3.23% | 未达目标 |
| 4921 / light | cohort | 0, 1, 2 | -0.61%, -0.01%, 0.58% | +1.35%, +2.12%, -0.03% | 未达目标 |
| 4921 / pressure | original | 0, 1, 2 | -0.03%, -0.10%, -0.06% | +2.02%, +1.46%, +2.85% | 未达目标 |
| 4921 / pressure | optimized | 0, 1, 2 | -0.05%, -0.15%, -0.12% | +2.80%, +3.18%, +3.65% | 未达目标 |
| 4921 / pressure | token-aware | 0, 1, 2 | -0.07%, -0.17%, -0.13% | +2.83%, +2.84%, +3.51% | 未达目标 |
| 4921 / pressure | cohort | 0, 1, 2 | 60.72%, 60.61%, 61.60% | +3.81%, +2.40%, +5.22% | 未达目标 |
| 4921 / prefill-heavy | original | 0 | -0.07% | -2.14% | 先导 / 不完整；不作完整验收 |
| 4921 / prefill-heavy | optimized | 0 | 0.00% | +4.66% | 先导 / 不完整；不作完整验收 |
| 4921 / prefill-heavy | token-aware | 0 | -0.01% | +1.90% | 先导 / 不完整；不作完整验收 |
| 4921 / prefill-heavy | cohort | 0 | 71.80% | +18.07% | 先导 / 不完整；不作完整验收 |
| 4922 / prefill-heavy | cohort | 0 | 71.10% | +5.25% | 先导 / 不完整；不作完整验收 |
| 4922 / prefill-heavy | paced | 0 | 72.72% | +6.89% | 先导 / 不完整；不作完整验收 |
| 4923 / pressure | bounded | 0, 1 | 55.52%, 55.94% | +8.20%, +8.45% | 先导 / 不完整；不作完整验收 |
| 4926 / pressure | serial | 0 | 31.99% | +4.21% | 先导 / 不完整；不作完整验收 |
| 4927 / pressure | serial | 0, 1, 2 | 32.36%, 31.74%, 31.34% | -2.45%, +0.77%, -2.84% | 通过两种 TPOT 门槛 |
| 4927 / prefill-heavy | serial | 0, 1, 2 | 70.75%, 70.87%, 70.98% | +1.14%, +2.30%, +0.75% | 通过两种 TPOT 门槛 |

表中较高 Program 并发并不是没有平均 JCT 收益，而是未同时守住 TPOT 门槛。完整原始 lower TPOT、线性 TPOT 和失败/缺失配对标记均保存在数据文件中。

## 9. “每个 D 并发 1”对可扩展性意味着什么

最终限制的是 Router 活跃 Program 数：D0 一个、D1 一个，P Prefill 上限为 4。16 个客户端可以同时提交，但通常只有两个 Program 同时推进，其余排队。它与引擎配置 `max-num-seqs=16` 不是同一个参数。

**当前通过配置偏保守，还不能作为高并发可扩展性的证明。** 对一个 P、两个 D 来说，只放两个 Program 会减少历史竞争，却也可能损失批处理机会。长输出的实际批次吞吐已经下降；这个“可能损失批处理”的贡献尚未单独隔离。

目前不能得出“并发超过 1 就完全没有收益”，因为 2、4 的 JCT 改善已经测到；也不能得出“方案本身无法扩展”，因为没有增加节点/GPU 做扩展曲线。但我们确实还没证明在较高活跃并发下能同时改善任务 JCT、吞吐和响应尾延迟。

所有最终任务都是一次提交的有限批次，并非持续到达的流量。固定队列最后总能清空，不能由此证明持续负载下队列不会越积越长。增加 D 数量是否有效，还受 P、KV 预算和传输影响，本次没有证据回答。

## 10. 输出文本和任务质量没有被证明等价

输入、输出 token 数相同只是工作量一致，不是语义质量一致。最终 pressure 三轮输出与配对 main 逐字相同的请求数为 19/64、20/64、20/64；短输出为 59/64、58/64、60/64。

本次未启用 batch invariance。vLLM 的[版本化 reproducibility 文档](https://docs.vllm.ai/en/v0.28.0/usage/reproducibility/)说明默认在线服务不保证跨调度逐字可复现。固定 seed/温度不能替代业务评估；这也不证明本次文本差异的具体原因。

因此 `completed_tasks` 只能说明四轮协议都成功，不能解释为正确解决业务任务；`tasks_with_patch` 不适用，更不能与 SWE-bench 的 patch 或 resolved task 数横向比较。

## 11. 当前实施范围与功能测试

性能实验验证的是 M1 单向 P→D、逻辑容量 admission、Program D 归属和显式短 TTL。没有实现或测出 M2–M4 的 D→P lease、自动 TTL、LocalAppend/TPOT 控制、Store、warm migration 和 exact KV feedback。

[上游 issue #320](https://github.com/vllm-project/router/issues/320) 与 [PR #326](https://github.com/vllm-project/router/pull/326) 提供 KV 驻留事件索引基础，已复用到分支并集成生命周期。它们不直接提供完整 PD 调度。**本次 GPU 性能实验关闭 KV event 订阅，因此收益不能归因于该索引。**

源码 `e11695a` 的回归验证共 739 项通过：707 library、4 launcher、18 DP、8 PD Program、2 PD；格式和 diff 检查通过。这支持已覆盖功能的正确性，不证明业务质量、生产稳定性或 GPU 吞吐。已有测试覆盖 P/D permit 边界、token 数组计数、取消、流式完整结束和 D 归属等。

## 12. 当前结论，以及仍需要补什么证据

当前可以支持的结论是：在被测部署和合成负载上，控制活跃 Program 并配合短 TTL、归属和等待规则，能够提高 P 历史复用，降低平均 Program JCT；最终配置满足预定成功工作量和 TPOT 门槛。短输出还改善这批任务的总耗时与吞吐。

当前不能支持的结论是：所有场景、模型和 PD 比例都会受益；高并发吞吐改善；首响应或每次请求的尾延迟不退化；真实 Agent 质量等价；整份 RFC 已完成。最终 serial 轻负载没测，三次重复也不足以宣称统计显著性或长期稳定性。

下一步验证应固定其他参数，单独扫每个 D 活跃 Program 上限 1/2/4/8，再扫客户端并发与持续到达率。每个配置都要看任务 JCT、请求 p95/p99、TTFT、TPOT、吞吐、超时/失败和队列是否增长。逐请求 Router queue 需要额外采集，业务负载需要真实任务完成、patch 和质量指标。不同 KV 预算、更长工具间隔、不同输出长度和更多 P/D 节点也需要独立实验。

这些是后续证据需求，本次没有新增实验，不把它们写成已经达成。当前 serial 应被视作平均 JCT 目标下的保守实验配置，不能直接当成最终生产默认。

## 13. 数据文件、来源和复现

- [简版报告](pd_program_scheduling_validation.md)：主要结论和原始验收口径。
- [全部 30 项对比表](pd_program_scheduling_validation_metrics.md)：19 组历史/最终对比，标明各 arm 的重复集合及 N/A。
- [逐轮 CSV](pd_program_scheduling_validation_metrics.csv)：159 个已保存 run，可直接筛选配置/分配/轮次。
- [逐轮指标 JSON](pd_program_scheduling_validation_metrics.json)：统计值、缺失原因、分角色引擎统计、raw/metrics 来源哈希。
- [原始验收与来源清单](pd_program_scheduling_validation_results.json)：被测源码、Linux 二进制哈希、trace/harness 哈希、配对 TPOT 判定和缓存/传输差分。
- [测过的 serial 配置片段](../examples/configs/pd_program_long_context.json)：不是完整部署文件，capacity 和应用 deadline 必须按实际部署核对。

原始 SSE、usage、Program JCT、engine metrics/logs、命令和健康快照保存在工作区 `pd-program-validation/20261010` 与 `20261011`。公共仓库只发布必要汇总，不发布共享机器完整进程列表、账号路径等信息。最终真实运行的 `harness.py` 与仓库 `run.py` 字节一致；没有使用名字相同但来源不明的候选二进制。

只重算报告，不启动 GPU：

```sh
python3 scripts/pd_program_validation/report_metrics.py /path/to/pd-program-validation --output-dir /path/to/report-output
```

最终原始验收复核：

```sh
python3 scripts/pd_program_validation/analyze.py /path/to/pd-program-validation/20261011/gpu-4927 --require-linear
python3 scripts/pd_program_validation/inspect.py /path/to/pd-program-validation/20261011/gpu-4927
```

`report_metrics.py` 输出 CSV、JSON 和完整指标附录；本详细报告中的解释性文字人工整理，最终两张完整指标表与附录一致。该脚本遇到计数、usage、个体 JCT 均值或引擎 histogram 计数不一致会中止。未采集的量保留 null/N/A，不从不完整数据构造收益。

## 14. 新目标：四项任务耗时均更好（2026-10-12 更新）

**新目标已经在原章 6 的两种长上下文负载上达到：mean、p50、p95、p99 任务耗时，三轮配对均严格优于 main。** 当前采用 cohort 配置，每个 D 最多放行 4 个活跃 Program，两个 D 合计最多 8 个；客户端仍为 16 个 Program 同时提交。

本次是调度配置优化，没有改 Rust 调度源码。被测源码仍为 e11695a，基线为 main bc16b19。旧 serial 配置的长输出 p95/p99 更差，不满足新目标。

### 新目标与验收

四项分别是 mean_task_duration_seconds、p50_task_duration_seconds、p95_task_duration_seconds、p99_task_duration_seconds。每项都必须严格小于对应轮次的 main，相等不算改善。不再要求原先 mean 至少降低 10%，TPOT、TTFT、吞吐等也不作为门槛。

公平比较仍要求成功完成相同 Program、请求和输入/输出 token 工作量。分位数从每个 Program 原始耗时样本按线性插值计算；每个场景要求 3 组完整配对，每轮四项都更好，不能只把三轮混起来看平均。

[新验收脚本](../scripts/pd_program_validation/analyze_task_duration.py)将结果保存为 task-duration-analysis.json，不覆盖旧目标记录。漏跑、重复轮次、缺少个体样本、失败/不同工作量或任意一项退化都不能通过。

### 配置优化

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

### 实验条件与成功工作量

SLURM 分配 4949，同节点 3 张 H20，一 P 两 D；vLLM 0.28.0、NIXL 1.3.2、27B 混合 Attention/Mamba 模型、BF16 language-only、每引擎 8 GiB KV pool，capacity 101338 tokens。最大序列数 16；Mamba 启动参数 all、运行时报告 align，与此前两个对照保持一致。

两个场景各 16 个 Program，每个顺序做 4 轮，共 64 请求。四轮输入为 8444/8572/8700/8828 tokens；pressure 每轮输出 128、prefill-heavy 每轮输出 32。main/cohort payload 相同，每个 arm 前等待空闲并 reset prefix cache；第二轮反转运行顺序。

每次 16/16 任务、64/64 请求成功。12 个 run 共 192 个完整 Program、768/768 成功请求；每个场景每个 arm 合计 48 个任务、192 请求。完整 SSE、usage 和逐轮相同工作量校验通过。

启动前完成工作区健康检查。head 上确认的 45 个 Codex 探测/失败代理做一次有界 TERM 清理，全部退出；后续新探测记录身份，不重复清理。内存、调度器和存储正常，自有文件读写/fsync约 10 ms。GPU、端口、IPC 和节点检查均在分配内执行，未清理其他作业。

四份不同二进制哈希与真实 P→D probe 通过。probe 先访问 D0，未完全对称预热所有批次形状；三轮和反转顺序减少顺序影响，不代表消除所有热身影响或统计显著性。

### 完整指标的统计口径

下方两个场景各直接列出此前要求的全部 30 项指标。每列都是该配置三轮实验中，**同一指标的中位数**，不是把三轮任务混合后重新计算分位数。验收仍使用后面的逐轮配对结果。

- 名称含 seconds 的指标单位是秒；吞吐单位是请求/秒或 token/秒；两项 hit_rate 以百分比展示；其他计数单位是任务、请求或 token。计数行也是单轮中位数，三轮完成量合计另列。
- 一项任务是一个做完四轮请求的 Program；任务耗时包含它等待和完成全部四轮的时间。latency 和 TTFT 是单次成功请求的客户端测量，包含 Router 等待；TTFT 从提交计时到首个内容输出。均值和分位数只统计成功任务或成功请求，分位数采用线性插值。
- 吞吐按成功请求数或成功请求的 usage token 总数除以整批 run_wall_time_seconds 计算。输入 token 按客户端请求统计一次，不把 P 与 D 的记录重复相加。
- prefix_cache_hit_rate 是 P 的命中 token 数/查询 token 数；prompt_token_hit_rate 是 P 缓存 prompt token 数/客户端 usage 输入 token 数。当前部署 P 查询统计每请求少一个边界 token，因此两者接近但不完全相等。D 收到的迁移 KV 不计作 P 的缓存收益。
- 四项 vLLM 时间使用 P、D0、D1 对应 histogram 的增量总和，除以成功客户端请求数，表示一次客户端请求经过 P 和所选 D 的引擎内时间。它们不含 Router 排队，inference 与 prefill/decode 不是额外可相加的独立阶段，不能直接拼成客户端总延迟。
- tasks_with_patch 指生成代码补丁的任务数。本实验没有实际改代码任务，所以填 N/A，表示不适用，不是 0，也不是使用修改后 Router 的任务数。

每轮原始指标与来源见[逐轮 CSV](pd_program_scheduling_validation_metrics.csv)和[指标 JSON](pd_program_scheduling_validation_metrics.json)；其他历史实验的完整表见[完整指标附录](pd_program_scheduling_validation_metrics.md)。

### 长输入、长输出

#### 四项验收指标的收益

| 指标 | main（三轮中位数） | cohort（三轮中位数） | 每轮配对降幅范围 |
|---|---:|---:|---:|
| mean_task_duration_seconds | 200.190002 s | 78.571640 s | 60.56%–62.50% |
| p50_task_duration_seconds | 200.029118 s | 91.215757 s | 54.35%–61.57% |
| p95_task_duration_seconds | 223.286335 s | 108.491810 s | 50.98%–54.21% |
| p99_task_duration_seconds | 223.745190 s | 111.808498 s | 49.80%–53.47% |

#### 全部 30 项指标对比

| 对比项 | main（n=3） | cohort（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 200.190002 | 78.571640 |
| p50_task_duration_seconds | 200.029118 | 91.215757 |
| p95_task_duration_seconds | 223.286335 | 108.491810 |
| p99_task_duration_seconds | 223.745190 | 111.808498 |
| run_wall_time_seconds | 223.870568 | 112.647919 |
| request_throughput_per_second | 0.285879 | 0.568142 |
| input_token_throughput_per_second | 2468.855134 | 4906.473241 |
| output_token_throughput_per_second | 36.592573 | 72.722160 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 68.8043% |
| prompt_token_hit_rate | 0.0000% | 68.7963% |
| latency_mean_seconds | 50.032436 | 19.627817 |
| latency_p50_seconds | 53.932225 | 9.412302 |
| latency_p95_seconds | 55.924154 | 73.891279 |
| latency_p99_seconds | 59.507421 | 86.910587 |
| ttft_mean_seconds | 44.266127 | 13.563895 |
| ttft_p50_seconds | 48.157605 | 3.356218 |
| ttft_p95_seconds | 50.236973 | 67.735814 |
| ttft_p99_seconds | 53.786866 | 80.897783 |
| vllm_queue_time_mean_seconds | 36.059381 | 3.275965 |
| vllm_prefill_time_mean_seconds | 7.663528 | 2.895620 |
| vllm_decode_time_mean_seconds | 5.773017 | 6.059712 |
| vllm_inference_time_mean_seconds | 13.432744 | 8.960017 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| cohort | 48 | 0 | 192 | 0 |

### 长输入、短输出

#### 四项验收指标的收益

| 指标 | main（三轮中位数） | cohort（三轮中位数） | 每轮配对降幅范围 |
|---|---:|---:|---:|
| mean_task_duration_seconds | 195.666392 s | 58.669136 s | 69.24%–70.07% |
| p50_task_duration_seconds | 195.545787 s | 70.633691 s | 62.99%–64.08% |
| p95_task_duration_seconds | 218.791427 s | 78.894581 s | 62.55%–64.05% |
| p99_task_duration_seconds | 219.094535 s | 80.688926 s | 62.15%–63.65% |

#### 全部 30 项指标对比

| 对比项 | main（n=3） | cohort（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 195.666392 | 58.669136 |
| p50_task_duration_seconds | 195.545787 | 70.633691 |
| p95_task_duration_seconds | 218.791427 | 78.894581 |
| p99_task_duration_seconds | 219.094535 | 80.688926 |
| run_wall_time_seconds | 219.180593 | 81.147541 |
| request_throughput_per_second | 0.291997 | 0.788687 |
| input_token_throughput_per_second | 2521.683114 | 6811.099787 |
| output_token_throughput_per_second | 9.343893 | 25.237980 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 67.3856% |
| prompt_token_hit_rate | 0.0000% | 67.3778% |
| latency_mean_seconds | 48.901534 | 14.652198 |
| latency_p50_seconds | 53.826586 | 6.821700 |
| latency_p95_seconds | 55.877476 | 55.567898 |
| latency_p99_seconds | 56.415735 | 67.486925 |
| ttft_mean_seconds | 47.528183 | 13.198510 |
| ttft_p50_seconds | 52.455261 | 5.402332 |
| ttft_p95_seconds | 54.504718 | 54.198486 |
| ttft_p99_seconds | 55.040475 | 66.088475 |
| vllm_queue_time_mean_seconds | 39.259585 | 4.268589 |
| vllm_prefill_time_mean_seconds | 7.659645 | 3.332245 |
| vllm_decode_time_mean_seconds | 1.376922 | 1.452540 |
| vllm_inference_time_mean_seconds | 9.035859 | 4.781511 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| cohort | 48 | 0 | 192 | 0 |

### 每轮四项验收

| 场景 | repeat | mean 降幅 | p50 降幅 | p95 降幅 | p99 降幅 | 四项均更好 |
|---|---:|---:|---:|---:|---:|---|
| pressure | 0 | 60.56% | 54.35% | 50.98% | 49.80% | 是 |
| pressure | 1 | 62.50% | 61.57% | 54.21% | 53.47% | 是 |
| pressure | 2 | 60.74% | 54.38% | 51.41% | 50.02% | 是 |
| prefill-heavy | 0 | 70.07% | 63.88% | 63.94% | 63.17% | 是 |
| prefill-heavy | 1 | 70.02% | 64.08% | 64.05% | 63.65% | 是 |
| prefill-heavy | 2 | 69.24% | 62.99% | 62.55% | 62.15% | 是 |

代表值是单次指标的三轮中位数，验收使用逐轮数据。两种场景三轮均通过。

### 结论与边界

当前源码配合 cohort 配置，满足原章 6 两种合成负载的四项任务时间目标。每个 D 上限从 1 提到 4，避免了旧 serial 中后排任务的 p95/p99 退化。

客户端并发 16 不等于每个引擎并发 16；这里最多 8 个活跃 Program，其余仍可能在 Router 等待。当前配置 light、更多节点、持续到达率和最大稳定吞吐没有补测，不能由一个并发点推出完整可扩展性结论。

上面的两张完整对比表已列出全部 30 项指标，历史数据仍保留在[完整指标附录](pd_program_scheduling_validation_metrics.md)和[逐轮 CSV](pd_program_scheduling_validation_metrics.csv)。四项任务时间之外的性能指标不是当前门槛。任务尾延迟更好，不自动证明请求 TTFT、TPOT 或业务质量更好。

其他性能指标仅观察：线性 TPOT p95 长输出逐轮增加约 4.2%–5.7%，短输出增加约 8.8%–11.7%；按旧 TPOT 门槛仍不能通过，但不影响用户明确的新目标。P/D preemption、NIXL transfer failure、notification failure、KV expired 增量均为 0。输出文本与配对 main 逐字相同的请求数为长输出 20/64、15/64、22/64，短输出 60/64、62/64、63/64，业务质量等价仍未验证。

这仍是固定语料四轮负载，工具间隔为 10/40 ms，没有实际执行工具或改代码。生成文本可能不同，业务质量等价未验证。KV event 订阅关闭，范围仍为 M1 单向 P→D，不代表完成 M2–M4。

[新目标结果 JSON](pd_program_task_duration_validation_results.json)包含每轮实测值、降幅、验收条件、源码/二进制/trace/harness 来源和配置。原始证据在工作区 gpu-4949；本报告第 1–13 节保留旧目标及失败诊断，不因目标变化而改写当时结论。

只重算新目标、不启动引擎：

    python3 scripts/pd_program_validation/analyze_task_duration.py /path/to/pd-program-validation/20261011/gpu-4949
