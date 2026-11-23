# Session 准入与 Rank 容量

适用入口是单个 Router 上的 `POST /v1/chat/completions`，后端属于同一个静态 HTTP 模型池。默认沿用 Router 原来的入口。设置环境变量 `VLLM_ROUTER_SESSION_CONFIG` 指向 JSON 文件即可启用。第一阶段只需要改 Router。第二阶段还要在每个独立寻址的单 DP Rank 上安装容量中间件。此实现不覆盖 PD、EPD、gRPC、动态发现和多 Router 间的会话轮转。

## 第一阶段怎样使用

下面的配置允许 32 个会话同时工作。某个会话已经开工时，后面还可以等 8 条。域内最多保留 256 条正在读入、等待或执行的请求，合计原始输入上限 64 MiB。正在读入正文的请求也占预算。部署示例选用原生 `--policy cache_aware`。

```json
{
  "active": 32,
  "active_per_subject": 8,
  "queued_per_session": 8,
  "resident_requests": 256,
  "resident_per_subject": 64,
  "resident_bytes": 67108864,
  "bytes_per_subject": 16777216,
  "sessions": 128,
  "sessions_per_subject": 32,
  "profiles": {"interactive": "cache_aware", "batch": "round_robin"},
  "rank_capacity": false
}
```

调用方在正文里传 `"session_params": {"session_id": "lin"}`。缺省时，Router 给这条请求分配临时会话。开启现有 Bearer 鉴权后，通过验证的凭据摘要作为主体；不配置鉴权时，整个池共用一个主体。可选的 `trusted_session_header` 只适合由接入层清除外部同名头并写入真实身份的部署。正文和头的会话 ID 冲突返回 400。

调用方可用 `x-router-profile: batch` 选择服务端预设的策略。两个 profile 共用同一个队列，同一会话切换 profile 也要排在自己的上一条之后。缺省走模型的原生策略。常驻 KV 事件订阅由主策略 `--policy kv_aware` 建立；在这类进程里也可以配置 `cache_aware` 和 `round_robin` 作为额外 profile。仅启用一阶段时还支持额外的 random、consistent_hash 和 rendezvous_hash。缓存前缀和原生负载在放行后参与选点。队满返回 429，响应头发出前达到整条请求的期限返回 504。SSE 的本地许可持续到流结束、客户端关闭或期限到达。

## 第二阶段怎样使用

将 `rank_capacity` 设为 `true`。主策略可选 cache_aware、kv_aware 或 round_robin。Router 与各 Rank 设置同一个私有 `VLLM_SESSION_RANK_KEY`。每台 worker 运行一个独占引擎和一个 API 进程，使用 vLLM 的 `--middleware rank_authority.RankAuthority`，把 `py_src/vllm_router` 加入该进程的 `PYTHONPATH`。容量承诺依赖单一入口，运行环境需隔离对引擎的直接调用。实验采用普通自回归、单模型、固定块大小的全注意力模型。一轮的容量涵盖历史、模板、本轮输出上限和候选条数。跨工具调用的未来轮次重新申请。

| Rank 环境变量 | 单位与含义 |
|---|---|
| `VLLM_SESSION_BLOCK_SIZE` | 每个 GPU KV block 的 token 数，与引擎一致 |
| `VLLM_SESSION_RANK_BLOCKS` | 分给这条入口的 block 数，可小于物理容量 |
| `VLLM_SESSION_MODEL_LEN` | 单序列可执行上下文的保守上限 |
| `VLLM_SESSION_RANK_JOURNAL` | 这个 Rank 独有、可跨进程重启保留的 SQLite 文件，须支持排他文件锁和 SQLite WAL |

中间件首次报价时，从同一个引擎读取 `/metrics` 中的块大小、GPU 块总数和 `/v1/models` 中的上下文长度。缺少证据、超配或多引擎指标都不能提供可用报价。纯文本且有输出上限时，它调用同一引擎的 chat render 计算输入长度；没有精确输入时按完整上下文预算。报价不预扣容量，不因预计命中缓存而少算。多个候选输出按条数计费。当前不支持 beam search 和扩展 best-of 采样。 如同一引擎在渲染时返回 400 或 422，该 Rank 会报为无法承接，Router 不会为这条无效输入预留块。

| 内部接口 | 负责的动作 |
|---|---|
| `POST /_session/quote` | 返回本代际总额、余量、所需块数和有时效的签名报价 |
| `POST /_session/lease` | 按尝试号、目标代际和请求摘要原子扣款；原样重复的未兑换认领只扣一次 |
| `GET /_session/wait` | 比对报价版本，长轮询等待目标余量变化 |
| `POST /v1/chat/completions` | 目标 Rank 核对凭证、代际及准确请求体，成功兑换一次后进入推理 |
| `DELETE /_session/lease/{id}` | 仅能撤销尚未兑换的凭证 |

R0 还有 8 块，R1 还有 16 块，一条需要 12 块的请求只能拿到 R1 的凭证。第二个需要 12 块的请求会交回会话活跃名额，在自己会话的队首等容量。这时一个只需要 3 块的会话仍能去 R0。请求在所有 Rank 的静态预算上都放不下时返回 400；没有可信 Rank 时返回 503。

Router 本地断连后，Rank 继续读引擎的有界输出。单次 JSON 收到全部候选的完成原因，或者 SSE 收到全部完成原因及正常结束标记，才结清已兑换的工作。已发送但结果未知的请求不会自动重发。可以证明尚未发送的故障可在原有期限内重新选点。若引擎异常退出且无终止证据，已兑换的占用在重启后仍记账，可能降低可用容量。退役这一代际的引擎并确认其不再使用 GPU 资源后，才有依据离线处置这份旧账。

## 如何复现

```bash
cargo build --locked --bin vllm-router
cargo test --locked --lib --features cpu,ready session_admission
python tests/session_admission/test_authority.py
python tests/session_admission/acceptance.py --router "$PWD/target/debug/vllm-router" --output /tmp/session-check-new
```

本地 HTTP 实验依赖 Starlette 和 Uvicorn，输出目录须为新路径。它启动实际 Rust Router 及两个可控 HTTP 后端，记录策略选点计数、后端报告的前缀复用、每条请求的发出时刻、容量争抢、超时和断连。这里的缓存计数来自测试后端。真实 GPU 的计数由 vLLM 的 `usage.prompt_tokens_details.cached_tokens` 提供。

H20 驱动为 `python tests/session_admission/h20.py --output /tmp/h20-session-new`。它通过系统 SSH 访问 `vllm-h20-head`，检查已有 `/home/david_cwq/ltw`，在其下创建唯一私有目录，提交一个两卡 SLURM 作业并收回证据。Cargo 依赖在工作站按锁文件打包，构建、运行、GPU 检查都在分配内执行。新加坡 H20e 上使用 vLLM 0.28.0 和 Qwen3-0.6B。启动前需确认模型目录包含权重分片，`SESSION_H20_MODEL` 可指定计算节点可读的完整模型目录。vLLM 进程通过作业专属的 Linux 抽象 Unix socket 通信。源码、编译产物、模型运行缓存、账本和日志写入实验目录。原生缓存对照、开启准入及开启容量分别启动独立引擎；每次启动前保存健康快照。

[H20e 双卡实机验收记录](H20_VALIDATION_ZH.md)
